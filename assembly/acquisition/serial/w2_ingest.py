"""Normalize decoded RunE W2 serial records into realtime stream samples."""

from __future__ import annotations

from assembly.acquisition.runtime.stream_store import (
    RealtimeStreamStore,
    StreamSample,
    StreamSchema,
)
from assembly.acquisition.serial.w2_worker import W2Record


def w2_stream_id(device_id: str) -> str:
    device_id = device_id.strip()
    if not device_id:
        raise ValueError("W2 device_id must not be empty.")
    return f"w2.{device_id}.signal"


def make_w2_stream_schema(
    device_id: str,
    *,
    nominal_rate_hz: float = 1000.0,
) -> StreamSchema:
    return StreamSchema(
        stream_id=w2_stream_id(device_id),
        field_keys=("value",),
        nominal_rate_hz=nominal_rate_hz,
    )


class W2RecordIngestor:
    """Convert one physical W2 worker's packet records into one runtime stream."""

    def __init__(
        self,
        store: RealtimeStreamStore,
        device_id: str,
    ) -> None:
        self.store = store
        self.stream_id = w2_stream_id(device_id)

        schema = store.schema(self.stream_id)
        if schema is None:
            raise ValueError(f"Store is missing W2 stream {self.stream_id!r}.")
        if schema.field_keys != ("value",):
            raise ValueError(
                f"W2 stream {self.stream_id!r} must expose exactly field ('value',)."
            )

    def ingest(self, record: W2Record) -> None:
        host_monotonic_ns = int(record["host_monotonic_ns"])
        host_unix_ns = int(record["host_unix_ns"])
        samples = tuple(record["samples"])  # type: ignore[arg-type]
        if not samples:
            raise ValueError("W2 record must contain at least one decoded sample.")

        # One serial read may yield one packet containing several reconstructed
        # samples.  The device does not provide a timestamp for each sample, so
        # all samples retain the packet's host observation timestamp.  Nominal
        # spacing is a later display/processing interpretation.
        self.store.append_batch(
            self.stream_id,
            (
                StreamSample(
                    host_monotonic_ns=host_monotonic_ns,
                    host_unix_ns=host_unix_ns,
                    values=(float(value),),
                )
                for value in samples
            ),
        )


__all__ = [
    "W2RecordIngestor",
    "make_w2_stream_schema",
    "w2_stream_id",
]
