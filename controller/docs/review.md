# Controller review and improvement plan

## Scope

This review covers the original `controller-gemini.py` fetched from `pipedal-cm5`,
which is identical to this repository's former `controller-daemon.py`. It is now
named `controller.py` without functional changes. Inspection of the device was
read-only: no controller restart, GPIO acquisition, MIDI transmission, or PiPedal
configuration changes were performed.

The environment inspected had Python 3.13.5, gpiozero 2.0.1, python-rtmidi 1.5.8,
and lgpio 0.2.2.0. Mido selected its RtMidi backend. The running controller was
launched by cron's `@reboot` facility rather than a dedicated systemd unit.

## What already works well

- The implementation is small and its two-button mappings are easy to inspect.
- Inputs use pull-ups and software debounce.
- MIDI discovery matches a destination name instead of a variable ALSA address.
- Note-on messages are paired with note-off messages during normal operation.
- A missing MIDI destination does not immediately terminate the process.

## Findings

### P1: Unmatched releases can generate false long-press actions

`start_times` is initialized to zero. If a release is handled before its press
timestamp is recorded, `end_timer()` subtracts zero from the current Unix time.
The resulting duration exceeds the hold threshold and selects a long action.

The live log contained:

```text
Button 26 released after 1792962995.19s
Skipping Note 40 (Target device not found)
```

This is consistent with a release without a recorded press; the exact electrical
or startup event that produced it was not established. No MIDI action was sent
for this logged event because the destination was unavailable.

**Recommendation:** Use `None` for an absent press timestamp, ignore unmatched
releases, and consume/reset the timestamp after a release. Require a fresh press
after startup if the initial button state is uncertain.

### P1: Blocking work runs inside GPIO callbacks

`end_timer()` calls `trigger_midi()` directly. Discovery, port opening, logging,
MIDI transmission, and the 50 ms note-duration sleep all happen in that callback.
The installed lgpio implementation dispatches callbacks on a shared notification
thread, so this work can delay processing of edges from both buttons.

**Recommendation:** Keep callbacks short and hand actions to a dedicated MIDI
worker through a bounded queue. Let the worker own the output port. Define a
policy for queue overflow and discard stale actions rather than replaying a burst
after reconnection. If measuring closely spaced edges precisely is important,
consider backend event timestamps rather than callback execution time.

### P1: Connection recovery assumes errors will expose a stale connection

`ensure_connection()` returns success whenever `outport` is not `None`. Only
`IOError`/`OSError` during sending clears it. A PiPedal restart can invalidate an
ALSA subscription without necessarily generating a handled send exception.

The initial log showed a missing destination. A later read-only enumeration found
`PiPedal:PiPedal:in 128:0`. In this initial-discovery case, the next action should
retry discovery because `outport` remains `None`. Recovery after an established
connection becomes stale was not exercised on the live device.

**Recommendation:** Reconcile destination availability and identity periodically,
reopen connections when required, and retry with backoff outside GPIO callbacks.
Enumeration alone does not prove delivery; test the actual backend's behavior
when PiPedal is restarted. Inspect backend-specific exception types and handle
them deliberately. Do not silently replay actions skipped during an outage.

### P2: Hold timing uses an adjustable wall clock

`time.time()` can jump after clock synchronization or manual adjustment, producing
incorrect hold durations.

**Recommendation:** Use `time.monotonic()` for elapsed-time measurement, or a
consistent monotonic backend timestamp source if moving to edge timestamps.

### P2: Cron does not supervise the controller

The current cron job starts the script at boot but does not restart it on failure.
A live main process also does not prove the GPIO callback thread is healthy.

**Recommendation:** Migrate to a dedicated systemd service, running as an
appropriately permitted non-root user, with `Restart=on-failure`, a retry delay,
and journal logging. Make worker/callback failures visible to the main process so
that supervision can respond. Startup ordering is useful but is not a replacement
for reconnect logic.

### P2: Shutdown and diagnostics need clearer lifecycle management

The script closes its MIDI port in a `finally` block but does not explicitly close
the buttons or handle SIGTERM gracefully. The bare `except:` during port cleanup
can hide unrelated exceptions. The "System Ready" message is printed before a
connection is established.

**Recommendation:** Use a `main()` entry point and explicit lifecycle management,
close GPIO and MIDI resources, handle termination, and use structured logging
with timestamps. Distinguish startup, disconnected, connected, and failed states.
Avoid logging every failed periodic discovery attempt at full volume.

### Design choice: Long presses act only on release

This is current behavior, not necessarily a bug. A ten-second hold produces its
action only after all ten seconds have elapsed and the button is released.

**Recommendation:** Confirm the desired stage behavior. If a long action should
fire at the one-second threshold, track a per-press long-action flag and suppress
the short action on release. Do not accidentally emit both actions.

## Suggested architecture

```text
GPIO callbacks -> button state / short-long classification -> bounded action queue
                                                              |
                                                              v
                                         single MIDI worker and connection owner
```

Keep GPIOs, note mappings, MIDI channel, thresholds, and debounce settings together
in a small configuration structure. Avoid adding unnecessary complexity for a
two-button controller.

## Validation performed

- Parsed the full source as Python without executing hardware setup.
- Exercised extracted timer functions locally with a fake clock and MIDI action
  collector: an unmatched release selected the long note; a normal 200 ms press
  selected the short note; a simulated forward wall-clock jump selected the long
  note for what could physically be a short press.
- Read the controller log, process/service information, installed callback/backend
  implementation, and available MIDI destination names.

These checks are not end-to-end hardware tests. Reconnection failure modes,
electrical bounce, and simultaneous-button timing remain to be tested safely.

## Suggested regression tests

1. Startup with each button released and held; release without a recorded press.
2. Short, long, and exact-threshold presses; repeated and unmatched releases.
3. Overlapping presses on both buttons and rapid sequential actions.
4. Electrical bounce and debounce behavior on the actual carrier/buttons.
5. Forward and backward wall-clock adjustments without duration changes.
6. Missing MIDI destination at startup; delayed appearance; disappearance and
   reappearance after a PiPedal restart.
7. MIDI open/send/close failures, including failure between note-on and note-off.
8. Queue overflow, stale-action expiry, and no delayed burst after reconnection.
9. SIGTERM/Ctrl+C cleanup, worker failure propagation, and service restart.

## Recommended implementation order

1. Guard unmatched releases and switch duration measurement to a monotonic clock.
2. Separate GPIO callbacks from MIDI delivery and make reconnection reliable.
3. Add hardware-free regression tests and explicit resource cleanup.
4. Replace cron startup with a supervised systemd service.
5. Refine configuration and long-press semantics if needed.
