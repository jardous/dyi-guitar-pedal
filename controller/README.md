# PiPedal button controller

A two-button GPIO-to-MIDI controller for a Raspberry Pi Compute Module 5 running
[PiPedal](https://github.com/rerdavies/pipedal).

`controller.py` is the former `controller-daemon.py`, renamed without behavior
changes. It also matches the `controller-gemini.py` reviewed on the running CM5.
The findings in [the code review](docs/review.md) are proposed improvements,
**not implemented fixes**.

## Hardware and button mappings

The script uses **BCM GPIO numbers**, not physical connector pin numbers. Each
button is configured with an internal pull-up and is active when connected to
ground. Verify the GPIO routing on your CM5 carrier board before wiring; do not
apply 5 V to a GPIO input.

| Button | BCM GPIO | Short press (< 1 second) | Long press (>= 1 second) |
| --- | --- | --- | --- |
| 1 | 27 | Note 10 | Note 20 |
| 2 | 26 | Note 30 | Note 40 |

For the circuit schematic using **one resistor and one capacitor per button**,
component values, and wiring notes, see [hardware debounce](docs/hardware-debounce.md).
The original pedal uses **104 / 100 nF capacitors** from physical pins **13 to 9**
and **37 to 39**, with a switch and series resistor in parallel with each capacitor.
The series resistors appear to be **1 kOhm**, pending measurement. Both inputs
match the script: GPIO27 / pin 13 and GPIO26 / pin 37. Pins 9 and 39 are ground.
Keep software debounce enabled.

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

The script selects the first destination whose name contains `PiPedal:in`, such
as `PiPedal:PiPedal:in 128:0`. The numeric ALSA address can change; it is deliberately
not hardcoded. If multiple ports match, the first enumerated match is used.

The initial "System Ready" message does not prove MIDI is connected. Look for
`SUCCESS: Connected to destination ...`. If no destination is available, an action
is skipped and connection discovery is retried on the next action.

## Configuration

Configuration is currently edited in `controller.py`:

| Setting | Location |
| --- | --- |
| MIDI destination substring | `TARGET_PORT_NAME` |
| Long-press threshold, seconds | `HOLD_THRESHOLD` |
| GPIO pins, pull-ups, debounce | `Button(...)` constructors |
| Short/long MIDI notes | `when_released` callbacks |
| Note duration | `time.sleep(0.05)` in `trigger_midi()` |

There are no command-line options or external configuration files.

## Existing device deployment and rename

Earlier instructions used `/home/pi/controller-daemon.py`. The reviewed CM5 starts
`/home/pi/controller-gemini.py` through a user cron `@reboot` job and redirects output
to `/home/pi/controller.log`. Updating this repository **does not rename or restart
that live copy**.

To deploy the renamed script later, during a maintenance window:

1. Back up the existing script and user crontab.
2. Stop the running controller before testing or starting a replacement. Avoid
   leaving two controller processes competing for the same GPIOs.
3. Install this directory's script as `/home/pi/controller.py`.
4. Edit only the existing controller entry with `crontab -e` to use the new name:

   ```cron
   @reboot /usr/bin/python3 -u /home/pi/controller.py > /home/pi/controller.log 2>&1
   ```

5. Start one copy using the new path, verify the connection and both button
   mappings, and confirm boot startup during a planned reboot.

Keep the old script until the replacement has been verified. A dedicated systemd
service is recommended instead of cron for future restart supervision; no service
unit or automatic deployment is included in this baseline.

## Known limitations

The baseline has a confirmed unmatched-release timing bug, uses an adjustable
wall clock for hold durations, blocks GPIO callbacks while sending MIDI, and has
limited reconnection and process supervision. See [docs/review.md](docs/review.md)
for evidence, priorities, and suggested tests.
