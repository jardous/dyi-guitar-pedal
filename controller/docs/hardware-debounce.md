# Hardware debounce for the footswitches

Use one filter per **normally open, momentary** footswitch. Both circuits below
preserve the controller's active-low behavior: released = HIGH, pressed = LOW.
Keep the existing `bounce_time=0.05` software debounce enabled initially.

These are starting designs, not measurements from the assembled pedal. Check the
actual switches with an oscilloscope or logic analyzer before relying on hardware
debounce alone.

## Recommended: RC filter plus Schmitt-trigger buffer

Use a **non-inverting** Schmitt-trigger buffer to turn the capacitor's slow voltage
ramps into clean logic edges. One
[SN74LVC2G17](https://www.ti.com/product/SN74LVC2G17) provides two independent
channels, enough for both buttons. Power it from the same **3.3 V** rail as the
CM5 GPIO interface, never 5 V.

### Schematic: one channel, repeated for each switch

```text
                         R1 10k             R2 1k
  CM5 3.3 V ------------/\/\/\-----o-------/\/\/\-------o------ A
                                  |                    |      |\
                                  |                    |      | \    U1 channel
                             SW1  o                 C1 ===     |  >--- Y ---> GPIO
                                   /              1 uF |      | /    non-inverting
                                  o                    |      |/     Schmitt buffer
                                  |                    |
  CM5 GND ------------------------o--------------------o

  U1 supply (shared by both channels):

  CM5 3.3 V --------o---------------- U1 VCC
                    |
                C3 === 100 nF
                    |
  CM5 GND ----------o---------------- U1 GND
```

`SW1` closes to ground when pressed. The capacitor is on the **buffer-input side
of R2**; do not connect it to the buffer output. Use the selected package's
datasheet to identify A, Y, VCC, and GND pins; the drawing uses signal names, not
package pin numbers.

| Channel | Switch | Pull-up | Series resistor | Filter capacitor | Buffer connection |
| --- | --- | --- | --- | --- | --- |
| 1 | SW1 | R1: 10 kOhm | R2: 1 kOhm | C1: 1 uF | 1A -> 1Y -> BCM GPIO 27 |
| 2 | SW2 | R3: 10 kOhm | R4: 1 kOhm | C2: 1 uF | 2A -> 2Y -> BCM GPIO 26 |

Share the 3.3 V supply, ground, and U1 between channels, but **do not share their
RC nodes or join their outputs**. If only one channel is populated, tie the
unused buffer input to ground and leave its output unconnected.

### Parts for both buttons

| Quantity | Part | Purpose |
| --- | --- | --- |
| 2 | Normally open momentary footswitch | Connects the switch node to ground |
| 2 | 10 kOhm resistor, 1% | External pull-up |
| 2 | 1 kOhm resistor, 1% | Limits capacitor discharge current and sets press filtering |
| 2 | 1 uF ceramic capacitor, X7R, rated >= 10 V | RC filtering; check effective capacitance at 3.3 V |
| 1 | SN74LVC2G17 dual non-inverting Schmitt buffer | Clean, hysteretic logic edges |
| 1 | 100 nF ceramic capacitor | U1 supply decoupling, mounted close to VCC/GND |

### Operation and timing

- **Released:** C1 charges through R1 + R2; the buffer output goes HIGH.
- **Pressed:** SW1 grounds the R1/R2 junction; C1 discharges through R2 and the
  buffer output goes LOW. The switch sinks about 0.33 mA continuously through R1,
  plus an initial capacitor discharge current of approximately 3.3 mA through R2.
- Nominal time constants are **11 ms on release** (`11 kOhm * 1 uF`) and
  **1 ms on press** (`1 kOhm * 1 uF`). These are RC time constants, not guaranteed
  debounce intervals or exact logic-transition delays.
- The buffer's hysteresis prevents repeated transitions around a single input
  threshold, but sufficiently long contact bounces can still cross both
  thresholds. Verify the actual waveform. Increasing C1 to 2.2 uF increases both
  time constants by 2.2x if more filtering is needed, at the cost of latency.
- Filtering delays press and release edges differently, so measured hold duration
  can shift by a few milliseconds near the one-second threshold.

The existing `pull_up=True` setting can remain: the internal pull-up is on the
buffer's **output**, not on the RC node, and only adds a small load when LOW.
No controller code changes are required for this non-inverting circuit.

## Minimal alternative: RC only

If omitting U1, connect the filtered node directly to the GPIO:

```text
                         R1 10k             R2 1k
  CM5 3.3 V ------------/\/\/\-----o-------/\/\/\-------o----> BCM GPIO 27 or 26
                                  |                    |
                             SW1  o                 C1 === 1 uF
                                   /                   |
                                  o                    |
                                  |                    |
  CM5 GND ------------------------o--------------------o
```

Repeat for the other button. R2 prevents the charged capacitor being shorted
directly through the switch contacts. This is useful noise/bounce filtering, but
it does not provide a separate, specified Schmitt-trigger stage; keep software
debounce enabled and check the carrier/SoC input requirements for slow edges.

The GPIO's enabled internal pull-up changes the nominal RC timing and produces a
small nonzero pressed voltage through R2. Verify the LOW voltage and actual edge
timing on your board. The Schmitt-buffer version avoids this interaction at the
filter node and is preferred for a more predictable input interface.

## Wiring and verification

- Power off before wiring. Use **3.3 V only**, with a common CM5/carrier ground.
  Do not connect a GPIO, buffer output, or pull-up to 5 V or the pedal's analog
  supply rails. Confirm BCM-to-connector routing on the actual carrier board.
- Put the RC parts and buffer close to the GPIO interface. Keep footswitch wires
  away from audio inputs, switching regulators, and I2S lines; use a signal/ground
  twisted pair for each switch. This circuit is not a substitute for appropriate
  ESD protection on externally accessible or long cables.
- Before connecting a buffer output to the CM5, check its supply and output
  voltages. Then verify HIGH when released and LOW when pressed on each channel.
- Check repeated presses, releases, overlapping presses, and long holds. Verify
  that each physical action produces the intended single MIDI action.
- Start with the existing 50 ms software debounce. Reduce it only after measured
  testing; hardware filtering does not fix the controller's unmatched-release
  startup bug described in [the review](review.md).
