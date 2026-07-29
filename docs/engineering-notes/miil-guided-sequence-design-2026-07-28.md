# MIIL Guided Sequence Design (2026-07-28)

## Decision summary

**MIIL Guided Sequence** is the optional Enter-to-Advance sub-mode of Manual
Instruction Interval Labeling. It prepares a compact action-code pattern and a
repeat count, while the operator decides when each transition occurs. It does
not add action durations, timers, automatic instruction changes, or source
control.

The feature remains above acquisition:

```text
Applied MIIL codebook
        |
Guided Sequence plan + runtime cursor
        |
RecordingSession -- one CapturePosition per operator command
        |
MIIL intervals -- per-stream row boundaries and stimulus_code
        |
Existing CSV + stimulus sidecar + metadata persistence
```

Acquisition remains the sole owner of source readiness, Pause, Resume, Stop,
and failure handling. Guided Sequence does not import device, transport,
protocol, capture-store, or GUI concerns. Its pure runtime returns requested
effects; `RecordingSession` applies those effects to MIIL and acquisition.

## Configuration contract

The option is presented as a collapsible MIIL child panel. When disabled, the
panel is collapsed and normal manual MIIL controls are used. When enabled, the
operator arranges applied MIIL actions into one group pattern and selects a
positive repeat count. For example:

```text
(Rest -> Knee Extension -> Rest -> Knee Flexion) x 5 groups
```

The plan controls only order and repetition. Each action is still expected to
last approximately 5--10 seconds in the present experiments, but neither end
of that range is encoded or enforced by the runtime.

Plan validation requires:

- a non-empty pattern;
- a positive integer repeat count;
- codes that exist in the applied MIIL codebook;
- different codes in adjacent pattern positions; and
- when repeating, a different last and first code across the group boundary.

The last rule prevents an invisible same-code transition between groups. It
also keeps simple code-run analysis consistent with the explicit MIIL interval
audit. The applied plan is copied when a capture is prepared, so edits for a
later experiment cannot change the plan saved with an earlier capture.

## Runtime state and transition contract

The progress denominator is:

```text
total_steps = steps_per_group * repeat_count
```

Only completed planned steps increment the numerator. Attempts created by a
Drop retry are audit records, not extra planned steps.

| Current condition | Operator input | Result |
| --- | --- | --- |
| Capture ready at code `0` | First Enter | Start group 1, step 1 |
| Normal planned action | Enter | Complete it and start the next step |
| Final planned action | Enter | Complete it, enter code `0`, mark complete, Pause acquisition |
| Normal planned action | No Stimulus | Complete it, enter code-`0` buffer, wait for Enter |
| Initial/buffer code `0` | No Stimulus | Ignore; do not consume a step |
| Normal planned action | Drop | Mark the whole MIIL interval `-1`; keep the same step pending |
| Dropped step | Enter | Start a new attempt of the same group and step |
| Dropped step | No Stimulus | Enter a buffer; the same step remains pending |
| Any active state | Pause | Freeze MIIL time and plan position |
| Paused active state | Resume | Restore the same action, buffer, or retry state |
| Completed | Resume or Enter | Reject; require Save or Stop |

Manual action selection is rejected at the recording-session boundary while a
Guided capture is active. Disabling only the visible buttons is insufficient,
because a future command or shortcut must not be able to insert an unplanned
action silently. No Stimulus and Drop remain intentional exception controls
with the table's explicit behavior.

The final action requires a final Enter. Merely reaching or displaying the
last action does not complete its interval. On that Enter, one captured
position closes the final action and opens `no_stimulus`; the normal global
Pause then freezes the capture and drains accepted residual data. This keeps
the final positive-code boundary tied to the operator command and assigns any
post-command tail to code `0`.

## Keyboard routing and UI safety

Both Return and numeric-keypad Enter are accepted. The application shell gives
the Command Palette and focused interactive controls priority over the Guided
handler. Modified Enter presses are ignored, and an Enter key must be released
before another advance is accepted. These rules prevent:

- an Enter used to confirm a text, numeric, combo, button, or Palette action
  from advancing the experiment;
