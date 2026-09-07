"""Timed fatigue interval-sequence experiment paradigm.

The fatigue paradigm is intentionally independent of acquisition, persistence,
plotting, and GUI code.  A caller preconfigures a sequence of timed intervals.
A session starts in ``no_stimulus``; the participant presses Q (or an equivalent
caller action) to start the pending interval.  Interval end boundaries are
computed from the configured duration, after which the paradigm returns to
``no_stimulus`` and waits for the next explicit start.

Drop semantics preserve audit history.  Dropping an active interval invalidates
that whole attempt and returns to the wait before the same planned interval.
Dropping while waiting invalidates the most recently completed interval and
rewinds the plan so that interval can be repeated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable

from assembly.experiment.miil import (
    IDLE_STIMULUS_CODE,
    INVALID_STIMULUS_CODE,
    MIILBoundary,
    capture_host_boundary,
)

FATIGUE_PARADIGM_ID = "fatigue"
FATIGUE_PARADIGM_NAME = "Timed Fatigue Interval Sequence"
NO_STIMULUS_ACTION = "no_stimulus"


class FatigueState(str, Enum):
    IDLE = "idle"
    WAITING = "waiting"
    RUNNING = "running"
    COMPLETE = "complete"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class FatigueIntervalSpec:
    """One planned interval in the preconfigured fatigue sequence."""

    action: str
    label: str
    code: int
    duration_s: float


DEFAULT_FATIGUE_INTERVAL_DURATION_S = 30.0

DEFAULT_FATIGUE_PLAN = (
    FatigueIntervalSpec("cr10_1", "CR10 1 - 非常轻松", 1, DEFAULT_FATIGUE_INTERVAL_DURATION_S),
    FatigueIntervalSpec("cr10_2", "CR10 2 - 轻松，能持续完成", 2, DEFAULT_FATIGUE_INTERVAL_DURATION_S),
    FatigueIntervalSpec("cr10_3", "CR10 3 - 中等用力，开始感觉疲劳", 3, DEFAULT_FATIGUE_INTERVAL_DURATION_S),
    FatigueIntervalSpec("cr10_4", "CR10 4 - 有些用力，疲劳感比较明确", 4, DEFAULT_FATIGUE_INTERVAL_DURATION_S),
    FatigueIntervalSpec("cr10_5", "CR10 5 - 用力，继续完成需要一定努力", 5, DEFAULT_FATIGUE_INTERVAL_DURATION_S),
    FatigueIntervalSpec("cr10_6", "CR10 6 - 很用力，动作开始难以维持", 6, DEFAULT_FATIGUE_INTERVAL_DURATION_S),
    FatigueIntervalSpec("cr10_7", "CR10 7 - 非常用力，只能继续一段时间", 7, DEFAULT_FATIGUE_INTERVAL_DURATION_S),
    FatigueIntervalSpec("cr10_8", "CR10 8 - 极其用力，接近不能继续", 8, DEFAULT_FATIGUE_INTERVAL_DURATION_S),
    FatigueIntervalSpec("cr10_9", "CR10 9 - 几乎达到极限", 9, DEFAULT_FATIGUE_INTERVAL_DURATION_S),
    FatigueIntervalSpec("cr10_10", "CR10 10 - 最大程度用力，无法继续", 10, DEFAULT_FATIGUE_INTERVAL_DURATION_S),
)


@dataclass(frozen=True, slots=True)
class FatigueSegment:
    """One actual timeline segment represented as ``[start, end)``."""

    event_index: int
    kind: str
    action: str
    label: str
    original_code: int
    effective_code: int
    start: MIILBoundary
    end: MIILBoundary | None = None
    status: str = "running"
    interval_index: int | None = None
    attempt: int | None = None
    planned_duration_s: float | None = None
    drop_pressed_at_monotonic_ns: int | None = None

    @property
    def start_monotonic_ns(self) -> int:
        return self.start.host_monotonic_ns

    @property
    def end_monotonic_ns(self) -> int | None:
        return None if self.end is None else self.end.host_monotonic_ns

    def duration_ns(self, current_monotonic_ns: int | None = None) -> int:
        end_ns = self.end_monotonic_ns
        if end_ns is None:
            end_ns = self.start_monotonic_ns if current_monotonic_ns is None else int(current_monotonic_ns)
        return max(0, end_ns - self.start_monotonic_ns)

    def duration_s(self, current_monotonic_ns: int | None = None) -> float:
        return self.duration_ns(current_monotonic_ns) / 1_000_000_000.0


class FatigueController:
    """Own one timed interval plan and its actual execution/audit timeline."""

    def __init__(self, plan: Iterable[FatigueIntervalSpec] = DEFAULT_FATIGUE_PLAN) -> None:
        self._plan: tuple[FatigueIntervalSpec, ...] = ()
        self._segments: list[FatigueSegment] = []
        self._attempt_counts: list[int] = []
        self._cursor = 0
        self._last_completed_segment_index: int | None = None
        self._last_boundary: MIILBoundary | None = None
        self.state = FatigueState.IDLE
        error = self.configure_plan(tuple(plan))
        if error is not None:
            raise ValueError(error)

    @property
    def plan(self) -> tuple[FatigueIntervalSpec, ...]:
        return self._plan

    @property
    def segments(self) -> tuple[FatigueSegment, ...]:
        return tuple(self._segments)

    @property
    def current_segment(self) -> FatigueSegment | None:
        if not self._segments or self._segments[-1].end is not None:
            return None
        return self._segments[-1]

    @property
    def current_interval_index(self) -> int | None:
        segment = self.current_segment
        if self.state is FatigueState.RUNNING and segment is not None:
            return segment.interval_index
        if self.state is FatigueState.WAITING and self._cursor < len(self._plan):
            return self._cursor
        return None

    @property
    def current_code(self) -> int:
        segment = self.current_segment
        return IDLE_STIMULUS_CODE if segment is None else segment.effective_code

    @property
    def current_label(self) -> str:
        if self.state is FatigueState.RUNNING:
            segment = self.current_segment
            return "No Stimulus" if segment is None else segment.label
        if self.state is FatigueState.WAITING:
            return "No Stimulus"
        if self.state is FatigueState.COMPLETE:
            return "Protocol Complete"
        return "No Stimulus"

    @property
    def next_interval(self) -> FatigueIntervalSpec | None:
        if self.state is FatigueState.WAITING and self._cursor < len(self._plan):
            return self._plan[self._cursor]
        return None

    def configure_plan(self, plan: Iterable[FatigueIntervalSpec]) -> str | None:
        if self.state in {FatigueState.WAITING, FatigueState.RUNNING}:
            return "Stop the fatigue session before changing its interval plan."

        validated: list[FatigueIntervalSpec] = []
        code_semantics: dict[int, tuple[str, str]] = {}
        action_codes: dict[str, int] = {}
        for value in plan:
            action = str(value.action).strip()
            label = str(value.label).strip()
            if not action:
                return "Fatigue interval action cannot be empty."
            if not label:
                return "Fatigue interval label cannot be empty."
            if isinstance(value.code, bool):
                return "Fatigue interval code must be a positive integer."
            try:
                code = int(value.code)
            except (TypeError, ValueError):
                return "Fatigue interval code must be a positive integer."
            if code != value.code or code <= IDLE_STIMULUS_CODE:
                return "Fatigue interval code must be a positive integer."
            try:
                duration_s = float(value.duration_s)
            except (TypeError, ValueError):
                return "Fatigue interval duration must be a positive finite number."
            if not math.isfinite(duration_s) or duration_s <= 0:
                return "Fatigue interval duration must be a positive finite number."

            normalized_action = action.casefold()
            existing_semantics = code_semantics.get(code)
            semantics = (normalized_action, label.casefold())
            if existing_semantics is not None and existing_semantics != semantics:
                return f"Fatigue code {code} is assigned to inconsistent actions/labels."
            existing_code = action_codes.get(normalized_action)
            if existing_code is not None and existing_code != code:
                return f"Fatigue action '{action}' is assigned to multiple codes."

            code_semantics[code] = semantics
            action_codes[normalized_action] = code
            validated.append(FatigueIntervalSpec(action, label, code, duration_s))

        if not validated:
            return "Fatigue interval plan cannot be empty."

        self._plan = tuple(validated)
        self._attempt_counts = [0] * len(validated)
        return None

    def reset_timeline(self) -> str | None:
        if self.state in {FatigueState.WAITING, FatigueState.RUNNING}:
            return "Stop the fatigue session before resetting its timeline."
        self.state = FatigueState.IDLE
        self._segments = []
        self._attempt_counts = [0] * len(self._plan)
        self._cursor = 0
        self._last_completed_segment_index = None
        self._last_boundary = None
        return None

    def start(self, boundary: MIILBoundary) -> str:
        if self.state in {FatigueState.WAITING, FatigueState.RUNNING}:
            return "Fatigue session is already active."
        if not self._plan:
            return "Fatigue interval plan is empty."

        self._segments = []
        self._attempt_counts = [0] * len(self._plan)
        self._cursor = 0
        self._last_completed_segment_index = None
        self._last_boundary = boundary
        self.state = FatigueState.WAITING
        self._open_no_stimulus(boundary)
        return "Fatigue session started with no_stimulus. Press Q to start interval 1."

    def start_next(self, boundary: MIILBoundary) -> str:
        if self.state is not FatigueState.WAITING:
            if self.state is FatigueState.RUNNING:
                return "Current fatigue interval is already running."
            if self.state is FatigueState.COMPLETE:
                return "Fatigue protocol is already complete."
            return "Fatigue session is not waiting for an interval start."
        if self._cursor >= len(self._plan):
            self.state = FatigueState.COMPLETE
            return "Fatigue protocol is already complete."

        normalized = self._normalized_boundary(boundary)
        self._close_current(normalized, "completed")
        spec = self._plan[self._cursor]
        self._attempt_counts[self._cursor] += 1
        attempt = self._attempt_counts[self._cursor]
        self._open_interval(self._cursor, attempt, spec, normalized)
        self._last_completed_segment_index = None
        self.state = FatigueState.RUNNING
        return (
            f"Started interval {self._cursor + 1}/{len(self._plan)}: "
            f"{spec.label} for {spec.duration_s:g} s."
        )

    def update(self, boundary: MIILBoundary) -> str | None:
        """Advance one running interval when its exact planned end has been reached."""

        if self.state is not FatigueState.RUNNING:
            return None
        segment = self.current_segment
        if segment is None or segment.planned_duration_s is None:
            raise RuntimeError("Running fatigue state has no active planned interval.")

        duration_ns = int(round(segment.planned_duration_s * 1_000_000_000.0))
        scheduled_end_ns = segment.start_monotonic_ns + duration_ns
        if boundary.host_monotonic_ns < scheduled_end_ns:
            return None

        scheduled_end = MIILBoundary(
            host_monotonic_ns=scheduled_end_ns,
            host_unix_ns=segment.start.host_unix_ns + duration_ns,
        )
        self._close_current(scheduled_end, "completed")
        self._last_completed_segment_index = len(self._segments) - 1
        completed_number = self._cursor + 1
        completed_label = segment.label
        self._cursor += 1
        self._open_no_stimulus(scheduled_end)
        if self._cursor >= len(self._plan):
            self.state = FatigueState.COMPLETE
            return f"Interval {completed_number} ({completed_label}) completed. Protocol complete."

        self.state = FatigueState.WAITING
        return (
            f"Interval {completed_number} ({completed_label}) completed. "
            f"Press Q to start interval {self._cursor + 1}."
        )

    def drop(self, boundary: MIILBoundary) -> str:
        if self.state is FatigueState.RUNNING:
            segment = self.current_segment
            if segment is None or segment.interval_index is None:
                return "Fatigue has no active planned interval to drop."
            normalized = self._normalized_boundary(boundary)
            self._segments[-1] = replace(
                segment,
                effective_code=INVALID_STIMULUS_CODE,
                end=normalized,
                status="dropped",
                drop_pressed_at_monotonic_ns=normalized.host_monotonic_ns,
            )
            interval_index = segment.interval_index
            self._last_boundary = normalized
            self._open_no_stimulus(normalized)
            self._cursor = interval_index
            self._last_completed_segment_index = None
            self.state = FatigueState.WAITING
            return (
                f"Dropped interval {interval_index + 1} attempt {segment.attempt}; "
                "press Q to retry it."
            )

        if self.state in {FatigueState.WAITING, FatigueState.COMPLETE}:
            index = self._last_completed_segment_index
            if index is None:
                return "No just-completed fatigue interval is available to drop."
            normalized = self._normalized_boundary(boundary)
            segment = self._segments[index]
            if segment.interval_index is None or segment.effective_code == INVALID_STIMULUS_CODE:
                self._last_boundary = normalized
                return "No just-completed fatigue interval is available to drop."
            self._segments[index] = replace(
                segment,
                effective_code=INVALID_STIMULUS_CODE,
                status="dropped",
                drop_pressed_at_monotonic_ns=normalized.host_monotonic_ns,
            )
            self._cursor = segment.interval_index
            self._last_completed_segment_index = None
            self._last_boundary = normalized
            self.state = FatigueState.WAITING
            return (
                f"Dropped completed interval {segment.interval_index + 1} attempt {segment.attempt}; "
                "press Q to retry it."
            )

        return "Fatigue session has no interval available to drop."

    def stop(self, boundary: MIILBoundary) -> str:
        if self.state in {FatigueState.IDLE, FatigueState.STOPPED}:
            return "Fatigue session is not active."

        if self.state is FatigueState.RUNNING:
            update_message = self.update(boundary)
            if update_message is None:
                normalized = self._normalized_boundary(boundary)
                self._close_current(normalized, "stopped")
            # If update completed the interval, current segment is now no_stimulus.
            if self.current_segment is not None and self.current_segment.end is None:
                self._close_current(self._normalized_boundary(boundary), "stopped")
        else:
            self._close_current(self._normalized_boundary(boundary), "stopped")

        self.state = FatigueState.STOPPED
        return "Fatigue session stopped."

    def current_elapsed_s(self, current_monotonic_ns: int | None = None) -> float:
        if self.state is not FatigueState.RUNNING:
            return 0.0
        segment = self.current_segment
        if segment is None or segment.planned_duration_s is None:
            return 0.0
        current_ns = segment.start_monotonic_ns if current_monotonic_ns is None else int(current_monotonic_ns)
        elapsed = max(0.0, (current_ns - segment.start_monotonic_ns) / 1_000_000_000.0)
        return min(segment.planned_duration_s, elapsed)

    def progress_fraction(self, current_monotonic_ns: int | None = None) -> float:
        segment = self.current_segment
        if self.state is not FatigueState.RUNNING or segment is None or not segment.planned_duration_s:
            return 0.0
        return min(1.0, max(0.0, self.current_elapsed_s(current_monotonic_ns) / segment.planned_duration_s))

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
                    "interval_number": (
                        None if segment.interval_index is None else segment.interval_index + 1
                    ),
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
                    "drop_pressed_at_monotonic_ns": segment.drop_pressed_at_monotonic_ns,
                }
            )
        return rows

    def metadata_snapshot(self) -> dict[str, object]:
        codebook: dict[int, FatigueIntervalSpec] = {}
        for spec in self._plan:
            codebook.setdefault(spec.code, spec)
        return {
            "paradigm": FATIGUE_PARADIGM_ID,
            "paradigm_name": FATIGUE_PARADIGM_NAME,
            "state": self.state.value,
            "boundary_method": "shared_host_monotonic_clock",
            "automatic_end_method": "planned_start_plus_duration",
            "code_semantics": {
                str(INVALID_STIMULUS_CODE): "drop_stimulus",
                str(IDLE_STIMULUS_CODE): NO_STIMULUS_ACTION,
                "positive": "configured fatigue interval action",
            },
            "codebook": [
                {"action": spec.action, "label": spec.label, "stimulus_code": code}
                for code, spec in sorted(codebook.items())
            ],
            "plan": [
                {
                    "interval_number": index + 1,
                    "action": spec.action,
                    "label": spec.label,
                    "stimulus_code": spec.code,
                    "duration_s": spec.duration_s,
                }
                for index, spec in enumerate(self._plan)
            ],
            "segments": self.event_log_rows(),
        }

    def _open_no_stimulus(self, boundary: MIILBoundary) -> None:
        self._segments.append(
            FatigueSegment(
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

    def _open_interval(
        self,
        interval_index: int,
        attempt: int,
        spec: FatigueIntervalSpec,
        boundary: MIILBoundary,
    ) -> None:
        self._segments.append(
            FatigueSegment(
                event_index=len(self._segments) + 1,
                kind="planned_interval",
                interval_index=interval_index,
                attempt=attempt,
                action=spec.action,
                label=spec.label,
                original_code=spec.code,
                effective_code=spec.code,
                planned_duration_s=spec.duration_s,
                start=boundary,
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
                "Fatigue boundary host_monotonic_ns cannot move backwards: "
                f"{boundary.host_monotonic_ns} < {floor}."
            )
        return boundary


__all__ = [
    "DEFAULT_FATIGUE_PLAN",
    "FATIGUE_PARADIGM_ID",
    "FATIGUE_PARADIGM_NAME",
    "FatigueController",
    "FatigueIntervalSpec",
    "FatigueSegment",
    "FatigueState",
    "capture_host_boundary",
]
