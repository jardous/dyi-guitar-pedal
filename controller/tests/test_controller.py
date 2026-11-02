"""Hardware-free regressions: python3 -m unittest discover -s tests -v."""

from dataclasses import replace
from pathlib import Path
import signal
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import controller


PORT = "PiPedal:PiPedal:in 128:0"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakePort:
    def __init__(self, midi):
        self.midi = midi
        self.closed = False
        self.messages = []
        self.failures = {}
        self.close_error = None

    def send(self, message):
        if self.failures.get(message.type, 0):
            self.failures[message.type] -= 1
            raise RuntimeError("send failed")
        self.messages.append(message)
        self.midi.sent.append(message)
        if self.midi.on_send:
            self.midi.on_send(message)

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class FakeMidi:
    def __init__(self):
        self.names = [PORT]
        self.names_error = None
        self.open_error = None
        self.ports = []
        self.sent = []
        self.on_open = None
        self.on_send = None
        self.opened = threading.Event()

    def get_output_names(self):
        if self.names_error:
            raise self.names_error
        return self.names

    def open_output(self, name):
        if self.open_error:
            raise self.open_error
        port = FakePort(self)
        self.ports.append(port)
        if self.on_open:
            self.on_open(port)
        self.opened.set()
        return port

    def Message(self, kind, **kwargs):
        return SimpleNamespace(type=kind, **kwargs)


class PressTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.state = controller.PressState(
            controller.ButtonMapping(27, 10, 20), 1.0, self.clock
        )

    def test_unmatched_startup_release_is_ignored(self):
        self.assertIsNone(self.state.release())
        self.assertIsNone(self.state.release())

    def test_short_press(self):
        self.state.press()
        self.clock.now += 0.2
        self.assertEqual(self.state.release(), 10)

    def test_long_press(self):
        self.state.press()
        self.clock.now += 2.0
        self.assertEqual(self.state.release(), 20)

    def test_exact_threshold_is_long(self):
        self.state.press()
        self.clock.now += 1.0
        self.assertEqual(self.state.release(), 20)

    def test_just_below_threshold_is_short(self):
        self.state.press()
        self.clock.now += 0.999
        self.assertEqual(self.state.release(), 10)

    def test_release_consumes_press(self):
        self.state.press()
        self.assertEqual(self.state.release(), 10)
        self.assertIsNone(self.state.release())

    def test_duplicate_press_does_not_reset_timer(self):
        self.state.press()
        self.clock.now += 0.9
        self.state.press()
        self.clock.now += 0.2
        self.assertEqual(self.state.release(), 20)

    def test_overlapping_buttons_are_independent(self):
        other = controller.PressState(controller.ButtonMapping(26, 30, 40), 1, self.clock)
        self.state.press()
        self.clock.now += 0.9
        other.press()
        self.clock.now += 0.2
        self.assertEqual(other.release(), 30)
        self.assertEqual(self.state.release(), 20)

    def test_wall_clock_changes_do_not_affect_duration(self):
        for wall_time in (0, 99999999999):
            with self.subTest(wall_time=wall_time), patch("time.time", return_value=wall_time):
                self.state.press()
                self.clock.now += 0.2
                self.assertEqual(self.state.release(), 10)

    def test_default_clock_is_monotonic(self):
        state = controller.PressState(controller.ButtonMapping(27, 10, 20), 1)
        self.assertIs(state.clock, controller.time.monotonic)


