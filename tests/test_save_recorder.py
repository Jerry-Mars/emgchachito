from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import h5py

from assembly.acquisition.runtime.stream_store import RealtimeStreamStore, StreamSample, StreamSchema
from assembly.save.recorder import H5StreamRecorder, RecorderState
from assembly.acquisition.serial.w2_ingest import W2RecordIngestor, make_w2_stream_schema
from assembly.save.store_tap import StreamStoreTap


class H5StreamRecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = StreamSchema("test.signal", ("x", "y"), nominal_rate_hz=100.0)

    def test_start_append_stop_persists_committed_rows_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "capture.h5"
            store = RealtimeStreamStore((self.schema,), retention_seconds=10.0)
            recorder = H5StreamRecorder()
            tap = StreamStoreTap(store, recorder)

            # Data committed before recording begins remains available to realtime
            # consumers but is intentionally not back-filled into the recording.
            before = tap.append(
                self.schema.stream_id,
                host_monotonic_ns=100,
                host_unix_ns=1_000,
                values=(1.0, 2.0),
            )
            self.assertEqual(before.runtime_index, 0)
            self.assertEqual(store.row_count, 1)

            result_path = recorder.start(path, store.schemas())
            self.assertEqual(result_path, path)
            self.assertEqual(recorder.state, RecorderState.RECORDING)

            rows = tap.append_batch(
                self.schema.stream_id,
                (
                    StreamSample(200, 2_000, (3.0, 4.0)),
                    StreamSample(300, 3_000, (5.0, 6.0)),
                ),
            )
            self.assertEqual(tuple(row.runtime_index for row in rows), (1, 2))
            self.assertEqual(recorder.rows_written, 2)
            self.assertEqual(recorder.rows_by_stream(), {self.schema.stream_id: 2})

            stopped_path = recorder.stop()
            self.assertEqual(stopped_path, path)
            self.assertEqual(recorder.state, RecorderState.STOPPED)
            self.assertEqual(store.row_count, 3)

            with h5py.File(path, "r") as handle:
                self.assertEqual(handle.attrs["format"], "assembly.normalized_streams")
                self.assertEqual(int(handle.attrs["format_version"]), 1)
                groups = list(handle["streams"].values())
                self.assertEqual(len(groups), 1)
                group = groups[0]
                self.assertEqual(group.attrs["stream_id"], self.schema.stream_id)
                self.assertEqual(
                    tuple(json.loads(group.attrs["field_keys_json"])),
                    self.schema.field_keys,
                )
                self.assertTrue(bool(group.attrs["nominal_rate_known"]))
                self.assertEqual(float(group.attrs["nominal_rate_hz"]), 100.0)
                self.assertEqual(group["runtime_index"][:].tolist(), [1, 2])
                self.assertEqual(group["host_monotonic_ns"][:].tolist(), [200, 300])
                self.assertEqual(group["host_unix_ns"][:].tolist(), [2_000, 3_000])
                self.assertEqual(group["values"][:].tolist(), [[3.0, 4.0], [5.0, 6.0]])

    def test_unknown_rate_schema_is_preserved_without_inventing_rate(self) -> None:
        schema = StreamSchema("imu", ("ax",), nominal_rate_hz=None)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "imu.h5"
            recorder = H5StreamRecorder()
            recorder.start(path, (schema,))
            store = RealtimeStreamStore((schema,))
            tap = StreamStoreTap(store, recorder)
            tap.append("imu", host_monotonic_ns=10, host_unix_ns=20, values=(0.25,))
            recorder.stop()

            with h5py.File(path, "r") as handle:
                group = next(iter(handle["streams"].values()))
                self.assertFalse(bool(group.attrs["nominal_rate_known"]))
                self.assertNotIn("nominal_rate_hz", group.attrs)
                self.assertEqual(group["values"][:].tolist(), [[0.25]])

    def test_plain_store_remains_usable_without_recorder_or_tap(self) -> None:
        store = RealtimeStreamStore((self.schema,))
        row = store.append(
            self.schema.stream_id,
            host_monotonic_ns=1,
            host_unix_ns=2,
            values=(7.0, 8.0),
        )
        self.assertEqual(row.runtime_index, 0)
        self.assertEqual(store.latest(self.schema.stream_id), row)

    def test_existing_w2_ingestor_can_commit_through_tap_without_custom_logic(self) -> None:
        schema = make_w2_stream_schema("left", nominal_rate_hz=1000.0)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "w2.h5"
            store = RealtimeStreamStore((schema,))
            recorder = H5StreamRecorder()
            tap = StreamStoreTap(store, recorder)
            ingestor = W2RecordIngestor(tap, "left")  # type: ignore[arg-type]

            recorder.start(path, store.schemas())
            ingestor.ingest(
                {
                    "packet_index": 7,
                    "mode": "emg_raw",
                    "host_monotonic_ns": 123,
                    "host_unix_ns": 456,
                    "samples": (10.0, 11.0, 12.0),
                }
            )
            recorder.stop()

            self.assertEqual(store.row_count, 3)
            self.assertEqual(
                [row.values for row in store.tail_samples(schema.stream_id, 3).rows],
                [(10.0,), (11.0,), (12.0,)],
            )
            with h5py.File(path, "r") as handle:
                group = next(iter(handle["streams"].values()))
                self.assertEqual(group["runtime_index"][:].tolist(), [0, 1, 2])
                self.assertEqual(group["host_monotonic_ns"][:].tolist(), [123, 123, 123])
                self.assertEqual(group["values"][:].tolist(), [[10.0], [11.0], [12.0]])

    def test_existing_output_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "capture.h5"
            path.write_bytes(b"existing")
            recorder = H5StreamRecorder()
            with self.assertRaises(FileExistsError):
                recorder.start(path, (self.schema,))
            self.assertEqual(path.read_bytes(), b"existing")
            self.assertEqual(recorder.state, RecorderState.STOPPED)

    def test_stop_is_safe_when_not_recording(self) -> None:
        recorder = H5StreamRecorder()
        self.assertIsNone(recorder.stop())
        self.assertEqual(recorder.state, RecorderState.STOPPED)


if __name__ == "__main__":
    unittest.main()
