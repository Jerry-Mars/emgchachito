# Manual Instruction Interval Labeling (MIIL) User Manual

## Purpose

Manual Instruction Interval Labeling (MIIL) labels a continuous recording with
the task that the participant has been instructed to perform. An operator
changes the active instruction manually while acquisition continues. Every
saved W2 and BWT901 sample receives a `stimulus_code`.

MIIL also provides an optional **MIIL Guided Sequence** mode. Guided Sequence
prepares a repeated action order before acquisition, then uses the operator's
Enter key to advance it. This is an **Enter-to-Advance** workflow: it reduces
repetitive button selection but does not assign or enforce an action duration.

The code describes the instructed task, not the exact physical onset or end of
the participant's movement. MIIL is intended for steady-state, offline
classification after transition regions have been trimmed.

## Code rules

MIIL reserves two codes:

| Code | Meaning | Raw data retained? | Default offline use |
| ---: | --- | --- | --- |
| `-1` | `drop_stimulus`: the current interval is invalid | Yes | Exclude |
| `0` | `no_stimulus`: intentionally unlabelled or buffer data | Yes | Exclude |
| Positive integer | A configured instruction such as Rest or Knee Flexion | Yes | Include after trimming |

Every configured action code must be a unique positive integer. Do not assign
`-1` or `0` to a normal action. The action-to-code mapping is saved in capture
metadata, so downstream analysis should read that mapping instead of assuming
that a particular positive code always means the same action.

## Configure MIIL before recording

Configure a new experiment only while acquisition is stopped:

1. Open the Stimulus window and select **Manual Instruction Interval Labeling
   (MIIL)** as the paradigm.
2. Add or edit the required action names and their unique positive integer
   codes.
3. If you edited the action list, select **Apply**. This commits the codebook
   for the next recording; Start is blocked while edits remain unapplied.
4. Review the action buttons before starting acquisition.

The codebook is frozen during an active or paused recording. This prevents an
action from changing meaning partway through an experiment.

The default codebook is pre-applied, so it can be used without an extra Apply
click when no fields have been edited:

| Action | Code |
| --- | ---: |
| Rest | `1` |
| Knee Flexion | `2` |
| Knee Extension | `3` |

These defaults are only a starting point; the applied codebook saved with each
experiment is authoritative.

## Optional: configure MIIL Guided Sequence

Guided Sequence is a MIIL sub-mode for a regular, repeated test. Configure it
only after the MIIL action configuration has been applied and while acquisition
is stopped:

1. Enable **MIIL Guided Sequence**. Its collapsible plan panel expands only
   while the option is enabled.
2. Add the applied MIIL actions to the plan in the required order. Reorder or
   remove steps until the displayed pattern matches the experiment.
3. Set **Repeat** to the number of complete groups. For example,
   `(Rest -> Knee Extension -> Rest -> Knee Flexion), Repeat = 5` produces
   four planned steps per group and twenty planned steps in total.
4. Apply the Guided Sequence plan before Start. The UI reports the steps per
   group, group count, and total planned steps.

The plan stores only action order and repeat count. It has no duration field,
countdown, or automatic transition. Expected 5--10 second actions remain under
operator control: hold the action for the required experimental duration, then
press Enter.

Every planned code must exist in the applied MIIL action configuration.
Adjacent steps cannot use the same code. This also applies across a repeated
group boundary: when Repeat is greater than one, the last code in a group must
differ from the first code in the next group. Without a code change, the
boundary would be ambiguous in the saved `stimulus_code` series.

The applied action configuration and Guided Sequence plan are frozen for the
capture. Start is blocked when Guided Sequence is enabled but its edited plan
has not been applied.

## Start and operate a recording

MIIL and acquisition share one recording lifecycle. There is no separate
serial or Bluetooth lifecycle in MIIL.

- **Acquisition Start** starts all configured sources. When every required
  source is ready, MIIL starts automatically at `no_stimulus` (`0`).
- **Stimulus Start** is a shortcut into the same acquisition start path. It
  does not create a second session or start devices independently.
- Select an action button when the participant should begin following that
  instruction. The selection closes the previous interval and opens the new
  one.
- Select **No Stimulus** for an unlabelled buffer period that should remain in
  the raw recording.
- Select **Drop Current Interval** when the current action interval is known to
  be unusable.
- **Pause**, **Resume**, and **Stop** use the normal acquisition controls and
  apply to the entire recording.

The current-action display shows the active instruction prominently. The
history panel shows completed interval durations and the elapsed time of the
current interval, and follows the latest entry automatically.

MIIL does not impose a 5-second or 10-second action limit. An action remains
active until the operator selects another action, No Stimulus, Drop, or stops
the recording. The expected 5--10 second steady-state intervals therefore need
no special timer configuration.

