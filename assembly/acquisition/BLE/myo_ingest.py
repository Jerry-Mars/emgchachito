"""Adapt raw MyoWorker records into generic runtime streams."""

from __future__ import annotations

import queue

from assembly.acquisition.BLE.myo_worker import MyoRecord
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore, StreamSchema


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
    """
    Drain MyoWorker records into a RealtimeStreamStore.

    No thread is created here.

    The caller decides *when* drain() is executed.  The first integration
    uses the GUI loop; later this can move to its own consumer thread
    without changing the store or Plot provider.
    """

    def __init__(
        self,
        records: queue.Queue[MyoRecord],
        store: RealtimeStreamStore,
    ) -> None:
        self.records = records
        self.store = store

        self._validate_store()

    def drain(self, max_records: int = 1024) -> int:
        """
        Consume up to max_records currently waiting in the worker queue.

        Returns the number of Myo records consumed.
        """

        max_records = max(1, int(max_records))
        consumed = 0

        while consumed < max_records:
            try:
                record = self.records.get_nowait()
            except queue.Empty:
                break

            self.ingest(record)
            consumed += 1

        return consumed

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
        notification_index = int(record["notification_index"])
        host_monotonic_ns = int(record["host_monotonic_ns"])
        host_unix_ns = int(record["host_unix_ns"])

        samples = tuple(record["samples"])  # type: ignore[arg-type]

        # Current MyoWorker contract guarantees two 8-channel samples
        # per notification.
        #
        # sample_index is a HOST-SIDE ORDERING COORDINATE.
        # It is not a Myo device timestamp.
        base_sample_index = notification_index * len(samples)

        for offset, sample in enumerate(samples):
            self.store.append(
                MYO_EMG_STREAM_ID,
                sample_index=base_sample_index + offset,
                host_monotonic_ns=host_monotonic_ns,
                host_unix_ns=host_unix_ns,
                values=tuple(float(value) for value in sample),
            )

    def _ingest_imu(self, record: MyoRecord) -> None:
        quaternion = tuple(record["quaternion"])  # type: ignore[arg-type]
        acceleration = tuple(record["accelerometer_g"])  # type: ignore[arg-type]
        gyroscope = tuple(record["gyroscope_dps"])  # type: ignore[arg-type]

        self.store.append(
            MYO_IMU_STREAM_ID,
            sample_index=int(record["sample_index"]),
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
