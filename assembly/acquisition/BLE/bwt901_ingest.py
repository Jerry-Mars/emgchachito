"""Normalize BWT901BLE frame observations into realtime stream samples."""

from __future__ import annotations

from assembly.acquisition.BLE.bwt901_worker import BWT901Record
from assembly.acquisition.runtime.stream_store import (
    RealtimeStreamStore,
    StreamSample,
    StreamSchema,
)


BWT901_FIELD_KEYS = (
    "acc_x_g",
    "acc_y_g",
    "acc_z_g",
    "gyro_x_dps",
    "gyro_y_dps",
    "gyro_z_dps",
    "angle_x_deg",
    "angle_y_deg",
    "angle_z_deg",
)


def bwt901_stream_id(device_id: str) -> str:
    device_id = device_id.strip()
    if not device_id:
        raise ValueError("BWT901 device_id must not be empty.")
    return f"bwt901.{device_id}.imu"


def make_bwt901_stream_schema(device_id: str) -> StreamSchema:
    """Create one unknown-rate normalized IMU stream for one physical device."""

    return StreamSchema(
        stream_id=bwt901_stream_id(device_id),
        field_keys=BWT901_FIELD_KEYS,
        nominal_rate_hz=None,
    )


class BWT901RecordIngestor:
    """Convert one physical BWT901 worker's frame records into one stream."""

    def __init__(self, store: RealtimeStreamStore, device_id: str) -> None:
        self.store = store
        self.stream_id = bwt901_stream_id(device_id)

        schema = store.schema(self.stream_id)
        if schema is None:
            raise ValueError(f"Store is missing BWT901 stream {self.stream_id!r}.")
        if schema.field_keys != BWT901_FIELD_KEYS:
            raise ValueError(
                f"BWT901 stream {self.stream_id!r} has an unexpected field layout."
            )
        if schema.nominal_rate_hz is not None:
            raise ValueError(
                f"BWT901 stream {self.stream_id!r} must not claim a nominal rate."
            )

    def ingest(self, record: BWT901Record) -> None:
        acceleration = tuple(record["acceleration_g"])  # type: ignore[arg-type]
        gyroscope = tuple(record["gyroscope_dps"])  # type: ignore[arg-type]
        angles = tuple(record["euler_angle_deg"])  # type: ignore[arg-type]

        if len(acceleration) != 3 or len(gyroscope) != 3 or len(angles) != 3:
            raise ValueError("BWT901 vectors must each contain exactly three values.")

        self.store.append_batch(
            self.stream_id,
            (
                StreamSample(
                    host_monotonic_ns=int(record["host_monotonic_ns"]),
                    host_unix_ns=int(record["host_unix_ns"]),
                    values=tuple(
                        float(value)
                        for value in (*acceleration, *gyroscope, *angles)
                    ),
                ),
            ),
        )


__all__ = [
    "BWT901_FIELD_KEYS",
    "BWT901RecordIngestor",
    "bwt901_stream_id",
    "make_bwt901_stream_schema",
]