class MidiTests(unittest.TestCase):
    def setUp(self):
        log = patch.object(controller, "LOG")
        log.start()
        self.addCleanup(log.stop)
        self.clock = Clock()
        self.midi = FakeMidi()
        self.stop = threading.Event()
        self.settings = replace(controller.Settings(), note_duration=0.001)
        self.worker = controller.MidiWorker(self.midi, self.settings, self.stop, self.clock)
        self.addCleanup(self.worker._disconnect, "Test cleanup")

    def action(self, note=10):
        self.assertTrue(self.worker.submit(note))
        return self.worker.actions.get_nowait()

    def connect(self):
        self.assertTrue(self.worker._reconcile())

    def test_missing_destination_recovers_and_does_not_replay(self):
        self.midi.names = []
        self.assertFalse(self.worker._reconcile())
        self.assertFalse(self.worker.submit(10))
        self.midi.names = [PORT]
        self.connect()
        self.assertTrue(self.worker.actions.empty())
        self.assertEqual(self.midi.sent, [])

    def test_ambiguous_destinations_are_not_chosen_arbitrarily(self):
        self.midi.names.append("PiPedal:PiPedal:in 130:0")
        self.assertFalse(self.worker._reconcile())
        self.assertEqual(self.midi.ports, [])

    def test_discovery_exception_types_are_recoverable(self):
        for error in (OSError("discovery"), RuntimeError("discovery"), SystemError("discovery")):
            with self.subTest(error=error):
                self.midi.names_error = error
                self.assertFalse(self.worker._reconcile())
                self.assertFalse(self.worker.connected)
        self.midi.names_error = None
        self.connect()

    def test_open_failure_and_recovery(self):
        self.midi.open_error = RuntimeError("open")
        self.assertFalse(self.worker._reconcile())
        self.midi.open_error = None
        self.connect()

    def test_pulse_has_correct_channel_note_and_velocity(self):
        self.connect()
        self.worker._process(self.action(20))
        self.assertEqual(
            [(m.type, m.channel, m.note, m.velocity) for m in self.midi.sent],
            [("note_on", 0, 20, 127), ("note_off", 0, 20, 0)],
        )

    def test_same_name_port_is_refreshed_before_each_action(self):
        self.connect()
        first = self.worker.port
        self.worker._process(self.action())
        second = self.worker.port
        self.worker._process(self.action(30))
        self.assertTrue(first.closed)
        self.assertTrue(second.closed)
        self.assertEqual(len(self.midi.ports), 3)
        self.assertEqual(len(self.midi.sent), 4)

    def test_destination_disappears_and_returns_at_new_address(self):
        self.connect()
        first = self.worker.port
        action = self.action()
        self.midi.names = []
        self.worker._process(action)
        self.assertTrue(first.closed)
        self.assertFalse(self.worker.connected)
        self.midi.names = ["PiPedal:PiPedal:in 131:0"]
        self.connect()
        self.assertEqual(self.midi.sent, [])
        self.worker._process(self.action())
        self.assertEqual(len(self.midi.sent), 2)

    def test_destination_identity_change_discards_pending_action(self):
        self.connect()
        action = self.action()
        self.midi.names = ["PiPedal:PiPedal:in 131:0"]
        self.worker._process(action)
        self.assertEqual(self.midi.sent, [])
        self.assertTrue(self.worker.connected)

    def test_closed_port_invalidates_pending_action(self):
        self.connect()
        action = self.action()
        self.worker.port.closed = True
        self.worker._process(action)
        self.assertEqual(self.midi.sent, [])

    def test_disconnect_purges_queue_and_invalidates_in_flight_action(self):
        self.connect()
        action = self.action()
        self.assertTrue(self.worker.submit(30))
        self.worker._disconnect("outage")
        self.assertTrue(self.worker.actions.empty())
        self.connect()
        self.worker._process(action)
        self.assertEqual(self.midi.sent, [])

    def test_queue_is_bounded_and_drops_newest_without_blocking(self):
        self.connect()
        for _ in range(self.settings.queue_size):
            self.assertTrue(self.worker.submit(10))
        self.assertFalse(self.worker.submit(20))
        self.assertEqual(self.worker.actions.qsize(), self.settings.queue_size)

    def test_expired_action_is_not_sent(self):
        self.connect()
        action = self.action()
        self.clock.now += self.settings.max_action_age + 0.001
        self.worker._process(action)
        self.assertEqual(self.midi.sent, [])

    def test_action_expiring_during_open_is_not_sent(self):
        self.connect()
        action = self.action()
        self.midi.on_open = lambda port: setattr(self.clock, "now", self.clock.now + 1)
        self.worker._process(action)
        self.assertEqual(self.midi.sent, [])

    def test_note_on_failure_attempts_note_off_and_disconnects(self):
        self.connect()
        self.midi.on_open = lambda port: port.failures.update(note_on=1)
        self.worker._process(self.action())
        self.assertFalse(self.worker.connected)
        self.assertTrue(self.midi.ports[-1].closed)
        self.assertEqual([m.type for m in self.midi.sent], ["note_off"])

    def test_note_off_failure_retries_cleanup_and_disconnects(self):
        self.connect()
        self.midi.on_open = lambda port: port.failures.update(note_off=1)
        self.worker._process(self.action())
        self.assertFalse(self.worker.connected)
        self.assertEqual([m.type for m in self.midi.sent], ["note_on", "note_off"])

    def test_failed_cleanup_still_closes_port(self):
        self.connect()
        port = self.worker.port
        self.worker.active_note = 10
        port.failures["note_off"] = 10
        port.close_error = RuntimeError("close")
        self.worker._disconnect("outage")
        self.assertTrue(port.closed)
        self.assertIsNone(self.worker.port)

    def test_shutdown_during_pulse_sends_note_off(self):
        self.connect()
        self.midi.on_send = lambda message: self.stop.set()
        self.worker._process(self.action())
        self.assertEqual([m.type for m in self.midi.sent], ["note_on", "note_off"])
        self.assertFalse(self.worker.submit(30))

    def test_real_worker_thread_stops_and_closes(self):
        midi = FakeMidi()
        stop = threading.Event()
        worker = controller.MidiWorker(midi, self.settings, stop)
        worker.start()
        try:
            self.assertTrue(midi.opened.wait(2))
        finally:
            stop.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(worker.error)
        self.assertTrue(all(port.closed for port in midi.ports))

    def test_unexpected_worker_failure_stops_controller(self):
        midi = FakeMidi()
        midi.Message = Mock(side_effect=ValueError("unexpected message construction error"))
        stop = threading.Event()
        worker = controller.MidiWorker(midi, self.settings, stop)
        worker.start()
        try:
            self.assertTrue(midi.opened.wait(2))
            # Inject directly so the test doesn't race the connected flag, which
            # is set just after open_output returns.
            worker.actions.put(controller.Action(10, worker.clock(), worker.generation))
            self.assertTrue(stop.wait(2))
        finally:
            stop.set()
            worker.join(2)
        self.assertIsInstance(worker.error, ValueError)
        self.assertTrue(all(port.closed for port in midi.ports))

    def test_missing_destination_uses_exponential_backoff(self):
        from queue import Empty

        calls = []
        self.midi.names = []
        self.clock.now = 0
        original_names = self.midi.get_output_names
        def names():
            calls.append(self.clock.now)
            return original_names()
        def get(timeout):
            self.clock.now += timeout
            if self.clock.now >= 3.6:
                self.stop.set()
            raise Empty
        def get_nowait():
            raise Empty
        self.midi.get_output_names = names
        self.worker.actions = SimpleNamespace(get=get, get_nowait=get_nowait)
        self.worker.run()
        self.assertEqual(len(calls), 4)
        for actual, expected in zip(calls, (0, 0.5, 1.5, 3.5)):
            self.assertAlmostEqual(actual, expected)


