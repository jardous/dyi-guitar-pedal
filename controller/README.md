# PiPedal button controller

A GPIO-to-MIDI controller for a Raspberry Pi Compute Module 5 running
[PiPedal](https://github.com/rerdavies/pipedal), with two enabled buttons and an
optional third-button wiring plan.

`controller.py` replaces the former `controller-daemon.py` / `controller-gemini.py`
with guarded monotonic press timing, a dedicated MIDI worker, connection recovery,
and explicit shutdown handling. GPIOs, MIDI notes, and release-triggered short/long
actions are unchanged.

## Hardware and button mappings

The script uses **BCM GPIO numbers**, not physical connector pin numbers. Each
button is configured with an internal pull-up and is active when connected to
ground. Verify the GPIO routing on your CM5 carrier board before wiring; do not
apply 5 V to a GPIO input.

| Button | BCM GPIO | Physical signal pin | Physical ground pin (shared) | Short press (< 1 second) | Long press (>= 1 second) | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 27 | **13** | **9 or 39** | Note 10 | Note 20 | Enabled |
| 2 | 26 | **37** | **9 or 39** | Note 30 | Note 40 | Enabled |
| 3 | 17 | **11** | **9 or 39** | Note 50 (proposed) | Note 60 (proposed) | Optional; not enabled |

The third input is a proposal: first verify that GPIO17 is unused by your carrier
and accessories. To enable it, add `ButtonMapping(17, 50, 60)` to `Settings.buttons`.
This documentation update does not change the script's two-button configuration.
Each button only sends MIDI notes; downstream action assignments are outside the
controller's scope.

For the circuit schematic using **one resistor and one capacitor per button**,
component values, and wiring notes, see [hardware debounce](docs/hardware-debounce.md).
The original pedal uses **104 / 100 nF capacitors** from physical pins **13 to 9**
and **37 to 39**, with a switch and series resistor in parallel with each capacitor.
The series resistors are **1 kOhm**, confirmed by the pedal's owner. Both inputs
match the script: GPIO27 / pin 13 and GPIO26 / pin 37. Pins 9 and 39 are ground.
Either ground pin can serve all buttons; keep the GPIO signal nodes separate.
The optional third filter connects between pin **11** and the same common ground.
Use the same 100 nF capacitor and a 1 kOhm series switch resistor; **820 Ohm** is
also an acceptable alternative with the internal pull-up. Keep software debounce enabled.

- Actions fire on **release**, including long-press actions.
- Software debounce is set to 50 ms for each button.
- Messages use MIDI channel 1 (`mido`'s default, zero-based channel 0).
- Each action sends note-on at velocity 127, followed by note-off at velocity 0
  after a 50 ms delay.
- Configure PiPedal's MIDI bindings to associate these notes with the desired
  actions. The script does not itself select presets or configure PiPedal.

## Dependencies

The controller requires Python 3, `gpiozero`, `mido`, `python-rtmidi`, and a working
GPIO backend. The reviewed CM5 installation uses `lgpio` and Mido's RtMidi backend
with ALSA MIDI ports.

On a compatible Debian/Raspberry Pi OS installation, install the distribution
packages:

```sh
sudo apt update
sudo apt install python3-gpiozero python3-mido python3-rtmidi python3-lgpio
```

The user running the controller must have access to the GPIO devices and ALSA MIDI
devices. Avoid running as root merely to work around missing device permissions.

## Running

**Do not run a second copy while an existing controller owns the GPIO pins.**

From this directory, with PiPedal running, start the controller in the foreground:

```sh
python3 -u controller.py
```

Stop it with Ctrl+C. To list available MIDI destinations without acquiring GPIOs
or sending messages:

```sh
python3 -c 'import mido; print("\n".join(mido.get_output_names()))'
```

The script finds destinations whose name contains `PiPedal:in`, such as
`PiPedal:PiPedal:in 128:0`. The numeric ALSA address can change; it is deliberately
not hardcoded. If multiple destinations match, it refuses to choose arbitrarily:
adjust `Settings.target_port` to identify exactly one destination.

Logs have timestamps, severity, and thread names. `GPIO controller running` means
the buttons are initialized; **`MIDI connected to ...`** confirms the port opened.
Connection-state messages are logged only when the state changes.

The MIDI worker checks the destination every second while connected. Missing ports
and backend failures trigger retries with exponential backoff from 0.5 to 10 seconds,
without requiring a button press. Before each action it refreshes the subscription
so a silent stale ALSA connection is not reused, including when a restart reuses
the same port name/address. GPIO callbacks never discover ports, send MIDI, or sleep.

The action queue holds at most eight entries and drops the newest if full. Actions
expire after 250 ms (checked again after opening the port), and disconnects discard
pending actions. Presses released while disconnected are dropped, not saved for a
later burst. A warning summarizes dropped actions during periodic checks.

A button held at startup must be released and pressed again before it can produce
an action. Unmatched releases and duplicate press callbacks do not generate extra
notes. Both short and long actions still fire **on release**, not at the threshold.

## Configuration

Configuration is centralized in the `Settings` dataclass in `controller.py`:

| Setting | Location |
| --- | --- |
| MIDI destination substring | `target_port` |
| GPIO pins and short/long notes | `buttons` / `ButtonMapping` |
| Long-press threshold, seconds | `hold_threshold` |
| Software debounce, seconds | `bounce_time` |
| MIDI channel, zero-based | `channel` |
| Note duration, seconds | `note_duration` |
| Queue capacity and expiry, seconds | `queue_size`, `max_action_age` |
| Connected polling and retry backoff, seconds | `poll_interval`, `retry_initial`, `retry_max` |

Inputs use internal pull-ups. There are no command-line options or external
configuration files. Importing the module does not acquire GPIOs or open MIDI.

## Deployment with systemd (recommended)

Earlier instructions used `/home/pi/controller-daemon.py`. The reviewed CM5 starts
`/home/pi/controller-gemini.py` through a user cron `@reboot` job and redirects output
to `/home/pi/controller.log`. Updating this repository **does not rename or restart
that live copy**.

The included [pipedal-controller.service](pipedal-controller.service) supervises the
process, restarts failures, and sends logs to the journal. It runs as `pi` and expects
`/home/pi/controller.py`. Edit `User`, `WorkingDirectory`, and `ExecStart` if needed;
the service account needs GPIO and ALSA MIDI access. Ordering after PiPedal helps at
boot, but the controller can start before its MIDI port exists.

During a maintenance window, from this directory on the device:

1. Back up the existing script and user crontab.
2. Remove **only the old controller's `@reboot` entry** using `crontab -e`. Do not
   leave cron and systemd both configured to start it.
3. Identify and stop the existing controller process before testing or starting a
   replacement. Removing the cron entry does not stop an already running copy.
4. Install the updated script, then test both buttons in the foreground:

   ```sh
   install -m 755 controller.py /home/pi/controller.py
   /usr/bin/python3 -u /home/pi/controller.py
   ```

   Stop the foreground test with Ctrl+C before starting the service.
5. Install and enable the service:

   ```sh
   sudo install -m 644 pipedal-controller.service /etc/systemd/system/pipedal-controller.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now pipedal-controller.service
   sudo systemctl status pipedal-controller.service
   sudo journalctl -u pipedal-controller.service -f
   ```

6. Verify both button mappings, a PiPedal restart/reconnect, and boot startup during
   a planned reboot. Keep the old script until the replacement has been verified.

SIGINT and SIGTERM request shutdown, disable callbacks, close both GPIO devices,
interrupt any note-duration wait, attempt a final note-off, and close MIDI. A worker
or callback failure causes a nonzero exit so systemd can restart it. Shutdown waits
up to five seconds for the worker; backend I/O that hangs beyond that is reported
as a failure rather than hanging the process indefinitely.

To roll back, stop and disable the new service, restore the old script, and restore
only its previous cron entry. Never run two controllers against the same GPIOs.

## Tests

Run from this directory:

```sh
python3 -m unittest discover -s tests -v
```

The tests use only the Python standard library and fake MIDI/GPIO objects; they
do not require `mido`, `gpiozero`, a Pi, or connected hardware. They cover timing,
startup releases, overlapping presses, bounded/expiring queues, reconnect/backoff,
backend failures, note-off cleanup, callback/worker failures, and resource shutdown.

## Known limitations

- Hardware bounce, GPIO timing under load, and real ALSA/PiPedal restarts still need
  validation on the assembled pedal. The software is not hard real-time.
- MIDI has no delivery acknowledgement here. A restart between discovery/open/send
  can still lose an action or its note-off; cleanup is best-effort, not a guarantee.
- Per-action subscription refresh adds backend work and should be checked for
  acceptable latency on the CM5. The worker does not replay failed actions.
- The main loop detects failures in its callbacks and worker, not every possible
  fault inside GPIO/backend library threads. There is no hardware input watchdog.
- The running device is unchanged until you deliberately deploy this version and
  migrate its startup configuration.
