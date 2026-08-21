"""PlotDataProvider backed by a RealtimeStreamStore."""

from __future__ import annotations

from dataclasses import dataclass

from assembly.plot.models import SeriesSpec, SeriesWindow
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore


@dataclass(frozen=True, slots=True)
class _SeriesBinding:
    spec: SeriesSpec
    field_index: int


class BufferedPlotProvider:
    """
    Read-only PlotDataProvider over RealtimeStreamStore.

    time_s returned to Plot is a nominal display coordinate:

        sample_index / nominal_rate_hz

    It is intentionally NOT presented as a device timestamp.
    PlotWindow currently only uses it to construct relative x coordinates.
    """

    def __init__(
        self,
        store: RealtimeStreamStore,
        series_specs: tuple[SeriesSpec, ...],
    ) -> None:
        self.store = store
        self._specs = series_specs

        if len({spec.series_id for spec in series_specs}) != len(series_specs):
            raise ValueError("series_id values must be unique.")

        bindings: dict[str, _SeriesBinding] = {}

        for spec in series_specs:
            schema = store.schema(spec.stream_id)

            if schema is None:
                raise ValueError(
                    f"Series {spec.series_id!r} references unknown "
                    f"stream {spec.stream_id!r}."
                )

            try:
                field_index = schema.field_keys.index(spec.field_key)
            except ValueError as exc:
                raise ValueError(
                    f"Series {spec.series_id!r} references unknown field "
                    f"{spec.field_key!r} in {spec.stream_id!r}."
                ) from exc

            bindings[spec.series_id] = _SeriesBinding(
                spec=spec,
                field_index=field_index,
            )

        self._bindings = bindings

    @property
    def row_count(self) -> int:
        return self.store.row_count

    @property
    def stream_count(self) -> int:
        return self.store.stream_count

    def series_specs(self) -> tuple[SeriesSpec, ...]:
        return self._specs

    def series_spec(self, series_id: str) -> SeriesSpec | None:
        binding = self._bindings.get(series_id)
        return binding.spec if binding is not None else None

    def get_series_window(
        self,
        series_id: str,
        window_seconds: float,
    ) -> SeriesWindow | None:
        binding = self._bindings.get(series_id)
        if binding is None:
            return None

        snapshot = self.store.window(
            binding.spec.stream_id,
            window_seconds,
        )

        if not snapshot.rows:
            return None

        rate = snapshot.schema.nominal_rate_hz

        # Derived plotting coordinate only.
        time_s = [
            row.sample_index / rate
            for row in snapshot.rows
        ]

        values = [
            row.values[binding.field_index]
            for row in snapshot.rows
        ]

        return SeriesWindow(
            spec=binding.spec,
            time_s=time_s,
            values=values,
        )

    def latest_series_values(
        self,
        limit: int = 4,
    ) -> tuple[tuple[SeriesSpec, float], ...]:
        result: list[tuple[SeriesSpec, float]] = []
        latest_by_stream = {}

        for spec in self._specs:
            if len(result) >= max(0, int(limit)):
                break

            binding = self._bindings[spec.series_id]

            if spec.stream_id not in latest_by_stream:
                latest_by_stream[spec.stream_id] = self.store.latest(
                    spec.stream_id
                )

            row = latest_by_stream[spec.stream_id]
            if row is None:
                continue

            result.append(
                (
                    spec,
                    row.values[binding.field_index],
                )
            )

        return tuple(result)
