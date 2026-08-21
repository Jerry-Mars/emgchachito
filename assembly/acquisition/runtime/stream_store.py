"""Small thread-safe finite-history store for live streams."""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StreamSchema:
    """
    Runtime description of one sampled stream.

    nominal_rate_hz describes nominal sample spacing.  It does NOT imply
    that the source provided a device timestamp.
    """

    stream_id: str
    field_keys: tuple[str, ...]
    nominal_rate_hz: float

    def __post_init__(self) -> None:
        if not self.stream_id:
            raise ValueError("stream_id must not be empty.")
        if not self.field_keys:
            raise ValueError("field_keys must not be empty.")
        if len(set(self.field_keys)) != len(self.field_keys):
            raise ValueError("field_keys must be unique.")
        if self.nominal_rate_hz <= 0:
            raise ValueError("nominal_rate_hz must be positive.")


@dataclass(frozen=True, slots=True)
class StreamRow:
    """
    One logical sample in a runtime stream.

    sample_index:
        Host-side ordering coordinate.

    host_monotonic_ns / host_unix_ns:
        Observation metadata inherited from the acquisition worker.

    values:
        Values corresponding to StreamSchema.field_keys.
    """

    sample_index: int
    host_monotonic_ns: int
    host_unix_ns: int
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class StreamSnapshot:
    schema: StreamSchema
    rows: tuple[StreamRow, ...]


class RealtimeStreamStore:
    """
    Keep a bounded recent history for multiple streams.

    This class deliberately knows nothing about:
      - BLE / serial / Myo
      - Plot
      - HDF5 / CSV
      - stimulus
      - device timestamps
    """

    def __init__(
        self,
        schemas: tuple[StreamSchema, ...],
        *,
        retention_seconds: float = 30.0,
    ) -> None:
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive.")
        if not schemas:
            raise ValueError("At least one stream schema is required.")

        by_id = {schema.stream_id: schema for schema in schemas}
        if len(by_id) != len(schemas):
            raise ValueError("stream_id values must be unique.")

        self._schemas = by_id
        self._buffers: dict[str, deque[StreamRow]] = {}
        self._last_sample_index: dict[str, int | None] = {}

        for schema in schemas:
            # A little slack avoids edge effects at the exact retention boundary.
            capacity = max(
                1,
                math.ceil(retention_seconds * schema.nominal_rate_hz) + 8,
            )
            self._buffers[schema.stream_id] = deque(maxlen=capacity)
            self._last_sample_index[schema.stream_id] = None

        self._lock = threading.RLock()
        self._total_rows = 0

    @property
    def row_count(self) -> int:
        """Total number of rows ingested during this store's lifetime."""
        with self._lock:
            return self._total_rows

    @property
    def stream_count(self) -> int:
        return len(self._schemas)

    def schemas(self) -> tuple[StreamSchema, ...]:
        return tuple(self._schemas.values())

    def schema(self, stream_id: str) -> StreamSchema | None:
        return self._schemas.get(stream_id)

    def append(
        self,
        stream_id: str,
        *,
        sample_index: int,
        host_monotonic_ns: int,
        host_unix_ns: int,
        values: tuple[float, ...],
    ) -> None:
        schema = self._schemas.get(stream_id)
        if schema is None:
            raise KeyError(f"Unknown stream_id: {stream_id!r}")

        if len(values) != len(schema.field_keys):
            raise ValueError(
                f"{stream_id!r} expects {len(schema.field_keys)} values, "
                f"got {len(values)}."
            )

        row = StreamRow(
            sample_index=int(sample_index),
            host_monotonic_ns=int(host_monotonic_ns),
            host_unix_ns=int(host_unix_ns),
            values=tuple(float(value) for value in values),
        )

        with self._lock:
            previous = self._last_sample_index[stream_id]

            # Gaps are allowed; reordering is not.
            if previous is not None and row.sample_index <= previous:
                raise ValueError(
                    f"Non-increasing sample index for {stream_id!r}: "
                    f"{row.sample_index} after {previous}."
                )

            self._buffers[stream_id].append(row)
            self._last_sample_index[stream_id] = row.sample_index
            self._total_rows += 1

    def latest(self, stream_id: str) -> StreamRow | None:
        buffer = self._buffers.get(stream_id)
        if buffer is None:
            raise KeyError(f"Unknown stream_id: {stream_id!r}")

        with self._lock:
            return buffer[-1] if buffer else None

    def window(
        self,
        stream_id: str,
        window_seconds: float,
    ) -> StreamSnapshot:
        """
        Return recent rows using sample_index + nominal_rate_hz.

        This is a nominal sample window, not a device-time query.
        """

        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive.")

        schema = self._schemas.get(stream_id)
        if schema is None:
            raise KeyError(f"Unknown stream_id: {stream_id!r}")

        with self._lock:
            rows = tuple(self._buffers[stream_id])

        if not rows:
            return StreamSnapshot(schema, ())

        latest_index = rows[-1].sample_index
        span_samples = math.ceil(window_seconds * schema.nominal_rate_hz)
        minimum_index = latest_index - span_samples

        recent = tuple(
            row
            for row in rows
            if row.sample_index >= minimum_index
        )

        return StreamSnapshot(schema, recent)