- OS key-repeat from skipping several steps; and
- a shortcut with Ctrl, Shift, or Alt from being interpreted as an action
  transition.

The Stimulus window must be visible, but it does not need keyboard focus; an
operator can therefore watch or interact with Plot while the Guided console
remains open. A hidden Stimulus window consumes and ignores Guided Enter input
to prevent blind progression.

The runtime display treats the action label and code as the primary signal. It
also shows group/step position, elapsed action time, next action, and completed
versus total steps. The progress bar represents planned completion only:

- code-`0` buffers display the next pending step without adding progress;
- a dropped attempt displays a retry warning and leaves progress unchanged;
- a retry may increase attempt number but not total planned steps; and
- completion displays a full bar and a locked Resume state.

This display logic does not change MIIL labeling semantics.

## Pause, checkpoint save, and completion

Ordinary Pause preserves the current MIIL interval and Guided cursor. Resume
continues that same state. Completion also pauses acquisition, but the Guided
state is `completed`; Resume is intentionally rejected so that no unplanned
tail or accidental plan restart can be added.

When Save is selected during an active Guided capture, `RecordingSession`
performs a coordinated checkpoint:

1. Pause all acquisition sources through the existing shared lifecycle.
2. Freeze MIIL and Guided state without consuming the current step.
3. Drain pending acquisition events so a queued source failure is not omitted
   from saved health/failure metadata.
4. Save the current buffers, sample-code resolver, MIIL audit, and Guided
   snapshot.
5. Remain paused until the operator explicitly selects Resume or Stop.

The low-level acquisition save guard continues to reject a running save; the
recording-session operation creates the allowed paused boundary first. A
running checkpoint has save status `partial_checkpoint`, and an active attempt
is serialized as `active_at_save` without being completed in the live runner.
Resume therefore returns to the same step.

All saves for one recording use its existing experiment path. Saving again to
that same path replaces the earlier checkpoint files with the later snapshot.
This avoids creating several experiment directories for one continuous
capture, but operators must choose another path if intermediate files need to
be retained independently.

## Persistence contract

The sensor CSV contract does not change: every populated W2 and BWT901 stream
contains one integer `stimulus_code`, and no row is deleted for code `0` or
`-1`.

The existing MIIL metadata gains a `guided_sequence` block. It records:

- mode enablement and `operator_enter_key` advance mode;
- the frozen `pattern_codes`, repeat count, steps per group, and total steps;
- the corresponding action IDs and display labels from the frozen codebook;
- completed, active, next, and retry-pending progress;
- save status such as `completed`, `partial_checkpoint`, `stopped_early`, or
  `aborted`;
- the save-time shared clock and per-stream row counts; and
- each physical attempt, including group, step, code, step-attempt number,
  start/end time, outcome, ending input, and linked MIIL event index.

The MIIL event sidecar remains the readable interval audit. Planned-action rows
receive scalar Guided fields for role, group, step, attempt number, outcome,
and ending input. The linked MIIL row continues to carry authoritative
stimulus code, original code, start/end time, status, and Drop information.
Initial/final/inserted No Stimulus intervals remain ordinary MIIL code-`0`
rows rather than fabricated plan attempts.

The association by MIIL event index avoids duplicating stream row-cursor maps
inside every attempt. Per-stream label boundaries remain authoritative in the
MIIL interval metadata.

## Analysis implications

Guided Sequence automates plan order, not participant behavior. An Enter marks
the experimenter's instruction boundary and does not prove physical onset,
completion, or compliance. Existing MIIL trimming and interval-safe windowing
requirements therefore remain unchanged.

Offline processing should additionally distinguish planned steps from physical
attempts:

- exclude code `0` and `-1` by default;
- treat dropped attempts as invalid even though they occupy plan history;
- expect attempt counts to exceed planned counts when retries occur;
- use group, step, and attempt audit fields when balancing trials; and
- never infer Guided progress solely from sensor row counts or elapsed time.

No action-duration field exists. The current 5--10 second practice is an
operator protocol and should be checked from saved interval durations before
the usual transition trimming is applied.
