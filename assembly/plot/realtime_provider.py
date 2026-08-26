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
    """Read-only PlotDataProvider over :class:`RealtimeStreamStore`.

    The realtime store selects rows by host-monotonic observation time.  This
    provider then chooses a display coordinate:

    * known nominal rate -> regular spacing anchored to the stream's latest
      host observation;
    * unknown nominal rate -> direct host-monotonic observation time.

    In both cases ``SeriesWindow.reference_time_s`` is the same latest host
    observation across the whole store, so different streams can share the
    Plot x-axis reference without pretending that they share a device clock.
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
        latest_row = snapshot.rows[-1]

        if rate is None:
            time_s = [
                row.host_monotonic_ns / 1_000_000_000.0
                for row in snapshot.rows
            ]
        else:
            # Keep a regular nominal sample spacing without inventing a device
            # clock: anchor only the latest normalized sample to the stream's
            # latest host observation and reconstruct relative spacing backward.
            anchor_s = latest_row.host_monotonic_ns / 1_000_000_000.0
            time_s = [
                anchor_s + (row.runtime_index - latest_row.runtime_index) / rate
                for row in snapshot.rows
            ]

        reference_time_s = (
            None
            if snapshot.reference_monotonic_ns is None
            else snapshot.reference_monotonic_ns / 1_000_000_000.0
        )

        values = [
            row.values[binding.field_index]
            for row in snapshot.rows
        ]

        return SeriesWindow(
            spec=binding.spec,
            time_s=time_s,
            values=values,
            reference_time_s=reference_time_s,
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
