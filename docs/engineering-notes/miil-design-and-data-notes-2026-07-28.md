# MIIL Design and Data Notes (2026-07-28)

## Decision summary

Manual Instruction Interval Labeling (MIIL) is implemented as an annotation
paradigm above acquisition, not as a device feature. Acquisition remains the
only owner of source startup, readiness, pause, resume, stop, and failure
handling. MIIL neither opens nor closes serial/Bluetooth transports and has no
knowledge of W2 or BWT901 protocols.

The resulting dependency direction is:

```text
Source / protocol / transport -> CaptureStore
                                  |
Acquisition lifecycle ------------+
              |
       RecordingSession
              |
     MIIL interval model
              |
CSV sample-code resolver + stimulus sidecar/metadata
```

This boundary keeps W2 and BWT901 parsing unchanged, permits both sensor types
to receive the same annotation, and avoids a second device-control state
machine in the Stimulus window.

## Lifecycle ownership

MIIL must be selected and configured while acquisition is stopped. Its default
codebook is pre-applied; after any editor change, Start is blocked until the
operator selects Apply. Action codes are unique positive integers, while `0`
and `-1` are reserved. The applied codebook is frozen for the recording.

Acquisition Start and Stimulus Start converge on the same recording-session
start operation. Sources pass through the existing readiness barrier first;
MIIL then opens an initial `no_stimulus` (`0`) interval. MIIL action buttons do
not affect source state. Stop closes the current annotation interval and ends
the complete acquisition session through the existing lifecycle.

Pause preserves the current instruction. The shared capture clock and MIIL
elapsed display remain frozen, and Resume continues the same interval without
creating an artificial annotation boundary. This matches the raw capture
policy in which rows are not appended while paused.

## Interval semantics

Normal actions have unique positive codes. Re-selecting the currently active
action is a no-op so an accidental double-click cannot produce a very short
interval. Re-selecting `no_stimulus` is also a no-op.

`no_stimulus` (`0`) means that the raw data is intentionally not assigned to a
classification action. It is useful as a preparation or buffer interval and
is retained on disk.

`drop_stimulus` (`-1`) invalidates the entire current normal-action interval,
back to that interval's original start, and remains active until a subsequent
normal action or No Stimulus is selected. The implementation does not mutate
already captured rows. Instead, the interval's effective code becomes `-1`,
and the save-time resolver applies it retrospectively. The audit retains the
original action/code and the shared time when Drop was pressed.

Drop while No Stimulus is active is ignored because code `0` is already
excluded by default and the click is most likely accidental. A repeated Drop
is ignored for the same fragmentation reason. Selecting the same normal action
after Drop is not a repeated selection: it closes the dropped interval and
opens a new valid interval.

## Why boundaries use two coordinates

The shared capture time is the correct coordinate for lifecycle state,
operator-visible duration, and cross-stream audit. It is not sufficient by
itself for assigning every stored row in the current multi-device system:

- W2 timestamps are anchored at the first packet and then reconstructed from
  nominal sample rate, so their accumulated timestamp can drift from host
  capture time.
- Independent W2 devices can accumulate different row counts.
- BWT901 rows arrive at a much lower rate and use host notification time.
- Queue draining and UI refresh occur asynchronously relative to device
  packets.

At every manual transition, MIIL therefore snapshots a `CapturePosition` with
the shared capture time and each stream's next row index. When saving, the
generic sample-code resolver receives `(stream_id, row_index, time_s)` and uses
the row interval belonging to that stream. Shared time is retained for display,
audit, and fallback if a stream was absent from a cursor snapshot.

This design deliberately does not force streams to equal length, resample raw
data, fill missing rows, or alter device timestamps. It prevents accumulated
timestamp drift from moving later manual annotations, while preserving each
source's actual received samples.

It does not provide hardware synchronization. A manual transition may still
be uncertain by approximately one device packet or one queue/UI refresh,
roughly 100 ms under the current operating conditions. The uncertainty is
small relative to the requested offline trims, but it is not valid for exact
onset or reaction-time claims.

## Save contract

Every populated W2 and BWT901 CSV has one integer `stimulus_code` column. CSV
rows remain raw apart from adding that annotation; no rows are dropped at save
time for codes `0` or `-1`.

The interval sidecar (`capture.stimulus.csv`) is an operator-readable audit. It
records effective and original codes, action/label, shared start/end time,
duration and status, plus Drop press time where applicable.

The `stimulus` section in `capture.metadata.json` records:

- the MIIL paradigm identifier and name;
- reserved-code semantics and the frozen action codebook;
- every interval's shared start/end time;
- per-stream start/end row counts;
- original and effective codes and Drop audit data;
- the row-cursor boundary method; and
- recommended offline trimming and windowing parameters.

The generic CSV writer accepts a per-sample resolver and does not import or
inspect the MIIL controller. The older timed-schedule paradigm retains its
time-based resolver. This keeps persistence independent of any particular
stimulus implementation.

## Duration and offline analysis

There is no automatic action duration and no hard-coded 5-second or 10-second
limit. An interval ends only on an operator command or recording Stop. Expected
5--10 second holds therefore do not add runtime scheduling complexity.

The first implementation stores recommendations but intentionally leaves
trimming and window extraction to a separate offline analysis step. That step
should:

1. exclude codes `0` and `-1` by default;
2. treat each MIIL interval independently;
3. trim action starts by about `1.0 s` and ends by `0.5--1.0 s`;
4. trim Rest starts by `0.5--1.0 s` and ends by about `0.5 s`;
5. reject a result shorter than the chosen `1.5--2.0 s` minimum;
6. create `200--300 ms` windows with `50%` overlap; and
7. never allow a window to cross an interval boundary.

The exact trims should be fixed in the analysis protocol and saved with
derived data. Analysts should also use time coordinates and common coverage
when combining independent device streams; raw row number equality is neither
expected nor required.

## Interpretation limits

MIIL labels the task requested by the experimenter. It does not verify actual
movement, identify a biomechanical transition, or measure the participant's
response time. The label should be interpreted as a high-confidence
steady-state segment under the specified instruction only after transition
trimming and quality review.

For exact EMG onset, reaction time, kinematic phase, or millisecond closed-loop
work, add a validated observed event and hardware-appropriate synchronization.
Software readiness barriers and per-stream row cursors are not substitutes for
a shared hardware clock or trigger.

## Safe configuration sequence between experiments

The recommended sequence is Stop, Save, review the saved paths, then change and
Apply the next MIIL configuration. Saving before reconfiguration makes the
completed experiment self-contained and avoids confusion over which codebook
belongs to which capture. No protocol, transport, source, or plot change is
needed when only the MIIL action list changes.
