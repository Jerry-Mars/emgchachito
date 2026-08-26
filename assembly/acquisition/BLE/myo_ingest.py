"""Adapt raw MyoWorker records into generic runtime streams."""

from __future__ import annotations

from assembly.acquisition.BLE.myo_worker import MyoRecord
from assembly.acquisition.runtime.stream_store import (
    RealtimeStreamStore,
    StreamSample,
    StreamSchema,
)


MYO_EMG_STREAM_ID = "myo.emg"
MYO_IMU_STREAM_ID = "myo.imu"


MYO_STREAM_SCHEMAS = (
    StreamSchema(
        stream_id=MYO_EMG_STREAM_ID,
        field_keys=tuple(
            f"emg_ch{channel}_code"
            for channel in range(1, 9)
        ),
        nominal_rate_hz=200.0,
    ),
    StreamSchema(
        stream_id=MYO_IMU_STREAM_ID,
        field_keys=(
            "quat_w",
            "quat_x",
            "quat_y",
            "quat_z",
            "accel_x_g",
            "accel_y_g",
            "accel_z_g",
            "gyro_x_dps",
            "gyro_y_dps",
            "gyro_z_dps",
        ),
        nominal_rate_hz=50.0,
    ),
)


class MyoRecordIngestor:
    """Convert raw MyoWorker records into normalized runtime stream samples.

    Queue draining is intentionally not part of this class.  A generic
    ``QueuePump`` owns that mechanical concern, while this class only knows how
    to interpret Myo record structure.

    The current ``notification_index`` / ``sample_index`` values emitted by
    MyoWorker are host-generated worker counters, not device-provided indices.
    They therefore do not become runtime sample identity here.  The store owns
    its normalized ``runtime_index``.  A future counter genuinely supplied by a
    device/protocol should be retained explicitly with its device semantics.
    """

    def __init__(self, store: RealtimeStreamStore) -> None:
        self.store = store
        self._validate_store()

    def ingest(self, record: MyoRecord) -> None:
        stream = record.get("stream")

        if stream == "emg":
            self._ingest_emg(record)
            return

        if stream == "imu":
            self._ingest_imu(record)
            return

        raise ValueError(f"Unsupported Myo record stream: {stream!r}")

    def _ingest_emg(self, record: MyoRecord) -> None:
        host_monotonic_ns = int(record["host_monotonic_ns"])
        host_unix_ns = int(record["host_unix_ns"])
        samples = tuple(record["samples"])  # type: ignore[arg-type]

        # Preserve the worker's observation semantics: both decoded EMG samples
        # belong to the same BLE notification and therefore share the same host
        # receive timestamps.  Nominal 200 Hz spacing is a later display/
        # processing interpretation, not raw observation timing.
        self.store.append_batch(
            MYO_EMG_STREAM_ID,
            (
                StreamSample(
                    host_monotonic_ns=host_monotonic_ns,
                    host_unix_ns=host_unix_ns,
                    values=tuple(float(value) for value in sample),
                )
                for sample in samples
            ),
        )

    def _ingest_imu(self, record: MyoRecord) -> None:
        quaternion = tuple(record["quaternion"])  # type: ignore[arg-type]
        acceleration = tuple(record["accelerometer_g"])  # type: ignore[arg-type]
        gyroscope = tuple(record["gyroscope_dps"])  # type: ignore[arg-type]

        self.store.append(
            MYO_IMU_STREAM_ID,
            host_monotonic_ns=int(record["host_monotonic_ns"]),
            host_unix_ns=int(record["host_unix_ns"]),
            values=tuple(
                float(value)
                for value in (
                    *quaternion,
                    *acceleration,
                    *gyroscope,
                )
            ),
        )

    def _validate_store(self) -> None:
        for required in MYO_STREAM_SCHEMAS:
            actual = self.store.schema(required.stream_id)

            if actual is None:
                raise ValueError(
                    f"RealtimeStreamStore is missing {required.stream_id!r}."
                )

            if actual.field_keys != required.field_keys:
                raise ValueError(
                    f"Field schema mismatch for {required.stream_id!r}."
                )
