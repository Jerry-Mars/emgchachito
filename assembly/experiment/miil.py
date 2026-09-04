"""Manual Instruction Interval Labeling (MIIL) domain model.

MIIL is intentionally independent of acquisition, persistence, plotting, and GUI.
Callers provide boundaries in the same host monotonic clock domain used by
normalized acquisition rows. This keeps experiment instruction timing separate
from sensor data while preserving deterministic offline alignment.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable

MIIL_PARADIGM_ID = "miil"
MIIL_PARADIGM_NAME = "Manual Instruction Interval Labeling"

IDLE_STIMULUS_CODE = 0
INVALID_STIMULUS_CODE = -1
NO_STIMULUS_ACTION = "no_stimulus"
DROP_STIMULUS_ACTION = "drop_stimulus"


class MIILState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class MIILAction:
    """One operator-selectable instruction."""

    action: str
    label: str
    code: int


DEFAULT_MIIL_ACTIONS = (
    MIILAction("rest", "Rest", 1),
    MIILAction("knee_flexion", "Knee Flexion", 2),
    MIILAction("knee_extension", "Knee Extension", 3),
)


@dataclass(frozen=True, slots=True)
class MIILBoundary:
    """One experiment boundary in the host clock domain.

    ``host_monotonic_ns`` is the canonical alignment coordinate.
    ``host_unix_ns`` is audit/wall-clock metadata only.
    """

    host_monotonic_ns: int
    host_unix_ns: int

    def __post_init__(self) -> None:
        if self.host_monotonic_ns < 0:
            raise ValueError("host_monotonic_ns must be non-negative.")
        if self.host_unix_ns < 0:
            raise ValueError("host_unix_ns must be non-negative.")


@dataclass(frozen=True, slots=True)
class MIILInterval:
    """One actual operator instruction interval, represented as [start, end)."""

    event_index: int
    action: str
    label: str
    original_code: int
    effective_code: int
    start: MIILBoundary
    end: MIILBoundary | None = None
    status: str = "running"
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


def capture_host_boundary() -> MIILBoundary:
    """Capture a boundary using the host clocks used by assembly acquisition."""

    return MIILBoundary(
        host_monotonic_ns=time.perf_counter_ns(),
        host_unix_ns=time.time_ns(),
    )


class MIILController:
    """Own the MIIL codebook and operator-created interval timeline."""

    def __init__(self, actions: Iterable[MIILAction] = DEFAULT_MIIL_ACTIONS) -> None:
        self._actions: tuple[MIILAction, ...] = ()
        self._intervals: list[MIILInterval] = []
        self._last_boundary: MIILBoundary | None = None
        self.state = MIILState.IDLE
        error = self.configure_actions(tuple(actions))
        if error is not None:
            raise ValueError(error)

    @property
    def actions(self) -> tuple[MIILAction, ...]:
        return self._actions

    @property
    def intervals(self) -> tuple[MIILInterval, ...]:
        return tuple(self._intervals)

    @property
    def current_interval(self) -> MIILInterval | None:
        if not self._intervals or self._intervals[-1].end is not None:
            return None
        return self._intervals[-1]

    @property
    def current_code(self) -> int:
        interval = self.current_interval
        return IDLE_STIMULUS_CODE if interval is None else interval.effective_code

    @property
    def current_action(self) -> str:
        interval = self.current_interval
        return NO_STIMULUS_ACTION if interval is None else interval.action

    @property
    def current_label(self) -> str:
        interval = self.current_interval
        if interval is None:
            return "No Stimulus"
        if interval.effective_code == INVALID_STIMULUS_CODE:
            return f"Drop: {interval.label}"
        return interval.label

    def current_elapsed_s(self, current_monotonic_ns: int | None = None) -> float:
        interval = self.current_interval
        if interval is None:
            return 0.0
        return interval.duration_s(current_monotonic_ns)

    def configure_actions(self, actions: Iterable[MIILAction]) -> str | None:
        """Validate and apply the action codebook while MIIL is inactive."""

        if self.state is MIILState.RUNNING:
            return "Stop MIIL before changing its actions."

        validated: list[MIILAction] = []
        codes: set[int] = set()
        action_ids: set[str] = set()
        for value in actions:
            action = str(value.action).strip()
            label = str(value.label).strip()
            if not action:
                return "MIIL action cannot be empty."
            if not label:
                return "MIIL action label cannot be empty."
            normalized_action = action.casefold()
            if normalized_action in {NO_STIMULUS_ACTION, DROP_STIMULUS_ACTION}:
                return f"MIIL action '{action}' is reserved."
            if isinstance(value.code, bool):
                return "MIIL action code must be a positive integer."
            try:
                code = int(value.code)
            except (TypeError, ValueError):
                return "MIIL action code must be a positive integer."
            if code != value.code or code <= IDLE_STIMULUS_CODE:
                return "MIIL action code must be a positive integer."
            if code in codes:
                return f"MIIL action code {code} is duplicated."
            if normalized_action in action_ids:
                return f"MIIL action '{action}' is duplicated."
            codes.add(code)
            action_ids.add(normalized_action)
            validated.append(MIILAction(action, label, code))

        if not validated:
            return "MIIL action list cannot be empty."

        self._actions = tuple(validated)
        return None

    def reset_timeline(self) -> str | None:
        if self.state is MIILState.RUNNING:
            return "Stop MIIL before resetting its timeline."
        self.state = MIILState.IDLE
        self._intervals = []
        self._last_boundary = None
        return None

    def start(self, boundary: MIILBoundary) -> str:
        if self.state == MIILState.RUNNING:
            return "MIIL is already running."
        if not self._actions:
            return "MIIL action list is empty."

        self._intervals = []
        self._last_boundary = boundary
        self.state = MIILState.RUNNING
        self._open_interval(NO_STIMULUS_ACTION, "No Stimulus", IDLE_STIMULUS_CODE, boundary)
        return "MIIL started with no_stimulus."

    def select_action(self, code: int, boundary: MIILBoundary) -> str:
        if self.state != MIILState.RUNNING:
            return "MIIL is not running."
        action = next((item for item in self._actions if item.code == code), None)
        if action is None:
            return f"MIIL action code {code} is not configured."
        current = self.current_interval
        normalized = self._normalized_boundary(boundary)
        if current is not None and current.effective_code == action.code:
            self._last_boundary = normalized
            return f"MIIL action '{action.label}' is already active; click ignored."

        boundary = self._close_current(normalized, "completed")
        self._open_interval(action.action, action.label, action.code, boundary)
        return f"MIIL action changed to '{action.label}'."

    def select_no_stimulus(self, boundary: MIILBoundary) -> str:
        if self.state != MIILState.RUNNING:
            return "MIIL is not running."
        current = self.current_interval
        normalized = self._normalized_boundary(boundary)
        if current is not None and current.effective_code == IDLE_STIMULUS_CODE:
            self._last_boundary = normalized
            return "no_stimulus is already active; click ignored."

        boundary = self._close_current(normalized, "completed")
        self._open_interval(NO_STIMULUS_ACTION, "No Stimulus", IDLE_STIMULUS_CODE, boundary)
        return "MIIL action changed to no_stimulus."

    def drop_current(self, boundary: MIILBoundary) -> str:
        if self.state != MIILState.RUNNING:
            return "MIIL is not running."
        interval = self.current_interval
        if interval is None:
            return "MIIL has no active interval."
        normalized = self._normalized_boundary(boundary)
        if interval.effective_code == IDLE_STIMULUS_CODE:
            self._last_boundary = normalized
            return "drop_stimulus ignored while no_stimulus is active."
        if interval.effective_code == INVALID_STIMULUS_CODE:
            self._last_boundary = normalized
            return "drop_stimulus is already active; click ignored."

        self._intervals[-1] = replace(
            interval,
            effective_code=INVALID_STIMULUS_CODE,
            status="dropped",
            drop_pressed_at_monotonic_ns=normalized.host_monotonic_ns,
        )
        self._last_boundary = normalized
        return f"Current '{interval.label}' interval was marked drop_stimulus from its beginning."

    def stop(self, boundary: MIILBoundary) -> str:
        if self.state is not MIILState.RUNNING:
            return "MIIL is not running."
        self._close_current(self._normalized_boundary(boundary), "stopped")
        self.state = MIILState.STOPPED
        return "MIIL stopped."

    def code_at(self, host_monotonic_ns: int) -> int:
        """Resolve the instruction code for one host-monotonic timestamp."""

        timestamp = int(host_monotonic_ns)
        for interval in self._intervals:
            end_ns = interval.end_monotonic_ns
            if timestamp >= interval.start_monotonic_ns and (end_ns is None or timestamp < end_ns):
                return interval.effective_code
        return IDLE_STIMULUS_CODE

    def event_log_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for interval in self._intervals:
            rows.append(
                {
                    "event_index": interval.event_index,
                    "stimulus_code": interval.effective_code,
                    "planned_code": interval.original_code,
                    "action": interval.action,
                    "label": interval.label,
                    "start_monotonic_ns": interval.start_monotonic_ns,
                    "end_monotonic_ns": interval.end_monotonic_ns,
                    "start_unix_ns": interval.start.host_unix_ns,
                    "end_unix_ns": None if interval.end is None else interval.end.host_unix_ns,
                    "duration_s": interval.duration_s(self._current_monotonic_ns()),
                    "status": interval.status,
                    "drop_pressed_at_monotonic_ns": interval.drop_pressed_at_monotonic_ns,
                }
            )
        return rows

    def metadata_snapshot(self) -> dict[str, object]:
        """Return JSON-serializable MIIL semantics and interval audit data."""

        return {
            "paradigm": MIIL_PARADIGM_ID,
            "paradigm_name": MIIL_PARADIGM_NAME,
            "state": self.state.value,
            "boundary_method": "shared_host_monotonic_clock",
            "code_semantics": {
                str(INVALID_STIMULUS_CODE): DROP_STIMULUS_ACTION,
                str(IDLE_STIMULUS_CODE): NO_STIMULUS_ACTION,
                "positive": "configured stimulus action",
            },
            "codebook": [
                {"action": action.action, "label": action.label, "stimulus_code": action.code}
                for action in self._actions
            ],
            "intervals": self.event_log_rows(),
        }

    def _open_interval(self, action: str, label: str, code: int, boundary: MIILBoundary) -> None:
        normalized = self._normalized_boundary(boundary)
        self._last_boundary = normalized
        self._intervals.append(
            MIILInterval(
                event_index=len(self._intervals) + 1,
                action=action,
                label=label,
                original_code=code,
                effective_code=code,
                start=normalized,
            )
        )

    def _close_current(self, boundary: MIILBoundary, status: str) -> MIILBoundary:
        interval = self.current_interval
        normalized = self._normalized_boundary(boundary)
        if interval is None:
            self._last_boundary = normalized
            return normalized
        closed_status = "dropped" if interval.effective_code == INVALID_STIMULUS_CODE else status
        self._intervals[-1] = replace(interval, end=normalized, status=closed_status)
        self._last_boundary = normalized
        return normalized

    def _normalized_boundary(self, boundary: MIILBoundary) -> MIILBoundary:
        previous = self._last_boundary
        current = self.current_interval
        floor = 0
        if previous is not None:
            floor = previous.host_monotonic_ns
        if current is not None:
            floor = max(floor, current.start_monotonic_ns)
        if boundary.host_monotonic_ns < floor:
            raise ValueError(
                "MIIL boundary host_monotonic_ns cannot move backwards: "
                f"{boundary.host_monotonic_ns} < {floor}."
            )
        return boundary

    def _current_monotonic_ns(self) -> int | None:
        return None if self._last_boundary is None else self._last_boundary.host_monotonic_ns


__all__ = [
    "DEFAULT_MIIL_ACTIONS",
    "DROP_STIMULUS_ACTION",
    "IDLE_STIMULUS_CODE",
    "INVALID_STIMULUS_CODE",
    "MIIL_PARADIGM_ID",
    "MIIL_PARADIGM_NAME",
    "NO_STIMULUS_ACTION",
    "MIILAction",
    "MIILBoundary",
    "MIILController",
    "MIILInterval",
    "MIILState",
    "capture_host_boundary",
]