## Run MIIL Guided Sequence (Enter-to-Advance)

When Guided Sequence is enabled, use this runtime sequence:

1. Select Start through either the Acquisition or Stimulus controls.
2. After all devices are ready, acquisition begins at `no_stimulus` (`0`). The
   first planned action has not started yet.
3. Press Enter once to start the first planned action.
4. Hold the action for the experimenter's chosen duration. Press Enter again
   to close it and start the next planned action.
5. Continue until the final planned action is being performed.
6. After holding the final action, press Enter once more. This final Enter
   closes the action, changes MIIL to `no_stimulus`, marks the plan complete,
   and pauses the complete acquisition session.

The final action is not complete merely because it is displayed as the last
step; it always needs the extra Enter that closes its interval.

Manual MIIL action buttons are disabled while Guided Sequence controls the
capture. This prevents an unplanned button selection from silently changing
the action order or progress count. No Stimulus and Drop Current Interval
remain available with the special rules below.

The Guided Sequence display shows the current action and code prominently,
the current group and step, completed steps versus total steps, current action
elapsed time, and the next expected action. Its progress bar advances only
when a planned step is completed. A buffer or dropped attempt is shown as a
status condition rather than as extra planned progress.

### Enter-key safety

Both the main Enter and numeric-keypad Enter keys can advance the plan. One
physical key press advances at most once; holding Enter cannot skip several
steps. Keep the Stimulus window open while operating the sequence; it may stay
visible while the Plot window has focus. Enter is ignored for Guided Sequence
when:

- the Stimulus window is closed or hidden;
- the sequence is not actively waiting for an advance;
- acquisition or Guided Sequence is paused;
- an editable or interactive control has keyboard focus;
- Ctrl, Shift, or Alt is held; or
- the Command Palette is open.

When the Command Palette is open, Enter remains reserved for executing its
selected command. After editing a field or using the Palette, check the
prominent current-action display before returning to the experiment.

## Button behavior and accidental clicks

### Selecting the current action again

Selecting an already active action is ignored. It does not split the interval
or create a short duplicate segment. Selecting No Stimulus while No Stimulus is
already active is handled the same way.

### No Stimulus

No Stimulus opens a normal interval with code `0`. Its rows are preserved in
the sensor CSV files but should normally be excluded from classification. Use
it for an unlabelled preparation, transition, or buffer period that may still
be useful for inspection.

In Guided Sequence, selecting No Stimulus while a normal planned action is
active completes that planned step and enters a code-`0` buffer. The next
planned action does not start automatically; press Enter when the participant
is ready. Selecting No Stimulus when the sequence is already in its initial or
inserted buffer does not consume another step.

If the current attempt has already been dropped, No Stimulus may be used as a
buffer, but the dropped planned step remains pending. The next Enter retries
that same step.

### Drop Current Interval

Drop changes the effective code of the entire current action interval to `-1`,
including samples recorded before the Drop button was pressed. The interval
continues as `-1` until the operator selects a normal action or No Stimulus.

The original action and code, the interval start, and the time at which Drop
was pressed remain in the stimulus audit data. Raw sensor rows are not deleted
or rewritten in memory; the retroactive code is resolved when the capture is
saved.

- Drop while No Stimulus is active is ignored, because code `0` is already
  excluded by default and a click there is likely accidental.
- Drop while the current interval is already dropped is ignored.
- Selecting an action after Drop starts a new valid interval, even when it is
  the same action that was dropped.

In Guided Sequence, Drop does not consume the planned step and does not advance
the progress bar. The entire current attempt becomes `-1`; the next Enter
starts a new attempt at the same group and step. Consequently, Repeat describes
the number of completed planned groups, while the physical number of attempts
may be higher when a step is retried.

Check the status message after an accidental or uncertain click. Ignored
commands are reported and do not create interval fragments.

## Pause, Resume, and Stop

Pause preserves the active action and freezes its elapsed-time display. Samples
received while acquisition is paused are not appended to the capture. Resume
continues the same action interval and does not create a new boundary.

For Guided Sequence, Pause also freezes the plan cursor. Resume returns to the
same active action, buffer, or retry-pending step; it never advances the plan.
After all planned steps are complete, Resume is locked. Save or Stop the
completed experiment instead of starting an unplanned code-`0` tail or
restarting the plan.

Stop closes the current interval and stops the complete acquisition session.
MIIL never stops or disconnects an individual W2 or BWT901 source.

## Saving and preparing the next experiment

Use this order to avoid mixing experiment definitions:

1. Finish the capture, or Pause/Stop it manually. A completed Guided Sequence
   is already paused.
