# Hardware debounce for the footswitches

The original pedal uses **one series resistor and one capacitor per button**.
The capacitor is marked **104**, meaning **100 nF (0.1 uF)**, not 1 uF. A normally
open momentary switch and its series resistor form a branch in parallel with the
capacitor. Both series resistors are **1 kOhm**, confirmed by the pedal's owner.

## Original pedal wiring: physical 40-pin header numbers

```text
  Pin 13 (GPIO27) ----o----------|| C1 100 nF---------o----- Pin 9 (GND)
                      |                               |
                      +---------SW1----R1 1k----------+

  Pin 37 (GPIO26) ----o----------|| C2 100 nF---------o----- Pin 39 (GND)
                      |                               |
                      +---------SW2----R2 1k----------+
```

`SW1` and `SW2` are normally open and close when pressed. R1/R2 are **in series
with the switches**, not pull-ups. Each GPIO also requires a pull-up supplied by
the GPIO configuration or existing board circuitry; it is not an additional
discrete part in the drawn filter.

| Channel | GPIO header pin | BCM GPIO | Ground header pin | Capacitor | Series resistor |
| --- | --- | --- | --- | --- | --- |
| 1 | 13 | 27 | 9 | C1: 104 / 100 nF | R1: 1 kOhm |
| 2 | 37 | 26 | 39 | C2: 104 / 100 nF | R2: 1 kOhm |

Both ground pins connect to board ground. **Do not join the GPIO27 and GPIO26
signal nodes.** These physical pin numbers refer to the standard 40-pin header;
verify its orientation and the carrier board's routing before wiring.

## Matching the current script

Both inputs match `controller.py`: button 1 uses **BCM GPIO27 / physical pin 13**,
and button 2 uses **BCM GPIO26 / physical pin 37**. Physical pins **9 and 39** are
ground. The script enables each input's internal pull-up with `pull_up=True`.
No pin-mapping changes are required.

## Parts for the two filters

| Quantity | Part | Purpose |
| --- | --- | --- |
| 2 | Normally open momentary footswitch | Pulls its input toward ground through the series resistor |
| 2 | 1 kOhm resistor | Limits capacitor discharge current through the switch |
| 2 | 104 / 100 nF capacitor | Connected from its input to ground |

The marking `104` specifies capacitance only; it does not identify dielectric or
voltage rating. For replacement parts, a ceramic X7R rated >= 10 V is suitable
for this 3.3 V circuit. No buffer IC or 1 uF capacitor is specified here.

## Operation and timing

- **Released:** The input's pull-up charges the capacitor toward 3.3 V.
- **Pressed:** The switch discharges the capacitor through its series resistor.
  This limits the initial capacitor discharge current to approximately `3.3 V / R`
  for a fully charged capacitor, neglecting other small resistances: approximately
  **3.3 mA** with the 1 kOhm resistor.
- The pull-up and series resistor form a voltage divider while pressed. The
  steady voltage is approximately `3.3 V * R / (Rpullup + R)` and must be below the
  GPIO's specified LOW threshold. A series resistor that is too large can prevent
  reliable press detection, especially if a strong board pull-up is present.
- The nominal release time constant is `Rpullup * C`; the press time constant is
  approximately `(Rpullup || R) * C`. Include both internal and board pull-ups
  when calculating the effective `Rpullup`.
- For illustration only, a 10 kOhm effective pull-up and 100 nF capacitor give a
  **1 ms release time constant**. That pull-up value has not been established for
  this pedal; the actual timing depends on the effective pull-up resistance.
  With the 1 kOhm series resistor, the press time constant is at most approximately
  **0.1 ms** for nominal 100 nF.

Keep `bounce_time=0.05` enabled. This RC network reduces bounce but does not
guarantee a single clean edge for every switch, and it adds no separate hysteresis
stage. Check the carrier/SoC input requirements for slow edges and measure the
actual waveform. Filtering can also shift measured hold durations near the
one-second threshold.

## Wiring and verification

- Power off before wiring. Use **3.3 V logic only**, with a common CM5/carrier
  ground. Do not connect GPIOs or pull-ups to 5 V or the analog supply rails.
- Verify the header orientation: GPIO27 is physical pin 13 and GPIO26 is pin 37;
  use physical pins 9 and 39 for the respective ground connections.
- Place the capacitor near the GPIO interface. Keep switch wiring away from audio
  inputs, switching regulators, and I2S lines; use a signal/ground twisted pair
  per switch. This filter is not ESD protection for external cables.
- Check the pull-up configuration. Verify HIGH when released and a valid LOW when
  pressed, then inspect bounce with an oscilloscope or logic
  analyzer if available. If measuring resistance, power off first; parallel paths
  can affect an in-circuit reading, so isolate one resistor lead if necessary.
- Test repeated presses, releases, overlapping presses, and long holds. Confirm
  each physical action produces the intended single MIDI action.
- The updated controller ignores unmatched startup releases in software, as
  described in [the review](review.md); the RC filter is not a replacement for that
  guard. Leave software debounce enabled.