class LifecycleTests(unittest.TestCase):
    def test_real_sigint_and_sigterm_exit_cleanly(self):
        script = """
import os
import signal
import sys
from unittest.mock import Mock
sys.path.insert(0, 'tests')
from test_controller import FakeMidi
import controller
buttons = []
def factory(*args, **kwargs):
    button = Mock()
    buttons.append(button)
    return button
midi = FakeMidi()
midi.on_open = lambda port: os.kill(os.getpid(), int(sys.argv[1]))
controller.run_controller(midi, factory)
assert len(buttons) == 2
for button in buttons:
    button.close.assert_called_once()
    assert button.when_pressed is None
    assert button.when_released is None
assert midi.ports and all(port.closed for port in midi.ports)
print('clean shutdown')
"""
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signum=signum):
                result = subprocess.run(
                    [sys.executable, "-c", script, str(int(signum))],
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("clean shutdown", result.stdout)

    def test_callback_only_enqueues_on_matched_release(self):
        worker = Mock()
        handlers = controller.ButtonHandlers(worker, threading.Event())
        state = controller.PressState(controller.ButtonMapping(27, 10, 20), 1)
        handlers.handle(state, False)
        worker.submit.assert_not_called()
        handlers.handle(state, True)
        worker.submit.assert_not_called()
        handlers.handle(state, False)
        worker.submit.assert_called_once_with(10)

    def test_callback_failure_is_visible_and_requests_shutdown(self):
        stop = threading.Event()
        handlers = controller.ButtonHandlers(Mock(), stop)
        state = Mock()
        state.press.side_effect = RuntimeError("GPIO callback")
        handlers.handle(state, True)
        self.assertTrue(stop.is_set())
        self.assertIsInstance(handlers.errors.get_nowait(), RuntimeError)

    def test_shutdown_disables_callbacks(self):
        stop = threading.Event()
        stop.set()
        worker, state = Mock(), Mock()
        handlers = controller.ButtonHandlers(worker, stop)
        handlers.handle(state, True)
        handlers.handle(state, False)
        state.press.assert_not_called()
        state.release.assert_not_called()
        worker.submit.assert_not_called()

    def test_partial_gpio_setup_failure_closes_created_buttons(self):
        first = Mock()
        factory = Mock(side_effect=[first, RuntimeError("second GPIO")])
        with patch.object(controller.signal, "signal", return_value=signal.SIG_DFL) as signals:
            with self.assertRaisesRegex(RuntimeError, "second GPIO"):
                controller.run_controller(FakeMidi(), factory)
        first.close.assert_called_once()
        self.assertEqual(signals.call_count, 4)

    def test_sigterm_closes_gpio_and_midi_and_restores_handlers(self):
        installed = {}
        def register(signum, handler):
            previous = installed.get(signum, signal.SIG_DFL)
            installed[signum] = handler
            return previous
        buttons = []
        def button_factory(*args, **kwargs):
            button = Mock()
            buttons.append(button)
            return button
        midi = FakeMidi()
        midi.on_open = lambda port: installed[signal.SIGTERM](signal.SIGTERM, None)
        with patch.object(controller.signal, "signal", side_effect=register):
            controller.run_controller(midi, button_factory)
        for button in buttons:
            button.close.assert_called_once()
            self.assertIsNone(button.when_pressed)
            self.assertIsNone(button.when_released)
        self.assertTrue(all(port.closed for port in midi.ports))
        self.assertEqual(installed[signal.SIGINT], signal.SIG_DFL)
        self.assertEqual(installed[signal.SIGTERM], signal.SIG_DFL)

    def test_callback_failure_reaches_main_and_closes_resources(self):
        buttons = []
        def factory(*args, **kwargs):
            button = Mock()
            buttons.append(button)
            return button
        midi = FakeMidi()
        midi.on_open = lambda port: buttons[0].when_pressed()
        with patch.object(controller.signal, "signal", return_value=signal.SIG_DFL):
            with patch.object(controller.PressState, "press", side_effect=ValueError("callback")):
                with self.assertRaisesRegex(RuntimeError, "GPIO callback failed"):
                    controller.run_controller(midi, factory)
        self.assertTrue(all(port.closed for port in midi.ports))
        for button in buttons:
            button.close.assert_called_once()

    def test_worker_failure_reaches_main(self):
        def fail(worker):
            worker.error = ValueError("worker")
            worker.stop.set()
        with patch.object(controller.signal, "signal", return_value=signal.SIG_DFL):
            with patch.object(controller.MidiWorker, "run", fail):
                with self.assertRaisesRegex(RuntimeError, "MIDI worker failed"):
                    controller.run_controller(FakeMidi(), Mock(return_value=Mock()))

    def test_invalid_configuration_is_rejected(self):
        for setting in (dict(queue_size=0), dict(channel=16), dict(poll_interval=0)):
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                controller.Settings(**setting)


if __name__ == "__main__":
    unittest.main()
