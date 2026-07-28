"""Manual Instruction Interval Labeling (MIIL) domain model.

The model deliberately knows nothing about acquisition devices or the GUI.  A
caller supplies a shared capture time and the current row cursor of every
stream whenever the operator changes the active instruction.  Saved samples
can then be labelled against per-stream row boundaries, with capture time as a
fallback for streams that were absent from a cursor snapshot.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping

from fundamental.stimulus_model import (
    IDLE_STIMULUS_CODE,
    INVALID_STIMULUS_CODE,
    StimulusState,
)


MIIL_PARADIGM_ID = "miil"
MIIL_PARADIGM_NAME = "Manual Instruction Interval Labeling"
NO_STIMULUS_ACTION = "no_stimulus"
DROP_STIMULUS_ACTION = "drop_stimulus"


@dataclass(frozen=True)
class MIILAction:
    """One operator-selectable MIIL instruction."""

    action: str
    label: str
    code: int


DEFAULT_MIIL_ACTIONS = (
    MIILAction("rest", "Rest", 1),
    MIILAction("knee_flexion", "Knee Flexion", 2),
    MIILAction("knee_extension", "Knee Extension", 3),
)


@dataclass(frozen=True)
class CapturePosition:
    """Shared capture time and each stream's next zero-based row index."""

    time_s: float
    row_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        time_s = float(self.time_s)
        if not math.isfinite(time_s) or time_s < 0.0:
            raise ValueError("Capture time must be a finite non-negative value.")

        normalized: dict[str, int] = {}
        for stream_id, row_count in self.row_counts.items():
            key = str(stream_id).strip()
            if not key:
                raise ValueError("Stream ID cannot be empty.")
            if isinstance(row_count, bool) or int(row_count) != row_count or row_count < 0:
                raise ValueError("Stream row counts must be non-negative integers.")
            normalized[key] = int(row_count)

        object.__setattr__(self, "time_s", time_s)
        object.__setattr__(self, "row_counts", MappingProxyType(normalized))


@dataclass(frozen=True)
class MIILInterval:
    """One actual instruction interval in shared time and stream row space."""

    event_index: int
    action: str
    label: str
    original_code: int
    effective_code: int
    start: CapturePosition
    end: CapturePosition | None = None
    status: str = "running"
    drop_pressed_at_s: float | None = None

    @property
    def start_time_s(self) -> float:
        return self.start.time_s

    @property
    def end_time_s(self) -> float | None:
        return None if self.end is None else self.end.time_s

    def duration_s(self, current_time_s: float | None = None) -> float:
        end_time_s = self.end_time_s
        if end_time_s is None:
            end_time_s = self.start_time_s if current_time_s is None else float(current_time_s)
        return max(0.0, end_time_s - self.start_time_s)


