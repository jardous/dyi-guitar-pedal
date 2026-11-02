#!/usr/bin/env python3
"""Three-button GPIO-to-MIDI controller; importing this module acquires no hardware."""

from contextlib import ExitStack
from dataclasses import dataclass
from functools import partial
import logging
from pathlib import Path
from queue import Empty, Full, Queue
import signal
import threading
import time


LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class ButtonMapping:
    gpio: int
    short_note: int
    long_note: int


@dataclass(frozen=True)
class Settings:
    buttons: tuple = (
        ButtonMapping(27, 10, 20),
        ButtonMapping(26, 30, 40),
        ButtonMapping(17, 50, 60),
    )
    target_port: str = "PiPedal:in"
    hold_threshold: float = 1.0
    bounce_time: float = 0.05
    channel: int = 0  # MIDI channel 1, using Mido's zero-based numbering.
    note_duration: float = 0.05
    queue_size: int = 8
    max_action_age: float = 0.25
    poll_interval: float = 1.0
    retry_initial: float = 0.5
    retry_max: float = 10.0

    def __post_init__(self):
        for name in (
            "hold_threshold", "note_duration", "queue_size", "max_action_age",
            "poll_interval", "retry_initial", "retry_max",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(name + " must be positive")
        if self.bounce_time < 0 or not 0 <= self.channel <= 15:
            raise ValueError("Invalid debounce time or MIDI channel")
        if len({mapping.gpio for mapping in self.buttons}) != len(self.buttons):
            raise ValueError("Each button must use a different GPIO")
        for mapping in self.buttons:
            if not all(0 <= note <= 127 for note in (mapping.short_note, mapping.long_note)):
                raise ValueError("MIDI notes must be between 0 and 127")


class PressState:
    """Require a fresh press, consume each release once, and ignore wall time."""

    def __init__(self, mapping, threshold, clock=time.monotonic):
        self.mapping = mapping
        self.threshold = threshold
        self.clock = clock
        self.started = None
        self.lock = threading.Lock()

    def press(self):
        with self.lock:
            if self.started is None:
                self.started = self.clock()

    def release(self):
        with self.lock:
            if self.started is None:
                return None
            elapsed = self.clock() - self.started
            self.started = None
        if elapsed < self.threshold:
            return self.mapping.short_note
        return self.mapping.long_note


@dataclass(frozen=True)
class Action:
    note: int
    created: float
    generation: int


class MidiWorker(threading.Thread):
    """Own all MIDI I/O; callbacks only submit bounded, expiring actions."""

    def __init__(self, midi, settings, stop, clock=time.monotonic):
        super().__init__(name="midi-worker", daemon=True)
        self.midi = midi
        self.settings = settings
        self.stop = stop
        self.clock = clock
        self.actions = Queue(maxsize=settings.queue_size)
        self.state_lock = threading.Lock()
        self.connected = False
        self.generation = 0
        self.dropped = 0
        self.port = None
        self.port_name = None
        self.active_note = None
        self.status = None
        self.error = None

    def submit(self, note):
        # Never wait for MIDI, discovery, or a full queue in a GPIO callback.
        with self.state_lock:
            if self.stop.is_set() or not self.connected:
                self.dropped += 1
                return False
            action = Action(note, self.clock(), self.generation)
            try:
                self.actions.put_nowait(action)
            except Full:
                self.dropped += 1
                return False
        return True

    def _status(self, message, level=logging.INFO):
        if message != self.status:
            LOG.log(level, "%s", message)
            self.status = message

    def _close_port(self):
        port, self.port = self.port, None
        self.port_name = None
        note, self.active_note = self.active_note, None
        if port is None:
            return
        if note is not None:
            try:
                port.send(self.midi.Message(
                    "note_off", channel=self.settings.channel, note=note, velocity=0
                ))
            except Exception:
                # Backend exception types vary; cleanup must still close the port.
                LOG.warning("Could not send final note-off for %s", note, exc_info=True)
        try:
            port.close()
        except Exception:
            LOG.warning("Could not close MIDI port", exc_info=True)

    def _disconnect(self, reason, level=logging.WARNING):
        with self.state_lock:
            self.connected = False
            self.generation += 1
            while True:
                try:
                    self.actions.get_nowait()
                    self.dropped += 1
                except Empty:
                    break
        self._close_port()
        self._status(reason, level)

    def _reconcile(self, refresh=False):
        # Catch backend errors at the I/O boundary, including RtMidi RuntimeError
        # and SystemError, rather than assuming all failures are OSError.
        try:
            matches = sorted(set(
                name for name in self.midi.get_output_names()
                if self.settings.target_port in name
            ))
            if len(matches) != 1:
                reason = ("Waiting for MIDI destination " + repr(self.settings.target_port)
                          if not matches else "Ambiguous MIDI destinations: " + repr(matches))
                self._disconnect(reason)
                return False
            name = matches[0]
            if self.port is not None and (
                name != self.port_name or getattr(self.port, "closed", False)
            ):
                self._disconnect("MIDI destination changed or port closed")
            if refresh or self.port is None:
                # Rebuild the ALSA subscription before every action. A restart
                # can reuse the same name/address without a send exception.
                self._close_port()
                self.port = self.midi.open_output(name)
                self.port_name = name
            with self.state_lock:
                self.connected = True
            self._status("MIDI connected to " + repr(name))
            return True
        except Exception as error:
            self._disconnect("MIDI connection failed: " + str(error))
            return False

    def _process(self, action):
        if self.stop.is_set() or self.clock() - action.created > self.settings.max_action_age:
            with self.state_lock:
                self.dropped += 1
            return
        if not self._reconcile(refresh=True):
            with self.state_lock:
                self.dropped += 1
            return
        # Discovery/opening may take time or reveal a new connection generation.
        with self.state_lock:
            expired = self.clock() - action.created > self.settings.max_action_age
            if self.stop.is_set() or expired or action.generation != self.generation:
                self.dropped += 1
                return
        on = self.midi.Message(
            "note_on", channel=self.settings.channel, note=action.note, velocity=127
        )
        off = self.midi.Message(
            "note_off", channel=self.settings.channel, note=action.note, velocity=0
        )
        try:
            self.active_note = action.note
            self.port.send(on)
            # SIGTERM interrupts the pulse delay, but still gets its note-off.
            self.stop.wait(self.settings.note_duration)
            self.port.send(off)
            self.active_note = None
            LOG.debug("Sent MIDI note %s", action.note)
        except Exception as error:
            self._disconnect("MIDI send failed: " + str(error))

    def run(self):
        next_check = self.clock()
        retry = self.settings.retry_initial
        try:
            while not self.stop.is_set():
                if self.clock() >= next_check:
                    if self._reconcile():
                        retry = self.settings.retry_initial
                        next_check = self.clock() + self.settings.poll_interval
                    else:
                        next_check = self.clock() + retry
                        retry = min(retry * 2, self.settings.retry_max)
                    with self.state_lock:
                        dropped, self.dropped = self.dropped, 0
                    if dropped:
                        LOG.warning("Dropped %s disconnected, stale, or overflow actions", dropped)
                timeout = min(0.2, max(0.0, next_check - self.clock()))
                try:
                    action = self.actions.get(timeout=timeout)
                except Empty:
                    continue
                self._process(action)
                if not self.connected:
                    next_check = self.clock() + retry
                    retry = min(retry * 2, self.settings.retry_max)
        except Exception as error:
            self.error = error
            LOG.exception("MIDI worker failed")
            self.stop.set()
        finally:
            self._disconnect("MIDI worker stopped", logging.INFO)


class ButtonHandlers:
    """Contain callback failures so a dead GPIO thread is not silently ignored."""

    def __init__(self, worker, stop):
        self.worker = worker
        self.stop = stop
        self.errors = Queue(maxsize=1)

    def handle(self, state, pressed):
        if self.stop.is_set():
            return
        try:
            if pressed:
                state.press()
            else:
                note = state.release()
                if note is not None:
                    self.worker.submit(note)
        except Exception as error:
            try:
                self.errors.put_nowait(error)
            except Full:
                pass
            self.stop.set()


def run_controller(midi, button_factory, settings=Settings()):
    stop = threading.Event()
    worker = MidiWorker(midi, settings, stop)
    handlers = ButtonHandlers(worker, stop)
    old_signals = {}
    buttons = []
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            old_signals[signum] = signal.signal(signum, lambda *_: stop.set())
        with ExitStack() as resources:
            for mapping in settings.buttons:
                button = button_factory(
                    mapping.gpio, pull_up=True, bounce_time=settings.bounce_time
                )
                resources.callback(button.close)
                buttons.append(button)
                state = PressState(mapping, settings.hold_threshold)
                button.when_pressed = partial(handlers.handle, state, True)
                button.when_released = partial(handlers.handle, state, False)
            LOG.info("GPIO controller running; waiting for MIDI connection")
            worker.start()
            try:
                while not stop.wait(0.2):
                    if not worker.is_alive():
                        raise RuntimeError("MIDI worker exited unexpectedly")
            finally:
                # Disable callbacks before closing GPIOs and waiting for MIDI.
                stop.set()
                for button in buttons:
                    button.when_pressed = None
                    button.when_released = None
    finally:
        stop.set()
        if worker.ident is not None:
            worker.join(timeout=5.0)
        for signum, handler in old_signals.items():
            signal.signal(signum, handler)
        if worker.is_alive():
            raise RuntimeError("MIDI worker did not stop within 5 seconds")
        LOG.info("Controller stopped")
    if not handlers.errors.empty():
        raise RuntimeError("GPIO callback failed") from handlers.errors.get_nowait()
    if worker.error is not None:
        raise RuntimeError("MIDI worker failed") from worker.error


def discover_header_chip(lgpio, paths):
    """Find the header controller by label, never by boot-dependent chip number."""
    labels = {"pinctrl-rp1", "pinctrl-bcm2711", "pinctrl-bcm2835"}
    matches = []
    for path in paths:
        chip = int(path.name.removeprefix("gpiochip"))
        try:
            handle = lgpio.gpiochip_open(chip)
        except Exception:
            LOG.debug("Cannot inspect %s", path, exc_info=True)
            continue
        try:
            if lgpio.gpio_get_chip_info(handle)[3] in labels:
                matches.append(chip)
        finally:
            lgpio.gpiochip_close(handle)
    if len(matches) != 1:
        raise RuntimeError(
            "Expected one accessible Raspberry Pi header GPIO chip, found "
            + repr(matches) + "; check /dev/gpiochip* permissions and board support"
        )
    return matches[0]


def make_pin_factory():
    import lgpio
    from gpiozero.pins.lgpio import LGPIOFactory, LGPIOPin
    from gpiozero.pins.pi import PiFactory

    chip = discover_header_chip(lgpio, sorted(Path("/dev").glob("gpiochip*")))

    class HeaderFactory(LGPIOFactory):
        def __init__(self):
            # gpiozero 2.0.1 may ignore LGPIOFactory(chip=...) and force 0/4.
            # Initialize its existing lgpio backend with the discovered chip;
            # GPIO numbering, debounce, callbacks, and cleanup stay inherited.
            PiFactory.__init__(self)
            self._handle = None
            self._chip = chip
            self.pin_class = LGPIOPin
            self._handle = lgpio.gpiochip_open(chip)

    factory = HeaderFactory()
    LOG.info("Using header GPIO controller /dev/gpiochip%s", chip)
    return factory


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(threadName)s %(message)s"
    )
    try:
        # Lazy imports keep unit tests and imports independent of Pi hardware.
        import mido
        from gpiozero import Button

        factory = make_pin_factory()
        try:
            run_controller(mido, partial(Button, pin_factory=factory))
        finally:
            factory.close()
    except Exception:
        LOG.exception("Controller failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
