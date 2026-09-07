"""Term-based fatigue evaluation experiment paradigm.

A session begins in no_stimulus. Each term is configured immediately before it
starts and may have either a positive planned duration or no duration. Timed
terms end automatically at their exact planned boundary, while T may end any
running term early. Q records a point action event without changing the active
term interval. After each valid term, the session returns to no_stimulus so the
participant can provide a CR10 rating and the operator can configure the next
term or finish the protocol.

The controller is independent of acquisition, persistence, plotting, and GUI.
All boundaries use the same host monotonic clock domain as normalized assembly
stream rows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum

from assembly.experiment.miil import (
    IDLE_STIMULUS_CODE,
    INVALID_STIMULUS_CODE,
    MIILBoundary,
    capture_host_boundary,
)

FATIGUE_EVALUATION_PARADIGM_ID = "fatigue_evaluation"
FATIGUE_EVALUATION_PARADIGM_NAME = "Term-Based Fatigue Evaluation"
NO_STIMULUS_ACTION = "no_stimulus"

CR10_REFERENCE: tuple[tuple[int, str], ...] = (
    (1, "Very easy"),
    (2, "Easy; can continue comfortably"),
    (3, "Moderate effort; fatigue begins"),
    (4, "Somewhat hard; fatigue is noticeable"),
    (5, "Hard; continuing requires clear effort"),
    (6, "Very hard; movement becomes difficult to maintain"),
    (7, "Extremely hard; can continue only briefly"),
    (8, "Near the limit; almost unable to continue"),
    (9, "Almost maximal effort"),
    (10, "Maximal effort; unable to continue"),
)
CR10_LABEL_BY_SCORE = dict(CR10_REFERENCE)


class FatigueEvaluationState(str, Enum):
    IDLE = "idle"
    READY = "ready"
    RUNNING = "running"
    EVALUATING = "evaluating"
    COMPLETE = "complete"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class FatigueEvaluationTermSpec:
    """Configuration for one term before it starts."""

    name: str
    duration_s: float | None = None


@dataclass(frozen=True, slots=True)
class FatigueEvaluationSegment:
    """One actual no_stimulus or term segment represented as [start, end)."""

    event_index: int
    kind: str
    action: str
    label: str
    original_code: int
    effective_code: int
    start: MIILBoundary
    end: MIILBoundary | None = None
    status: str = "running"
    term_number: int | None = None
    attempt: int | None = None
    planned_duration_s: float | None = None
    end_method: str | None = None
    action_events: tuple[MIILBoundary, ...] = ()
    cr10_score: int | None = None
    cr10_label: str | None = None
    drop_pressed_at_monotonic_ns: int | None = None

    @property
    def start_monotonic_ns(self) -> int:
        return self.start.host_monotonic_ns

    @property
    def end_monotonic_ns(self) -> int | None:
        return None if self.end is None else self.end.host_monotonic_ns

    @property
    def action_count(self) -> int:
        return len(self.action_events)

    def duration_ns(self, current_monotonic_ns: int | None = None) -> int:
        end_ns = self.end_monotonic_ns
        if end_ns is None:
            end_ns = self.start_monotonic_ns if current_monotonic_ns is None else int(current_monotonic_ns)
        return max(0, end_ns - self.start_monotonic_ns)

    def duration_s(self, current_monotonic_ns: int | None = None) -> float:
        return self.duration_ns(current_monotonic_ns) / 1_000_000_000.0


class FatigueEvaluationController:
    """Own dynamic term execution, action events, CR10 ratings, and retries."""

    def __init__(self) -> None:
        self._segments: list[FatigueEvaluationSegment] = []
        self._last_boundary: MIILBoundary | None = None
        self._pending_term_number = 1
        self._attempt_counts: dict[int, int] = {}
        self._last_completed_segment_index: int | None = None
        self.state = FatigueEvaluationState.IDLE

    @property
    def segments(self) -> tuple[FatigueEvaluationSegment, ...]:
        return tuple(self._segments)

    @property
    def current_segment(self) -> FatigueEvaluationSegment | None:
        if not self._segments or self._segments[-1].end is not None:
            return None
        return self._segments[-1]

    @property
    def current_term(self) -> FatigueEvaluationSegment | None:
        segment = self.current_segment
        if self.state is FatigueEvaluationState.RUNNING and segment is not None and segment.kind == "term":
            return segment
        return None

    @property
    def pending_term_number(self) -> int:
        return self._pending_term_number

    @property
    def last_completed_term(self) -> FatigueEvaluationSegment | None:
        index = self._last_completed_segment_index
        if index is None:
            return None
        segment = self._segments[index]
        if segment.kind != "term" or segment.status != "completed":
            return None
        return segment

    @property
    def current_code(self) -> int:
        segment = self.current_segment
        return IDLE_STIMULUS_CODE if segment is None else segment.effective_code

    def start_session(self, boundary: MIILBoundary) -> str:
        if self.state in {
            FatigueEvaluationState.READY,
            FatigueEvaluationState.RUNNING,
            FatigueEvaluationState.EVALUATING,
        }:
            return "Fatigue evaluation session is already active."

        self._segments = []
        self._last_boundary = boundary
        self._pending_term_number = 1
        self._attempt_counts = {}
        self._last_completed_segment_index = None
        self.state = FatigueEvaluationState.READY
        self._open_no_stimulus(boundary)
        return "Fatigue evaluation started in no_stimulus. Configure and start term 1."

    def start_term(self, spec: FatigueEvaluationTermSpec, boundary: MIILBoundary) -> str:
        if self.state not in {FatigueEvaluationState.READY, FatigueEvaluationState.EVALUATING}:
            if self.state is FatigueEvaluationState.RUNNING:
                return "A fatigue evaluation term is already running."
            if self.state is FatigueEvaluationState.COMPLETE:
                return "Fatigue evaluation protocol is complete."
            return "Fatigue evaluation session is not ready to start a term."

        validated, error = self.validate_term_spec(spec)
        if error is not None:
            return error
        assert validated is not None

        normalized = self._normalized_boundary(boundary)
        self._close_current(normalized, "completed")
        term_number = self._pending_term_number
        attempt = self._attempt_counts.get(term_number, 0) + 1
        self._attempt_counts[term_number] = attempt
        self._open_term(term_number, attempt, validated, normalized)
        self._last_completed_segment_index = None
        self.state = FatigueEvaluationState.RUNNING
        duration_text = "open-ended" if validated.duration_s is None else f"{validated.duration_s:g} s"
        return f"Started term {term_number} attempt {attempt}: {validated.name} ({duration_text})."

    @staticmethod
    def validate_term_spec(
        spec: FatigueEvaluationTermSpec,
    ) -> tuple[FatigueEvaluationTermSpec | None, str | None]:
        name = str(spec.name).strip()
        if not name:
            return None, "Term name must not be empty."
        if spec.duration_s is None:
            return FatigueEvaluationTermSpec(name, None), None
        if isinstance(spec.duration_s, bool):
            return None, "Term duration must be a positive finite number or left unset."
        try:
            duration_s = float(spec.duration_s)
        except (TypeError, ValueError):
            return None, "Term duration must be a positive finite number or left unset."
        if not math.isfinite(duration_s) or duration_s <= 0:
            return None, "Term duration must be a positive finite number or left unset."
        return FatigueEvaluationTermSpec(name, duration_s), None

    def update(self, boundary: MIILBoundary) -> str | None:
        """Auto-complete a timed term at its exact planned boundary."""

        term = self.current_term
        if term is None or term.planned_duration_s is None:
            return None
        duration_ns = int(round(term.planned_duration_s * 1_000_000_000.0))
        scheduled_end_ns = term.start_monotonic_ns + duration_ns
        if boundary.host_monotonic_ns < scheduled_end_ns:
            return None

        scheduled_end = MIILBoundary(
            host_monotonic_ns=scheduled_end_ns,
            host_unix_ns=term.start.host_unix_ns + duration_ns,
        )
        return self._complete_running_term(scheduled_end, end_method="timer")

    def end_term_manual(self, boundary: MIILBoundary) -> str:
        """End any running term immediately; this is the T-key semantic."""

        if self.state is not FatigueEvaluationState.RUNNING:
            return "No fatigue evaluation term is running."
        auto_message = self.update(boundary)
        if auto_message is not None:
            return auto_message
        return self._complete_running_term(self._normalized_boundary(boundary), end_method="manual_t")

    def record_action(self, boundary: MIILBoundary) -> str:
        """Record one Q point event without changing the current term interval."""

        if self.state is not FatigueEvaluationState.RUNNING:
            return "Q action event ignored because no term is running."
        auto_message = self.update(boundary)
        if auto_message is not None:
            return "Term reached its planned duration before this Q event; action was not counted."
        term = self.current_term
        if term is None:
            return "Q action event ignored because no term is running."
        normalized = self._normalized_boundary(boundary)
        self._segments[-1] = replace(term, action_events=(*term.action_events, normalized))
        self._last_boundary = normalized
        return f"Recorded action {term.action_count + 1} in term {term.term_number}."

    def set_cr10(self, score: int) -> str:
        if self.state is not FatigueEvaluationState.EVALUATING:
            return "CR10 rating is only available after a completed term."
        if isinstance(score, bool):
            return "CR10 score must be an integer from 1 to 10."
        try:
            normalized_score = int(score)
        except (TypeError, ValueError):
            return "CR10 score must be an integer from 1 to 10."
        if normalized_score != score or normalized_score not in CR10_LABEL_BY_SCORE:
            return "CR10 score must be an integer from 1 to 10."
        index = self._last_completed_segment_index
        if index is None:
            return "No completed term is available for CR10 evaluation."
        term = self._segments[index]
        if term.status != "completed":
            return "No completed term is available for CR10 evaluation."
        label = CR10_LABEL_BY_SCORE[normalized_score]
        self._segments[index] = replace(term, cr10_score=normalized_score, cr10_label=label)
        return f"Assigned CR10 {normalized_score} to term {term.term_number}."

    def finish_protocol(self) -> str:
        if self.state not in {FatigueEvaluationState.READY, FatigueEvaluationState.EVALUATING}:
            if self.state is FatigueEvaluationState.COMPLETE:
                return "Fatigue evaluation protocol is already complete."
            if self.state is FatigueEvaluationState.RUNNING:
                return "End the running term before finishing the protocol."
            return "Fatigue evaluation session is not active."
        self.state = FatigueEvaluationState.COMPLETE
        return "Fatigue evaluation protocol complete. Stop the recording session when ready."

    def drop(self, boundary: MIILBoundary) -> str:
        if self.state is FatigueEvaluationState.RUNNING:
            auto_message = self.update(boundary)
            if auto_message is not None:
                return self.drop(boundary)
            term = self.current_term
            if term is None or term.term_number is None:
                return "No running term is available to drop."
            normalized = self._normalized_boundary(boundary)
            self._segments[-1] = replace(
                term,
                effective_code=INVALID_STIMULUS_CODE,
                end=normalized,
                status="dropped",
                end_method="drop",
                cr10_score=None,
                cr10_label=None,
                drop_pressed_at_monotonic_ns=normalized.host_monotonic_ns,
            )
            self._last_boundary = normalized
            self._pending_term_number = term.term_number
            self._last_completed_segment_index = None
            self._open_no_stimulus(normalized)
            self.state = FatigueEvaluationState.EVALUATING
            return f"Dropped term {term.term_number} attempt {term.attempt}; configure and retry it."

        if self.state is FatigueEvaluationState.EVALUATING:
            index = self._last_completed_segment_index
            if index is None:
                return "No just-completed term is available to drop."
            term = self._segments[index]
            if term.term_number is None or term.status != "completed":
                return "No just-completed term is available to drop."
            normalized = self._normalized_boundary(boundary)
            self._segments[index] = replace(
                term,
                effective_code=INVALID_STIMULUS_CODE,
                status="dropped",
                cr10_score=None,
                cr10_label=None,
                drop_pressed_at_monotonic_ns=normalized.host_monotonic_ns,
            )
            self._pending_term_number = term.term_number
            self._last_completed_segment_index = None
            self._last_boundary = normalized
            return f"Dropped completed term {term.term_number} attempt {term.attempt}; configure and retry it."

        return "No current or just-completed term is available to drop."

    def stop_session(self, boundary: MIILBoundary) -> str:
        if self.state in {FatigueEvaluationState.IDLE, FatigueEvaluationState.STOPPED}:
            return "Fatigue evaluation session is not active."
        if self.state is FatigueEvaluationState.RUNNING:
            auto_message = self.update(boundary)
            if auto_message is None:
                normalized = self._normalized_boundary(boundary)
                term = self.current_term
                if term is not None:
                    self._segments[-1] = replace(term, end=normalized, status="stopped", end_method="session_stop")
                    self._last_boundary = normalized
            if self.current_segment is not None and self.current_segment.end is None:
                self._close_current(self._normalized_boundary(boundary), "stopped")
        else:
            self._close_current(self._normalized_boundary(boundary), "stopped")
        self.state = FatigueEvaluationState.STOPPED
        return "Fatigue evaluation session stopped."

    def current_elapsed_s(self, current_monotonic_ns: int | None = None) -> float:
        term = self.current_term
        if term is None:
            return 0.0
        current_ns = term.start_monotonic_ns if current_monotonic_ns is None else int(current_monotonic_ns)
        elapsed = max(0.0, (current_ns - term.start_monotonic_ns) / 1_000_000_000.0)
        if term.planned_duration_s is not None:
            elapsed = min(term.planned_duration_s, elapsed)
        return elapsed

    def progress_fraction(self, current_monotonic_ns: int | None = None) -> float:
        term = self.current_term
        if term is None or term.planned_duration_s is None:
            return 0.0
        return min(1.0, max(0.0, self.current_elapsed_s(current_monotonic_ns) / term.planned_duration_s))

    def code_at(self, host_monotonic_ns: int) -> int:
        timestamp = int(host_monotonic_ns)
        for segment in self._segments:
            end_ns = segment.end_monotonic_ns
            if timestamp >= segment.start_monotonic_ns and (end_ns is None or timestamp < end_ns):
                return segment.effective_code
        return IDLE_STIMULUS_CODE

    def event_log_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        current_ns = None if self._last_boundary is None else self._last_boundary.host_monotonic_ns
        for segment in self._segments:
            rows.append(
                {
                    "event_index": segment.event_index,
                    "kind": segment.kind,
                    "term_number": segment.term_number,
                    "attempt": segment.attempt,
                    "stimulus_code": segment.effective_code,
                    "planned_code": segment.original_code,
                    "action": segment.action,
                    "label": segment.label,
                    "planned_duration_s": segment.planned_duration_s,
                    "start_monotonic_ns": segment.start_monotonic_ns,
                    "end_monotonic_ns": segment.end_monotonic_ns,
                    "start_unix_ns": segment.start.host_unix_ns,
                    "end_unix_ns": None if segment.end is None else segment.end.host_unix_ns,
                    "duration_s": segment.duration_s(current_ns),
                    "status": segment.status,
                    "end_method": segment.end_method,
                    "action_count": segment.action_count,
                    "action_events": [
                        {
                            "host_monotonic_ns": event.host_monotonic_ns,
                            "host_unix_ns": event.host_unix_ns,
                        }
                        for event in segment.action_events
                    ],
                    "cr10_score": segment.cr10_score,
                    "cr10_label": segment.cr10_label,
                    "drop_pressed_at_monotonic_ns": segment.drop_pressed_at_monotonic_ns,
                }
            )
        return rows

    def metadata_snapshot(self) -> dict[str, object]:
        return {
            "schema": "assembly.experiment.fatigue_evaluation",
            "schema_version": 1,
            "paradigm": FATIGUE_EVALUATION_PARADIGM_ID,
            "paradigm_name": FATIGUE_EVALUATION_PARADIGM_NAME,
            "state": self.state.value,
            "alignment_semantics": {
                "key": "host_monotonic_ns",
                "unit": "ns",
                "interval_convention": "[start, end)",
                "membership_rule": "start_monotonic_ns <= sample.host_monotonic_ns < end_monotonic_ns",
                "host_monotonic_ns": "host observation timestamp used as the canonical alignment clock",
                "host_unix_ns": "host wall-clock observation timestamp for audit/reference",
            },
            "code_semantics": {
                "planned_code": "original intended term code before invalidation",
                "stimulus_code": "effective code used for offline alignment",
                "no_stimulus": {
                    "code": IDLE_STIMULUS_CODE,
                    "meaning": "no active fatigue-evaluation term",
                    "is_rest_label": False,
                },
                "dropped": {
                    "code": INVALID_STIMULUS_CODE,
                    "meaning": "whole term attempt was invalidated retrospectively",
                },
                "positive": "logical term number",
            },
            "status_semantics": {
                "completed": "segment completed normally",
                "dropped": "term attempt was explicitly invalidated and is not a valid completed trial",
                "stopped": "segment was interrupted by session termination",
                "running": "segment was still open when metadata was captured",
            },
            "term_semantics": {
                "term_number": "logical sequential term identity",
                "attempt": "execution attempt number for the same logical term; increments on retry",
                "planned_duration_s": "configured term duration; null means open-ended",
                "duration_s": "actual segment duration derived from recorded boundaries",
                "end_method": {
                    "timer": "planned duration reached",
                    "manual_t": "T key ended the running term",
                    "drop": "term attempt was invalidated",
                    "session_stop": "recording session ended while the term was running",
                },
            },
            "action_event_semantics": {
                "type": "manual_point_event",
                "trigger": "keyboard_q",
                "meaning": "manual indication that one action occurred during the active term",
                "timestamp_key": "host_monotonic_ns",
                "changes_term_boundary": False,
                "represents_action_duration": False,
                "automatically_detected": False,
            },
            "cr10_semantics": {
                "scale": "CR10",
                "range": [1, 10],
                "type": "post_term_subjective_fatigue_rating",
                "applies_to": "completed_term",
                "is_samplewise_label": False,
                "null_meaning": "no CR10 rating was assigned to this term",
            },
            "cr10_reference": [
                {"score": score, "description": description}
                for score, description in CR10_REFERENCE
            ],
            "segments": self.event_log_rows(),
        }

    def _complete_running_term(self, boundary: MIILBoundary, *, end_method: str) -> str:
        term = self.current_term
        if term is None or term.term_number is None:
            return "No fatigue evaluation term is running."
        normalized = self._normalized_boundary(boundary)
        self._segments[-1] = replace(
            term,
            end=normalized,
            status="completed",
            end_method=end_method,
        )
        self._last_completed_segment_index = len(self._segments) - 1
        self._pending_term_number = term.term_number + 1
        self._last_boundary = normalized
        self._open_no_stimulus(normalized)
        self.state = FatigueEvaluationState.EVALUATING
        return (
            f"Term {term.term_number} completed by {end_method}; "
            "rate CR10, then configure the next term or finish."
        )

    def _open_no_stimulus(self, boundary: MIILBoundary) -> None:
        self._segments.append(
            FatigueEvaluationSegment(
                event_index=len(self._segments) + 1,
                kind="no_stimulus",
                action=NO_STIMULUS_ACTION,
                label="No Stimulus",
                original_code=IDLE_STIMULUS_CODE,
                effective_code=IDLE_STIMULUS_CODE,
                start=boundary,
            )
        )
        self._last_boundary = boundary

    def _open_term(
        self,
        term_number: int,
        attempt: int,
        spec: FatigueEvaluationTermSpec,
        boundary: MIILBoundary,
    ) -> None:
        self._segments.append(
            FatigueEvaluationSegment(
                event_index=len(self._segments) + 1,
                kind="term",
                action=f"term_{term_number}",
                label=spec.name,
                original_code=term_number,
                effective_code=term_number,
                start=boundary,
                term_number=term_number,
                attempt=attempt,
                planned_duration_s=spec.duration_s,
            )
        )
        self._last_boundary = boundary

    def _close_current(self, boundary: MIILBoundary, status: str) -> MIILBoundary:
        normalized = self._normalized_boundary(boundary)
        segment = self.current_segment
        if segment is not None:
            closed_status = "dropped" if segment.effective_code == INVALID_STIMULUS_CODE else status
            self._segments[-1] = replace(segment, end=normalized, status=closed_status)
        self._last_boundary = normalized
        return normalized

    def _normalized_boundary(self, boundary: MIILBoundary) -> MIILBoundary:
        floor = 0 if self._last_boundary is None else self._last_boundary.host_monotonic_ns
        segment = self.current_segment
        if segment is not None:
            floor = max(floor, segment.start_monotonic_ns)
        if boundary.host_monotonic_ns < floor:
            raise ValueError(
                "Fatigue evaluation boundary host_monotonic_ns cannot move backwards: "
                f"{boundary.host_monotonic_ns} < {floor}."
            )
        return boundary


__all__ = [
    "CR10_REFERENCE",
    "FATIGUE_EVALUATION_PARADIGM_ID",
    "FATIGUE_EVALUATION_PARADIGM_NAME",
    "FatigueEvaluationController",
    "FatigueEvaluationSegment",
    "FatigueEvaluationState",
    "FatigueEvaluationTermSpec",
    "capture_host_boundary",
]
