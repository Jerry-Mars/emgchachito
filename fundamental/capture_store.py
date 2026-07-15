"""In-memory storage and plot queries for heterogeneous streams."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from fundamental.messages import DEFAULT_PLOT_BUFFER_SIZE
from fundamental.streams import (
    CaptureResumeState,
    SeriesSpec,
    SeriesWindow,
    StreamBlock,
    StreamCursor,
    StreamSnapshot,
    StreamSpec,
    freeze_cursors,
)


@dataclass
class _StreamState:
    spec: StreamSpec
    plot_buffer_size: int

    def __post_init__(self) -> None:
        self.time_window: deque[float] = deque(maxlen=self.plot_buffer_size)
        self.value_windows: dict[str, deque[float]] = {
            field.key: deque(maxlen=self.plot_buffer_size)
            for field in self.spec.fields
            if field.plottable
        }
        self.full_time_s: list[float] = []
        self.full_rows: list[tuple[int | float, ...]] = []
        self.latest_row: tuple[int | float, ...] | None = None

    def clear(self) -> None:
        self.time_window.clear()
        for values in self.value_windows.values():
            values.clear()
        self.full_time_s.clear()
        self.full_rows.clear()
        self.latest_row = None


class CaptureStore:
    """Keep full capture rows plus bounded windows for live consumers."""

    def __init__(
        self,
        plot_buffer_size: int = DEFAULT_PLOT_BUFFER_SIZE,
        stream_specs: tuple[StreamSpec, ...] = (),
    ) -> None:
        self.plot_buffer_size = max(1, int(plot_buffer_size))
        self._states: dict[str, _StreamState] = {}
        self.configure_streams(stream_specs, clear=True)

    @property
    def row_count(self) -> int:
        """Total stored rows across all independently sampled streams."""

        return sum(len(state.full_rows) for state in self._states.values())

    @property
    def latest_time_s(self) -> float:
        latest = [state.full_time_s[-1] for state in self._states.values() if state.full_time_s]
        return max(latest, default=0.0)

    @property
    def stream_count(self) -> int:
        return len(self._states)

    def configure_streams(self, specs: tuple[StreamSpec, ...], clear: bool = True) -> None:
        next_specs = {spec.stream_id: spec for spec in specs}
        if len(next_specs) != len(specs):
            raise ValueError("CaptureStore stream IDs must be unique.")

        if clear:
            self._states = {
                spec.stream_id: _StreamState(spec, self.plot_buffer_size)
                for spec in specs
            }
            return

        for spec in specs:
            state = self._states.get(spec.stream_id)
            if state is None:
                self._states[spec.stream_id] = _StreamState(spec, self.plot_buffer_size)
            elif state.spec != spec:
                if state.full_rows:
                    raise ValueError(f"Cannot change active schema for stream {spec.stream_id!r}.")
                self._states[spec.stream_id] = _StreamState(spec, self.plot_buffer_size)

    def reset(self, specs: tuple[StreamSpec, ...] | None = None) -> None:
        if specs is not None:
            self.configure_streams(specs, clear=True)
            return
        for state in self._states.values():
            state.clear()

    def append_block(self, block: StreamBlock) -> int:
        if not block.rows:
            return 0

        state = self._states.get(block.spec.stream_id)
        if state is None:
            state = _StreamState(block.spec, self.plot_buffer_size)
            self._states[block.spec.stream_id] = state
        elif state.spec != block.spec:
            raise ValueError(f"Schema changed while capturing stream {block.spec.stream_id!r}.")

        previous_time = state.full_time_s[-1] if state.full_time_s else None
        field_indexes = block.spec.field_index
        for timestamp, row in zip(block.time_s, block.rows, strict=True):
            numeric_time = float(timestamp)
            if previous_time is not None and numeric_time < previous_time:
                raise ValueError(f"Non-monotonic time in stream {block.spec.stream_id!r}.")
            previous_time = numeric_time
            state.full_time_s.append(numeric_time)
            state.full_rows.append(row)
            state.time_window.append(numeric_time)
            state.latest_row = row
            for field_key, values in state.value_windows.items():
                values.append(float(row[field_indexes[field_key]]))
        return block.row_count

    def stream_specs(self) -> tuple[StreamSpec, ...]:
        return tuple(state.spec for state in self._states.values())

    def series_specs(self) -> tuple[SeriesSpec, ...]:
        series: list[SeriesSpec] = []
        for state in self._states.values():
            for field in state.spec.fields:
                if not field.plottable:
                    continue
                series.append(
                    SeriesSpec(
                        series_id=f"{state.spec.stream_id}/{field.key}",
                        stream_id=state.spec.stream_id,
                        field_key=field.key,
                        label=field.label,
                        unit=field.unit,
                        signal_kind=field.signal_kind,
                        default_plot=field.default_plot,
                        fixed_range=field.fixed_range,
                    )
                )
        return tuple(series)

    def series_spec(self, series_id: str) -> SeriesSpec | None:
        return next((spec for spec in self.series_specs() if spec.series_id == series_id), None)

    def get_series_window(self, series_id: str, window_seconds: float) -> SeriesWindow | None:
        series = self.series_spec(series_id)
        if series is None:
            return None
        state = self._states[series.stream_id]
        if not self._series_is_active(state, series.field_key):
            return None

        timestamps = list(state.time_window)
        values = list(state.value_windows.get(series.field_key, ()))
        if not timestamps or not values:
            return None

        x_max = timestamps[-1]
        x_min = x_max - max(0.1, float(window_seconds))
        start_index = next(
            (index for index, timestamp in enumerate(timestamps) if timestamp >= x_min),
            len(timestamps) - 1,
        )
        return SeriesWindow(series, timestamps[start_index:], values[start_index:])

    def latest_series_values(self, limit: int = 4) -> tuple[tuple[SeriesSpec, float], ...]:
        result: list[tuple[SeriesSpec, float]] = []
        for series in self.series_specs():
            state = self._states[series.stream_id]
            values = state.value_windows.get(series.field_key)
            if values and self._series_is_active(state, series.field_key):
                result.append((series, values[-1]))
            if len(result) >= max(0, int(limit)):
                break
        return tuple(result)

    def snapshots(self) -> tuple[StreamSnapshot, ...]:
        return tuple(
            StreamSnapshot(state.spec, tuple(state.full_time_s), tuple(state.full_rows))
            for state in self._states.values()
            if state.full_rows
        )

    def resume_state(self) -> CaptureResumeState:
        cursors: dict[str, StreamCursor] = {}
        for stream_id, state in self._states.items():
            if not state.full_rows:
                continue
            cursors[stream_id] = StreamCursor(
                spec=state.spec,
                row_count=len(state.full_rows),
                last_time_s=state.full_time_s[-1],
                last_row=state.full_rows[-1],
            )
        return CaptureResumeState(self.latest_time_s, freeze_cursors(cursors))

    def stream_row_counts(self) -> dict[str, int]:
        return {stream_id: len(state.full_rows) for stream_id, state in self._states.items()}

    @staticmethod
    def _series_is_active(state: _StreamState, field_key: str) -> bool:
        count_key = state.spec.active_signal_count_key
        if count_key is None or state.latest_row is None:
            return True

        signal_keys = [field.key for field in state.spec.signal_fields if field.plottable]
        try:
            signal_index = signal_keys.index(field_key)
        except ValueError:
            return True
        active_count = int(state.latest_row[state.spec.field_index[count_key]])
        return signal_index < active_count
