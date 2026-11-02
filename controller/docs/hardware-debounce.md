# Hardware debounce for the footswitches

The hardware filter uses **one resistor and one capacitor per button**, with no
buffer IC or additional series resistor. Use a **normally open, momentary**
footswitch. The circuit preserves active-low behavior: released = HIGH,
pressed = LOW. Keep the existing `bounce_time=0.05` software debounce enabled.

## Schematic: repeat once for each button

```text
                         R1 10k
  CM5 3.3 V ------------/\/\/\-----o--------------------o----> BCM GPIO
                                  |                    |
                             SW1  o                 C1 === 1 uF
                                   /                   |
                                  o                    |
                                  |                    |
  CM5 GND ------------------------o--------------------o
```

The resistor pulls the GPIO node up to 3.3 V. The capacitor and switch are both
connected between that same GPIO node and ground, **in parallel**. The capacitor
is not in series with the GPIO, and the resistor is not in series with the switch.

| Channel | Switch | Pull-up to 3.3 V | Capacitor to GND | GPIO node |
| --- | --- | --- | --- | --- |
| 1 | SW1 | R1: 10 kOhm | C1: 1 uF | BCM GPIO 27 |
| 2 | SW2 | R2: 10 kOhm | C2: 1 uF | BCM GPIO 26 |

The two channels share only the supply and ground; **do not join their GPIO
nodes**. These are BCM numbers, not connector pin numbers. Confirm routing on
your CM5 carrier board before wiring.

## Parts for both buttons

| Quantity | Part | Purpose |
| --- | --- | --- |
| 2 | Normally open momentary footswitch | Grounds its GPIO node when pressed |
| 2 | 10 kOhm resistor, 1% | External pull-up |
| 2 | 1 uF ceramic capacitor, X7R, rated >= 10 V | RC filtering; check effective capacitance at 3.3 V |

The resistor/capacitor values are starting recommendations, not measurements from
the assembled pedal.

## Operation and timing

- **Pressed:** The switch grounds the GPIO and discharges the capacitor directly
  through its contacts. The external pull-up supplies about **0.33 mA** while held.
  There is also a brief capacitor-discharge current pulse, limited by contact,
  wiring, and capacitor resistance, **not by the 10 kOhm pull-up**. Check that the
  chosen switch tolerates this capacitive load; avoid arbitrarily large capacitors.
- **Released:** The capacitor charges through the pull-up, slowing the return to
  HIGH and helping bridge short contact openings during bounce.
- With the external resistor alone, the nominal release time constant is
  **10 ms** (`10 kOhm * 1 uF`). This is not a guaranteed debounce interval or the
  exact GPIO switching delay. Closing the switch is much faster than releasing it.
- The existing `pull_up=True` can remain enabled, but its internal pull-up is in
  parallel with the external resistor. The actual resistance and release time
  constant will therefore be smaller than the external-only calculation.
- Filtering delays release more than press, so the measured hold duration can
  increase slightly near the one-second threshold.

No controller code changes are required. Keep the existing software debounce:
this simple RC network reduces bounce but does not guarantee one clean edge for
every switch. It adds no separate hysteresis stage; check the carrier/SoC input
requirements for slow edges and verify the actual waveform.

## Wiring and verification

- Power off before wiring. Use **3.3 V only**, with a common CM5/carrier ground.
  Do not connect a GPIO or pull-up to 5 V or the pedal's analog supply rails.
- Place the capacitor and pull-up near the GPIO interface. Keep footswitch wiring
  away from audio inputs, switching regulators, and I2S lines; use a signal/ground
  twisted pair per switch. This filter is not ESD protection for external cables.
- Check the supply and node voltage before connecting to the CM5. Verify HIGH
  when released and LOW when pressed, then inspect bounce with an oscilloscope or
  logic analyzer if available.
- Test repeated presses, releases, overlapping presses, and long holds. Confirm
  that each physical action produces the intended single MIDI action.
- Leave the 50 ms software debounce enabled unless measurements justify changing
  it. Hardware filtering does not fix the unmatched-release startup bug described
  in [the review](review.md).
