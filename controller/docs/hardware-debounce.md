# Hardware debounce for the footswitches

The original pedal uses **one series resistor and one capacitor per button**.
The capacitor is marked **104**, meaning **100 nF (0.1 uF)**, not 1 uF. A normally
open momentary switch and its series resistor form a branch in parallel with the
capacitor. Both series resistors appear to be **1 kOhm**; this is a visual estimate,
not a confirmed measurement.

## Original pedal wiring: physical 40-pin header numbers

```text
  Pin 5 (GPIO3)  -----o----------|| C1 100 nF---------o----- Pin 9 (GND)
                      |                               |
                      +---------SW1----R1 ~1k---------+

  Pin 37 (GPIO26) ----o----------|| C2 100 nF---------o----- Pin 39 (GND)
                      |                               |
                      +---------SW2----R2 ~1k---------+
```

`SW1` and `SW2` are normally open and close when pressed. R1/R2 are **in series
with the switches**, not pull-ups. Each GPIO also requires a pull-up supplied by
the GPIO configuration or existing board circuitry; it is not an additional
discrete part in the drawn filter.

| Channel | GPIO header pin | BCM GPIO | Ground header pin | Capacitor | Series resistor |
| --- | --- | --- | --- | --- | --- |
| 1 | 5 | 3 | 9 | C1: 104 / 100 nF | R1: likely 1 kOhm; verify |
| 2 | 37 | 26 | 39 | C2: 104 / 100 nF | R2: likely 1 kOhm; verify |

Both ground pins connect to board ground. **Do not join the GPIO3 and GPIO26
signal nodes.** These physical pin numbers refer to the standard 40-pin header;
verify its orientation and the carrier board's routing before wiring.

## Important: first-button mismatch with the current script

`controller.py` currently configures **BCM GPIO27** for button 1, which is physical
**pin 13**, not pin 5. Button 2 uses BCM GPIO26 / physical pin 37 and matches the
reported wiring. The script has not been changed to resolve this discrepancy.

Confirm whether the first switch should remain on GPIO3 or be wired to GPIO27
before changing code or hardware. GPIO3 is also **I2C SDA**: a switch to ground
on that line can interfere with an enabled I2C bus and connected devices. Check
the carrier/Codec Zero wiring and configuration before using it as a button.

## Parts for the two filters

| Quantity | Part | Purpose |
| --- | --- | --- |
| 2 | Normally open momentary footswitch | Pulls its input toward ground through the series resistor |
| 2 | Resistor, likely 1 kOhm; verify | Limits capacitor discharge current through the switch |
| 2 | 104 / 100 nF capacitor | Connected from its input to ground |

The marking `104` specifies capacitance only; it does not identify dielectric or
voltage rating. For replacement parts, a ceramic X7R rated >= 10 V is suitable
for this 3.3 V circuit. No buffer IC or 1 uF capacitor is specified here.

## Operation and timing

- **Released:** The input's pull-up charges the capacitor toward 3.3 V.
- **Pressed:** The switch discharges the capacitor through its series resistor.
  This limits the initial capacitor discharge current to approximately `3.3 V / R`
  for a fully charged capacitor, neglecting other small resistances: approximately
  **3.3 mA** if R is 1 kOhm.
- The pull-up and series resistor form a voltage divider while pressed. The
  steady voltage is approximately `3.3 V * R / (Rpullup + R)` and must be below the
  GPIO's specified LOW threshold. A series resistor that is too large can prevent
  reliable press detection, especially if a strong board pull-up is present.
- The nominal release time constant is `Rpullup * C`; the press time constant is
  approximately `(Rpullup || R) * C`. Include both internal and board pull-ups
  when calculating the effective `Rpullup`.
- For illustration only, a 10 kOhm effective pull-up and 100 nF capacitor give a
  **1 ms release time constant**. That pull-up value has not been established for
  this pedal; the actual timing cannot be specified until the series resistor
  values and pull-up configuration are confirmed. With a 1 kOhm series resistor,
  the press time constant is at most approximately **0.1 ms** for nominal 100 nF.

**Check GPIO3's I2C pull-up in particular:** if the carrier has a 1.8 kOhm pull-up
to 3.3 V, a 1 kOhm series switch resistor gives a pressed voltage of approximately
**1.18 V** (`3.3 * 1 / (1.8 + 1)`). That must not be assumed to be a valid LOW.
The carrier's actual pull-up value is not established here; verify it and the
GPIO input specification before using this wiring. A weak internal pull-up alone
produces a much lower pressed voltage with the same series resistor.

Keep `bounce_time=0.05` enabled. This RC network reduces bounce but does not
guarantee a single clean edge for every switch, and it adds no separate hysteresis
stage. Check the carrier/SoC input requirements for slow edges and measure the
actual waveform. Filtering can also shift measured hold durations near the
one-second threshold.

## Wiring and verification

- Power off before wiring. Use **3.3 V logic only**, with a common CM5/carrier
  ground. Do not connect GPIOs or pull-ups to 5 V or the analog supply rails.
- Verify the first-button GPIO discrepancy and possible I2C conflict before
  copying the original wiring to the current CM5 setup.
- Place the capacitor near the GPIO interface. Keep switch wiring away from audio
  inputs, switching regulators, and I2S lines; use a signal/ground twisted pair
  per switch. This filter is not ESD protection for external cables.
- Confirm the resistor values and pull-up configuration. Check HIGH when released
  and a valid LOW when pressed, then inspect bounce with an oscilloscope or logic
  analyzer if available. If measuring resistance, power off first; parallel paths
  can affect an in-circuit reading, so isolate one resistor lead if necessary.
- Test repeated presses, releases, overlapping presses, and long holds. Confirm
  each physical action produces the intended single MIDI action.
- Hardware filtering does not fix the unmatched-release startup bug described in
  [the review](review.md). Leave software debounce enabled.