2. Save the capture and confirm the reported output paths.
3. If the completed capture remains paused, Stop it after saving.
4. Only then select or edit the paradigm, codebook, and plan for the next
   experiment.

Saving first is the safest procedure because it keeps the completed
experiment's definition, interval audit, and sensor files together before any
setup for the next participant or task begins.

### Guided Sequence checkpoints

Save is also available while a Guided Sequence is running. In that case the
single Save operation first pauses the complete acquisition session, drains
the data already accepted at the pause boundary, and then saves a **partial
checkpoint**. The session remains paused after saving. Select Resume to
continue at exactly the same Guided Sequence step, or Stop if the checkpoint
is the intended end of the experiment.

A checkpoint does not consume or complete the active planned step. Its
metadata records the plan position and `partial_checkpoint` status at the save
boundary. Saving the same experiment to the same path again overwrites the
earlier checkpoint files; use a different path only when the earlier snapshot
must also be retained.

For a multi-device capture, the experiment directory contains one raw CSV per
populated stream. Both W2 EMG CSVs and BWT901 IMU CSVs include a
`stimulus_code` column. The directory also contains:

- `capture.stimulus.csv`: a readable interval audit containing the effective
  code, original code, action and label, shared start/end time, status,
  duration, and Drop press time when applicable.
- `capture.metadata.json`: capture and stream metadata plus a `stimulus` block
  containing the MIIL paradigm identity, frozen codebook, code semantics,
  interval boundaries, per-stream row cursors, boundary method, and offline
  processing recommendations.

For Guided Sequence captures, the `stimulus.guided_sequence` metadata also
contains the frozen code pattern and repeat count, action names corresponding
to the codes, completed/active/next plan position, retry state, save-time row
cursors, and an attempt audit linked to MIIL event indices. The stimulus
sidecar adds scalar `guided_*` columns to planned-action rows, including group,
step, attempt number, outcome, and how the attempt ended. Raw W2 and BWT901
files retain the same single `stimulus_code` annotation column; plan structure
is not duplicated into every sensor row.

The exact sensor CSV filenames include a filesystem-safe form of the stream ID
when more than one stream is saved, for example
`capture.ble_w2_w2_1_signal.csv` and `capture.bwt901_imu_1_imu.csv`.

## How sample labels are assigned

W2 and BWT901 streams can have different sampling rates, row counts, and clock
drift. At each operator instruction change, MIIL records both:

- the shared capture time, used for the operator timeline and audit; and
- the current next-row index for every stream, used as that stream's label
  boundary when saving.

Consequently, a W2 at approximately 1000 Hz and a BWT901 at approximately
10 Hz do not need equal row counts, and accumulated W2 timestamp drift does not
move later manual boundaries by several samples or seconds. Shared time remains
available as a fallback when a stream did not have a row cursor at a boundary.

This is application-level annotation, not a hardware trigger. A boundary can
still be uncertain by approximately one incoming packet or one queue/UI refresh
(typically around 100 ms in the current setup). Do not interpret the first
labelled sample as an exact movement onset.

## Recommended offline processing

Keep raw files unchanged. Derive analysis data from each positive-code interval
separately:

```text
usable_start = interval_start + start_trim_seconds
usable_end   = interval_end   - end_trim_seconds
```

Initial recommendations are:

| Parameter | Recommendation |
| --- | --- |
| Action start trim | `1.0 s` |
| Action end trim | `0.5--1.0 s` |
| Rest start trim | `0.5--1.0 s` |
| Rest end trim | `0.5 s` |
| Minimum usable continuous duration | `1.5--2.0 s` |
| Window length | `200--300 ms` |
| Window overlap | `50%` |

Exclude codes `0` and `-1` by default. Reject an interval when its duration
after trimming is below the chosen minimum. Create windows within one interval
only; a window must never cross an MIIL interval boundary, even if adjacent
analysis data happens to use the same class code.

For expected 5--10 second instructions, the recommended trims normally leave
enough steady-state data. Still inspect interval duration, packet loss, source
health, and the resulting number of windows. A long instruction is not proof
that the participant performed it correctly; MIIL records the requested task,
not observed compliance.

## Appropriate and inappropriate uses

MIIL is suitable for offline steady-state classification, pilot experiments,
continuous recordings without a hardware trigger, and operator-directed Rest,
Flexion, or Extension tasks.

MIIL is not suitable as the sole timing source for exact EMG onset measurement,
reaction-time studies, kinematic phase localization, millisecond closed-loop
control, or verification that the participant actually completed an action.
Those uses require an appropriate observed event, synchronized hardware
trigger, or another validated timing source.
