"""Pure state model for MIIL's optional Enter-driven guided sequence.

The controller owns only a compact plan and its runtime cursor.  It does not
import the MIIL model, acquisition controller, or GUI.  Instead, every
operator operation returns a :class:`GuidedSequenceCommand` describing the
effect that the recording-session coordinator should apply to MIIL and (for a
completed plan) to acquisition.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable


class GuidedSequenceState(str, Enum):
    """Configuration and runtime phases of one guided sequence."""

    UNCONFIGURED = "unconfigured"
    READY = "ready"
    WAITING_FIRST = "waiting_first"
    ACTIVE = "active"
    BUFFER = "buffer"
    RETRY_PENDING = "retry_pending"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"
    ABORTED = "aborted"


class GuidedAttemptStatus(str, Enum):
    """Outcome of one attempt at a planned step."""

    ACTIVE = "active"
    COMPLETED = "completed"
    DROPPED = "dropped"
    STOPPED = "stopped"
    ABORTED = "aborted"


@dataclass(frozen=True)
class GuidedSequencePlan:
    """One action-code pattern repeated a fixed number of groups."""

    pattern: tuple[int, ...]
    repeat_count: int

    @property
    def total_steps(self) -> int:
        return len(self.pattern) * self.repeat_count

    def step(self, flat_index: int) -> "GuidedSequenceStep":
        if flat_index < 0 or flat_index >= self.total_steps:
            raise IndexError("Guided sequence step index is out of range.")
        pattern_length = len(self.pattern)
        return GuidedSequenceStep(
            flat_index=flat_index,
            group_number=(flat_index // pattern_length) + 1,
            step_number=(flat_index % pattern_length) + 1,
            code=self.pattern[flat_index % pattern_length],
        )


@dataclass(frozen=True)
class GuidedSequenceStep:
    """One addressed occurrence in the repeated plan."""

    flat_index: int
    group_number: int
    step_number: int
    code: int


@dataclass(frozen=True)
class GuidedSequenceAttempt:
    """Audit record for one actual attempt at a planned step."""

    attempt_index: int
    flat_step_index: int
    group_number: int
    step_number: int
    code: int
    step_attempt_number: int
    status: GuidedAttemptStatus = GuidedAttemptStatus.ACTIVE
    started_at_s: float | None = None
    ended_at_s: float | None = None
    ended_by: str | None = None
    miil_event_index: int | None = None


@dataclass(frozen=True)
class GuidedSequenceCommand:
    """Effects requested by one accepted or ignored state-machine input."""

    accepted: bool
    state: GuidedSequenceState
    message: str
    select_action_code: int | None = None
    select_no_stimulus: bool = False
    drop_current_interval: bool = False
    pause_acquisition: bool = False

    @property
    def has_miil_effect(self) -> bool:
        return (
            self.select_action_code is not None
            or self.select_no_stimulus
            or self.drop_current_interval
        )


_RUNNING_STATES = {
    GuidedSequenceState.WAITING_FIRST,
    GuidedSequenceState.ACTIVE,
    GuidedSequenceState.BUFFER,
    GuidedSequenceState.RETRY_PENDING,
}


class GuidedSequenceController:
    """Run an operator-paced action plan without assigning action durations."""

    def __init__(self) -> None:
        self.state = GuidedSequenceState.UNCONFIGURED
        self._plan: GuidedSequencePlan | None = None
        self._next_step_index = 0
        self._active_step_index: int | None = None
        self._retry_step_index: int | None = None
        self._resume_state: GuidedSequenceState | None = None
        self._attempts: list[GuidedSequenceAttempt] = []
        self._active_attempt_list_index: int | None = None
        self._attempt_counts: dict[int, int] = {}
        self._completed_step_count = 0
        self._last_event_time_s: float | None = None

    @property
    def plan(self) -> GuidedSequencePlan | None:
        return self._plan

    @property
    def attempts(self) -> tuple[GuidedSequenceAttempt, ...]:
        return tuple(self._attempts)

    @property
    def total_steps(self) -> int:
        return 0 if self._plan is None else self._plan.total_steps

    @property
    def completed_step_count(self) -> int:
        return self._completed_step_count

    @property
    def active_step(self) -> GuidedSequenceStep | None:
        if self._plan is None:
            return None
        index = self._active_step_index
        if index is None:
            index = self._retry_step_index
        return None if index is None else self._plan.step(index)

    @property
    def next_step(self) -> GuidedSequenceStep | None:
        if self._plan is None:
            return None
        if self._retry_step_index is not None:
            return self._plan.step(self._retry_step_index)
        if self._next_step_index >= self._plan.total_steps:
            return None
        return self._plan.step(self._next_step_index)

    @property
    def progress_fraction(self) -> float:
        if self.total_steps == 0:
            return 0.0
        if self.state == GuidedSequenceState.COMPLETED:
            return 1.0
        return self._completed_step_count / self.total_steps

    @property
    def retry_pending(self) -> bool:
        return self._retry_step_index is not None

    def bind_active_miil_event(self, event_index: int) -> str | None:
        """Associate the active attempt with the MIIL interval it created."""

        if isinstance(event_index, bool) or not isinstance(event_index, int) or event_index <= 0:
            return "MIIL event index must be a positive integer."
        index = self._active_attempt_list_index
        if index is None:
            return "Guided sequence has no active attempt to bind."
        attempt = self._attempts[index]
        if attempt.miil_event_index is not None:
            return "The active guided attempt already has a MIIL event."
        self._attempts[index] = replace(attempt, miil_event_index=event_index)
        return None

    def apply_plan(
        self,
        pattern: Iterable[int],
        repeat_count: int,
        configured_codes: Iterable[int],
    ) -> str | None:
        """Validate and apply a plan against the current MIIL codebook."""

        if self.state in _RUNNING_STATES or self.state == GuidedSequenceState.PAUSED:
            return "Stop the guided sequence before changing its plan."

        error, plan = _validated_plan(pattern, repeat_count, configured_codes)
        if error is not None:
            return error
        assert plan is not None
        self._plan = plan
        self._clear_runtime()
        self.state = GuidedSequenceState.READY
        return None

    def clear_plan(self) -> str | None:
        if self.state in _RUNNING_STATES or self.state == GuidedSequenceState.PAUSED:
            return "Stop the guided sequence before clearing its plan."
        self._plan = None
        self._clear_runtime()
        self.state = GuidedSequenceState.UNCONFIGURED
        return None

    def reset_runtime(self) -> str | None:
        """Return a stopped/completed plan to READY without changing it."""

        if self.state in _RUNNING_STATES or self.state == GuidedSequenceState.PAUSED:
            return "Stop the guided sequence before resetting it."
        self._clear_runtime()
        self.state = (
            GuidedSequenceState.READY
            if self._plan is not None
            else GuidedSequenceState.UNCONFIGURED
        )
        return None

    def start(self, at_s: float | None = None) -> GuidedSequenceCommand:
        """Begin at no_stimulus and wait for Enter to start step one."""

        if self._plan is None:
            return self._ignored("Apply a guided sequence plan before Start.")
        if self.state in _RUNNING_STATES:
            return self._ignored("Guided sequence is already running.")
        if self.state == GuidedSequenceState.PAUSED:
            return self._ignored("Resume the paused guided sequence instead.")

        self._clear_runtime()
        self._record_time(at_s)
        self.state = GuidedSequenceState.WAITING_FIRST
        return self._accepted(
            "Guided sequence is waiting at no_stimulus; press Enter for the first action."
        )

    def advance(self, at_s: float | None = None) -> GuidedSequenceCommand:
        """Handle one Enter press and request at most one planned action."""

        event_time_s = self._record_time(at_s)
        if self.state == GuidedSequenceState.WAITING_FIRST:
            return self._start_new_step(0, event_time_s)
        if self.state == GuidedSequenceState.RETRY_PENDING:
            assert self._retry_step_index is not None
            return self._start_retry(event_time_s)
        if self.state == GuidedSequenceState.BUFFER:
            if self._retry_step_index is not None:
                return self._start_retry(event_time_s)
            if self._next_step_index < self.total_steps:
                return self._start_new_step(self._next_step_index, event_time_s)
            return self._complete(
                event_time_s,
                select_no_stimulus=False,
                message="Guided sequence completed; acquisition should pause.",
            )
        if self.state == GuidedSequenceState.ACTIVE:
            self._finish_active_attempt(
                GuidedAttemptStatus.COMPLETED,
                event_time_s,
                ended_by="enter",
            )
            self._completed_step_count += 1
            self._active_step_index = None
            if self._next_step_index < self.total_steps:
                return self._start_new_step(self._next_step_index, event_time_s)
            return self._complete(
                event_time_s,
                select_no_stimulus=True,
                message=(
                    "Final guided action completed; switch to no_stimulus and "
                    "pause acquisition."
                ),
            )
        if self.state == GuidedSequenceState.PAUSED:
            return self._ignored("Guided sequence is paused; Enter was ignored.")
        if self.state == GuidedSequenceState.COMPLETED:
            return self._ignored("Guided sequence is already complete.")
        return self._ignored("Guided sequence is not running.")

    def select_no_stimulus(
        self,
        at_s: float | None = None,
    ) -> GuidedSequenceCommand:
        """Insert a code-0 buffer without consuming the next planned step."""

        event_time_s = self._record_time(at_s)
        if self.state == GuidedSequenceState.ACTIVE:
            self._finish_active_attempt(
                GuidedAttemptStatus.COMPLETED,
                event_time_s,
                ended_by="no_stimulus",
            )
            self._completed_step_count += 1
            self._active_step_index = None
            if self._next_step_index >= self.total_steps:
                return self._complete(
                    event_time_s,
                    select_no_stimulus=True,
                    message=(
                        "Final guided action completed with no_stimulus; "
                        "acquisition should pause."
                    ),
                )
            self.state = GuidedSequenceState.BUFFER
            return self._accepted(
                "Entered no_stimulus buffer; Enter will start the next planned action.",
                select_no_stimulus=True,
            )
        if self.state == GuidedSequenceState.RETRY_PENDING:
            self.state = GuidedSequenceState.BUFFER
            return self._accepted(
                "Entered no_stimulus buffer; Enter will retry the dropped step.",
                select_no_stimulus=True,
            )
        if self.state in (GuidedSequenceState.WAITING_FIRST, GuidedSequenceState.BUFFER):
            return self._ignored("no_stimulus is already active; command ignored.")
        if self.state == GuidedSequenceState.PAUSED:
            return self._ignored("Guided sequence is paused; command ignored.")
        return self._ignored("Guided sequence is not running.")

    def drop_current(self, at_s: float | None = None) -> GuidedSequenceCommand:
        """Drop the active attempt and require the same planned step again."""

        event_time_s = self._record_time(at_s)
        if self.state == GuidedSequenceState.ACTIVE:
            assert self._active_step_index is not None
            retry_index = self._active_step_index
            self._finish_active_attempt(
                GuidedAttemptStatus.DROPPED,
                event_time_s,
                ended_by="drop_stimulus",
            )
            self._active_step_index = None
            self._retry_step_index = retry_index
            self.state = GuidedSequenceState.RETRY_PENDING
            step = self._require_plan().step(retry_index)
            return self._accepted(
                (
                    f"Dropped group {step.group_number}, step {step.step_number}; "
                    "Enter will retry it."
                ),
                drop_current_interval=True,
            )
        if self.state == GuidedSequenceState.RETRY_PENDING:
            return self._ignored("The current guided step is already dropped.")
        if self.state in (GuidedSequenceState.WAITING_FIRST, GuidedSequenceState.BUFFER):
            return self._ignored("drop_stimulus is ignored while no_stimulus is active.")
        if self.state == GuidedSequenceState.PAUSED:
            return self._ignored("Guided sequence is paused; command ignored.")
        return self._ignored("Guided sequence is not running.")

    def pause(self) -> GuidedSequenceCommand:
        if self.state not in _RUNNING_STATES:
            return self._ignored("Guided sequence is not running.")
        self._resume_state = self.state
        self.state = GuidedSequenceState.PAUSED
        return self._accepted("Guided sequence paused; its plan position is preserved.")

    def resume(self) -> GuidedSequenceCommand:
        if self.state != GuidedSequenceState.PAUSED or self._resume_state is None:
            return self._ignored("Guided sequence is not paused.")
        self.state = self._resume_state
        self._resume_state = None
        return self._accepted("Guided sequence resumed at the same plan position.")

    def stop(
        self,
        *,
        aborted: bool = False,
        at_s: float | None = None,
    ) -> GuidedSequenceCommand:
        """End runtime, distinguishing an expected stop from an abort/failure."""

        event_time_s = self._record_time(at_s)
        can_abort_completed = aborted and self.state == GuidedSequenceState.COMPLETED
        if (
            self.state not in _RUNNING_STATES
            and self.state != GuidedSequenceState.PAUSED
            and not can_abort_completed
        ):
            return self._ignored("Guided sequence is not active.")
        if self._active_attempt_list_index is not None:
            self._finish_active_attempt(
                GuidedAttemptStatus.ABORTED if aborted else GuidedAttemptStatus.STOPPED,
                event_time_s,
                ended_by="acquisition_failure" if aborted else "stop",
            )
            self._active_step_index = None
        self._resume_state = None
        self.state = (
            GuidedSequenceState.ABORTED if aborted else GuidedSequenceState.STOPPED
        )
        return self._accepted(
            "Guided sequence aborted." if aborted else "Guided sequence stopped."
        )

    def metadata_snapshot(self) -> dict[str, object]:
        """Return compact JSON-safe plan, cursor, and attempt audit metadata."""

        plan = self._plan
        active = self.active_step
        next_step = self.next_step
        return {
            "mode": "guided_sequence",
            "state": self.state.value,
            "plan": None
            if plan is None
            else {
                "pattern_codes": list(plan.pattern),
                "repeat_count": plan.repeat_count,
                "steps_per_group": len(plan.pattern),
                "total_steps": plan.total_steps,
            },
            "progress": {
                "completed_steps": self._completed_step_count,
                "total_steps": self.total_steps,
                "active_flat_step_index": (
                    None if active is None else active.flat_index
                ),
                "next_flat_step_index": (
                    None if next_step is None else next_step.flat_index
                ),
                "retry_pending": self.retry_pending,
            },
            "attempts": [
                {
                    "attempt_index": attempt.attempt_index,
                    "flat_step_index": attempt.flat_step_index,
                    "group_number": attempt.group_number,
                    "step_number": attempt.step_number,
                    "stimulus_code": attempt.code,
                    "step_attempt_number": attempt.step_attempt_number,
                    "status": attempt.status.value,
                    "outcome": _attempt_outcome(attempt),
                    "started_at_s": attempt.started_at_s,
                    "ended_at_s": attempt.ended_at_s,
                    "ended_by": attempt.ended_by,
                    "miil_event_index": attempt.miil_event_index,
                }
                for attempt in self._attempts
            ],
        }

    def _start_new_step(
        self,
        flat_index: int,
        at_s: float | None,
    ) -> GuidedSequenceCommand:
        step = self._require_plan().step(flat_index)
        self._active_step_index = flat_index
        self._retry_step_index = None
        self._next_step_index = flat_index + 1
        self._open_attempt(step, at_s)
        self.state = GuidedSequenceState.ACTIVE
        return self._accepted(
            (
                f"Started group {step.group_number}, step {step.step_number}, "
                f"stimulus code {step.code}."
            ),
            select_action_code=step.code,
        )

    def _start_retry(self, at_s: float | None) -> GuidedSequenceCommand:
        assert self._retry_step_index is not None
        step_index = self._retry_step_index
        step = self._require_plan().step(step_index)
        self._active_step_index = step_index
        self._retry_step_index = None
        self._open_attempt(step, at_s)
        self.state = GuidedSequenceState.ACTIVE
        return self._accepted(
            (
                f"Retrying group {step.group_number}, step {step.step_number}, "
                f"stimulus code {step.code}."
            ),
            select_action_code=step.code,
        )

    def _complete(
        self,
        at_s: float | None,
        *,
        select_no_stimulus: bool,
        message: str,
    ) -> GuidedSequenceCommand:
        self._record_time(at_s)
        self._active_step_index = None
        self._retry_step_index = None
        self._resume_state = None
        self.state = GuidedSequenceState.COMPLETED
        return self._accepted(
            message,
            select_no_stimulus=select_no_stimulus,
            pause_acquisition=True,
        )

    def _open_attempt(
        self,
        step: GuidedSequenceStep,
        at_s: float | None,
    ) -> None:
        step_attempt_number = self._attempt_counts.get(step.flat_index, 0) + 1
        self._attempt_counts[step.flat_index] = step_attempt_number
        self._attempts.append(
            GuidedSequenceAttempt(
                attempt_index=len(self._attempts) + 1,
                flat_step_index=step.flat_index,
                group_number=step.group_number,
                step_number=step.step_number,
                code=step.code,
                step_attempt_number=step_attempt_number,
                started_at_s=at_s,
            )
        )
        self._active_attempt_list_index = len(self._attempts) - 1

    def _finish_active_attempt(
        self,
        status: GuidedAttemptStatus,
        at_s: float | None,
        *,
        ended_by: str,
    ) -> None:
        index = self._active_attempt_list_index
        if index is None:
            raise RuntimeError("Guided sequence has no active attempt to finish.")
        self._attempts[index] = replace(
            self._attempts[index],
            status=status,
            ended_at_s=at_s,
            ended_by=ended_by,
        )
        self._active_attempt_list_index = None

    def _clear_runtime(self) -> None:
        self._next_step_index = 0
        self._active_step_index = None
        self._retry_step_index = None
        self._resume_state = None
        self._attempts = []
        self._active_attempt_list_index = None
        self._attempt_counts = {}
        self._completed_step_count = 0
        self._last_event_time_s = None

    def _record_time(self, value: float | None) -> float | None:
        if value is None:
            return None
        time_s = float(value)
        if not math.isfinite(time_s) or time_s < 0.0:
            raise ValueError("Guided sequence event time must be finite and non-negative.")
        if self._last_event_time_s is not None and time_s < self._last_event_time_s:
            raise ValueError("Guided sequence event time cannot move backwards.")
        self._last_event_time_s = time_s
        return time_s

    def _require_plan(self) -> GuidedSequencePlan:
        if self._plan is None:
            raise RuntimeError("Guided sequence plan is not configured.")
        return self._plan

    def _accepted(self, message: str, **effects: object) -> GuidedSequenceCommand:
        return GuidedSequenceCommand(True, self.state, message, **effects)

    def _ignored(self, message: str) -> GuidedSequenceCommand:
        return GuidedSequenceCommand(False, self.state, message)


def _validated_plan(
    pattern: Iterable[int],
    repeat_count: int,
    configured_codes: Iterable[int],
) -> tuple[str | None, GuidedSequencePlan | None]:
    codes, error = _positive_integer_tuple(configured_codes, "Configured action code")
    if error is not None:
        return error, None
    if len(set(codes)) != len(codes):
        return "Configured MIIL action codes must be unique.", None

    planned, error = _positive_integer_tuple(pattern, "Guided sequence code")
    if error is not None:
        return error, None
    if not planned:
        return "Guided sequence pattern cannot be empty.", None
    if isinstance(repeat_count, bool) or not isinstance(repeat_count, int):
        return "Guided sequence repeat count must be a positive integer.", None
    if repeat_count <= 0:
        return "Guided sequence repeat count must be a positive integer.", None

    available = set(codes)
    unknown = [code for code in planned if code not in available]
    if unknown:
        unique_unknown = ", ".join(str(code) for code in dict.fromkeys(unknown))
        return f"Guided sequence uses unconfigured action code(s): {unique_unknown}.", None

    for previous, current in zip(planned, planned[1:]):
        if current == previous:
            return (
                f"Adjacent guided steps cannot use the same code ({current}); "
                "MIIL would not create a new interval.",
                None,
            )
    if repeat_count > 1 and planned[-1] == planned[0]:
        return (
            f"The last and first guided codes cannot both be {planned[0]} when "
            "the pattern repeats; the group boundary would be ambiguous.",
            None,
        )

    return None, GuidedSequencePlan(planned, repeat_count)


def _positive_integer_tuple(
    values: Iterable[int],
    label: str,
) -> tuple[tuple[int, ...], str | None]:
    normalized: list[int] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return (), f"{label}s must be positive integers."
        normalized.append(value)
    return tuple(normalized), None


def _attempt_outcome(attempt: GuidedSequenceAttempt) -> str:
    if attempt.status == GuidedAttemptStatus.ACTIVE:
        return "active_at_save"
    if attempt.status == GuidedAttemptStatus.COMPLETED:
        return f"completed_by_{attempt.ended_by or 'unknown'}"
    return attempt.status.value