class MIILController:
    """Own the MIIL codebook and operator-created interval timeline."""

    def __init__(self) -> None:
        self._actions: tuple[MIILAction, ...] = DEFAULT_MIIL_ACTIONS
        self._capture_actions: tuple[MIILAction, ...] = self._actions
        self.state = StimulusState.IDLE
        self._intervals: list[MIILInterval] = []
        self._last_time_s = 0.0
        self._row_resolver_cache: dict[
            str,
            tuple[tuple[int, ...], tuple[MIILInterval, ...], bool],
        ] = {}

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
        if interval is None:
            return NO_STIMULUS_ACTION
        if interval.effective_code == INVALID_STIMULUS_CODE:
            return DROP_STIMULUS_ACTION
        return interval.action

    @property
    def current_label(self) -> str:
        interval = self.current_interval
        if interval is None:
            return "No Stimulus"
        if interval.effective_code == INVALID_STIMULUS_CODE:
            return f"Drop: {interval.label}"
        return interval.label

    def current_elapsed_s(self, current_time_s: float | None = None) -> float:
        interval = self.current_interval
        if interval is None:
            return 0.0
        if current_time_s is None or self.state == StimulusState.PAUSED:
            current_time_s = self._last_time_s
        return interval.duration_s(current_time_s)

    def apply_actions(self, actions: list[MIILAction] | tuple[MIILAction, ...]) -> str | None:
        """Validate and freeze a codebook until the next stopped/idle edit."""

        if self.state in (StimulusState.RUNNING, StimulusState.PAUSED):
            return "Stop MIIL before changing its actions."

        validated: list[MIILAction] = []
        codes: set[int] = set()
        action_ids: set[str] = set()
        for value in actions:
            action = value.action.strip()
            label = value.label.strip()
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
        if not self._intervals:
            self._capture_actions = self._actions
        self._row_resolver_cache.clear()
        return None

    def configure_actions(self, actions: list[MIILAction] | tuple[MIILAction, ...]) -> str | None:
        """Compatibility alias emphasizing that Apply commits the codebook."""

        return self.apply_actions(actions)

    def reset_timeline(self) -> str | None:
        if self.state in (StimulusState.RUNNING, StimulusState.PAUSED):
            return "Stop MIIL before resetting its timeline."
        self.state = StimulusState.IDLE
        self._intervals = []
        self._last_time_s = 0.0
        self._capture_actions = self._actions
        self._row_resolver_cache.clear()
        return None

    def start(self, position: CapturePosition) -> str:
        if self.state == StimulusState.RUNNING:
            return "MIIL is already running."
        if self.state == StimulusState.PAUSED:
            return self.resume(position)
        if not self._actions:
            return "MIIL action list is empty."

        self._intervals = []
        self._capture_actions = self._actions
        self._row_resolver_cache.clear()
        self.state = StimulusState.RUNNING
        self._last_time_s = position.time_s
        self._open_interval(
            action=NO_STIMULUS_ACTION,
            label="No Stimulus",
            code=IDLE_STIMULUS_CODE,
            position=position,
        )
        return "MIIL started with no_stimulus."

    def select_action(self, code: int, position: CapturePosition) -> str:
        if self.state != StimulusState.RUNNING:
            return "MIIL is not running."
        action = next((item for item in self._capture_actions if item.code == code), None)
        if action is None:
            return f"MIIL action code {code} is not configured."
        current = self.current_interval
        if current is not None and current.effective_code == action.code:
            self._last_time_s = max(self._last_time_s, position.time_s)
            return f"MIIL action '{action.label}' is already active; click ignored."

        boundary = self._close_current(position, "completed")
        self._open_interval(action.action, action.label, action.code, boundary)
        return f"MIIL action changed to '{action.label}'."

    def select_no_stimulus(self, position: CapturePosition) -> str:
        if self.state != StimulusState.RUNNING:
            return "MIIL is not running."
        current = self.current_interval
        if current is not None and current.effective_code == IDLE_STIMULUS_CODE:
            self._last_time_s = max(self._last_time_s, position.time_s)
            return "no_stimulus is already active; click ignored."

        boundary = self._close_current(position, "completed")
        self._open_interval(
            NO_STIMULUS_ACTION,
            "No Stimulus",
            IDLE_STIMULUS_CODE,
            boundary,
        )
        return "MIIL action changed to no_stimulus."

    def drop_current(self, position: CapturePosition) -> str:
        if self.state != StimulusState.RUNNING:
            return "MIIL is not running."
        interval = self.current_interval
        if interval is None:
            return "MIIL has no active interval."
        if interval.effective_code == IDLE_STIMULUS_CODE:
            self._last_time_s = max(self._last_time_s, position.time_s)
            return "drop_stimulus ignored while no_stimulus is active."
        if interval.effective_code == INVALID_STIMULUS_CODE:
            self._last_time_s = max(self._last_time_s, position.time_s)
            return "drop_stimulus is already active; click ignored."

        drop_time_s = max(
            interval.start_time_s,
            self._last_time_s,
            float(position.time_s),
        )
        self._intervals[-1] = replace(
            interval,
            effective_code=INVALID_STIMULUS_CODE,
            status="dropped",
            drop_pressed_at_s=drop_time_s,
        )
        self._row_resolver_cache.clear()
        self._last_time_s = max(self._last_time_s, drop_time_s)
        return (
            f"Current '{interval.label}' interval was marked drop_stimulus "
            "from its beginning."
        )

    def pause(self, position: CapturePosition) -> str:
        if self.state != StimulusState.RUNNING:
            return "MIIL is not running."
        self._last_time_s = self._normalized_boundary(position).time_s
        self.state = StimulusState.PAUSED
        return "MIIL paused; the current action is preserved."

    def resume(self, position: CapturePosition | None = None) -> str:
        if self.state != StimulusState.PAUSED:
            return "MIIL is not paused."
        if position is not None:
            self._last_time_s = max(self._last_time_s, position.time_s)
        self.state = StimulusState.RUNNING
        return "MIIL resumed with the same action."

    def stop(self, position: CapturePosition) -> str:
        if self.state not in (StimulusState.RUNNING, StimulusState.PAUSED):
            return "MIIL is not active."
        self._close_current(position, "stopped")
        self.state = StimulusState.STOPPED
        return "MIIL stopped."

    def sample_code(self, stream_id: str, row_index: int, time_s: float) -> int:
        """Resolve one saved row using its stream cursor, then shared time."""

        starts, indexed_intervals, complete = self._row_index(stream_id)
        if complete and starts:
            candidate_index = bisect_right(starts, int(row_index)) - 1
            if candidate_index >= 0:
                interval = indexed_intervals[candidate_index]
                end_row = (
                    None
                    if interval.end is None
                    else interval.end.row_counts.get(stream_id)
                )
                if interval.end is None or (
                    end_row is not None and row_index < end_row
                ):
                    return interval.effective_code
            return IDLE_STIMULUS_CODE

        for interval in self._intervals:
            end_time_s = interval.end_time_s
            if time_s >= interval.start_time_s and (
                end_time_s is None or time_s < end_time_s
            ):
                return interval.effective_code
        return IDLE_STIMULUS_CODE

    def _row_index(
        self,
        stream_id: str,
    ) -> tuple[tuple[int, ...], tuple[MIILInterval, ...], bool]:
        cached = self._row_resolver_cache.get(stream_id)
        if cached is not None:
            return cached

        entries: list[tuple[int, MIILInterval]] = []
        complete = True
        for interval in self._intervals:
            start_row = interval.start.row_counts.get(stream_id)
            end_row = (
                None
                if interval.end is None
                else interval.end.row_counts.get(stream_id)
            )
            if start_row is None or (interval.end is not None and end_row is None):
                complete = False
                continue
            entries.append((start_row, interval))

        entries.sort(key=lambda item: (item[0], item[1].event_index))
        result = (
            tuple(item[0] for item in entries),
            tuple(item[1] for item in entries),
            complete,
        )
        self._row_resolver_cache[stream_id] = result
        return result

    def event_log_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        current = self.current_interval
        for interval in self._intervals:
            status = interval.status
            if interval == current and self.state == StimulusState.PAUSED and status != "dropped":
                status = "paused"
            rows.append(
                {
                    "event_index": interval.event_index,
                    "stimulus_code": interval.effective_code,
                    "planned_code": interval.original_code,
                    "label": interval.label,
                    "start_time_s": interval.start_time_s,
                    "end_time_s": interval.end_time_s,
                    "status": status,
                    "action": interval.action,
                    "original_code": interval.original_code,
                    "duration_s": interval.duration_s(self._last_time_s),
                    "drop_pressed_at_s": interval.drop_pressed_at_s,
                }
            )
        return rows

    def metadata_snapshot(self) -> dict[str, object]:
        """Return a JSON-serializable codebook, audit trail, and offline guidance."""

        intervals: list[dict[str, object]] = []
        current = self.current_interval
        for interval in self._intervals:
            status = interval.status
            if interval == current and self.state == StimulusState.PAUSED and status != "dropped":
                status = "paused"
            intervals.append(
                {
                    "event_index": interval.event_index,
                    "action": interval.action,
                    "label": interval.label,
                    "original_code": interval.original_code,
                    "stimulus_code": interval.effective_code,
                    "start_time_s": interval.start_time_s,
                    "end_time_s": interval.end_time_s,
                    "start_row_counts": dict(interval.start.row_counts),
                    "end_row_counts": (
                        None if interval.end is None else dict(interval.end.row_counts)
                    ),
                    "status": status,
                    "drop_pressed_at_s": interval.drop_pressed_at_s,
                }
            )

        return {
            "paradigm": MIIL_PARADIGM_ID,
            "paradigm_name": MIIL_PARADIGM_NAME,
            "state": self.state.value,
            "code_semantics": {
                str(INVALID_STIMULUS_CODE): DROP_STIMULUS_ACTION,
                str(IDLE_STIMULUS_CODE): NO_STIMULUS_ACTION,
                "positive": "configured stimulus action",
            },
            "codebook": [
                {"action": action.action, "label": action.label, "stimulus_code": action.code}
                for action in (self._capture_actions if self._intervals else self._actions)
            ],
            "boundary_method": "per_stream_row_cursor_with_shared_time_fallback",
            "intervals": intervals,
            "offline_processing_recommendations": {
                "apply_at_analysis_time": True,
                "exclude_codes": [INVALID_STIMULUS_CODE, IDLE_STIMULUS_CODE],
                "action_start_trim_s": 1.0,
                "action_end_trim_s_range": [0.5, 1.0],
                "rest_start_trim_s_range": [0.5, 1.0],
                "rest_end_trim_s": 0.5,
                "minimum_valid_duration_s_range": [1.5, 2.0],
                "window_length_ms_range": [200, 300],
                "window_overlap_fraction": 0.5,
                "windows_must_not_cross_interval_boundaries": True,
            },
        }

    def _open_interval(
        self,
        action: str,
        label: str,
        code: int,
        position: CapturePosition,
    ) -> None:
        self._last_time_s = max(self._last_time_s, position.time_s)
        self._intervals.append(
            MIILInterval(
                event_index=len(self._intervals) + 1,
                action=action,
                label=label,
                original_code=code,
                effective_code=code,
                start=position,
            )
        )
        self._row_resolver_cache.clear()

    def _close_current(self, position: CapturePosition, status: str) -> CapturePosition:
        interval = self.current_interval
        if interval is None:
            return position
        boundary = self._normalized_boundary(position)
        closed_status = "dropped" if interval.effective_code == INVALID_STIMULUS_CODE else status
        self._intervals[-1] = replace(interval, end=boundary, status=closed_status)
        self._row_resolver_cache.clear()
        self._last_time_s = boundary.time_s
        return boundary

    def _normalized_boundary(self, position: CapturePosition) -> CapturePosition:
        interval = self.current_interval
        if interval is None:
            return position
        rows = dict(position.row_counts)
        for stream_id, start_row in interval.start.row_counts.items():
            rows[stream_id] = max(start_row, rows.get(stream_id, start_row))
        return CapturePosition(
            max(interval.start_time_s, self._last_time_s, position.time_s),
            rows,
        )
