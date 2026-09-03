from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from assembly.acquisition.runtime.stream_store import RealtimeStreamStore, StreamSample, StreamSchema
from assembly.save.csv_recorder import CSVStreamRecorder
from assembly.save.recorder import RecorderState
from assembly.save.selectable_recorder import SelectableStreamRecorder
from assembly.save.store_tap import StreamStoreTap


class CSVStreamRecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.known = StreamSchema("known.signal", ("x", "y"), nominal_rate_hz=100.0)
        self.unknown = StreamSchema("unknown.imu", ("ax",), nominal_rate_hz=None)

    def test_multistream_csv_preserves_normalized_rows_and_schema_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            requested = Path(temp_dir) / "capture.csv"
            store = RealtimeStreamStore((self.known, self.unknown))
            recorder = CSVStreamRecorder()
            tap = StreamStoreTap(store, recorder)

            tap.append(
                self.known.stream_id,
                host_monotonic_ns=100,
                host_unix_ns=1_000,
                values=(1.0, 2.0),
            )

            output = recorder.start(requested, store.schemas())
            self.assertEqual(output, Path(temp_dir) / "capture")
            self.assertEqual(recorder.state, RecorderState.RECORDING)

            tap.append_batch(
                self.known.stream_id,
                (
                    StreamSample(200, 2_000, (3.0, 4.0)),
                    StreamSample(300, 3_000, (5.0, 6.0)),
                ),
            )
            tap.append(
                self.unknown.stream_id,
                host_monotonic_ns=250,
                host_unix_ns=2_500,
                values=(0.25,),
            )
            stopped = recorder.stop()

            self.assertEqual(stopped, output)
            self.assertEqual(recorder.rows_written, 3)
            self.assertEqual(
                recorder.rows_by_stream(),
                {self.known.stream_id: 2, self.unknown.stream_id: 1},
            )

            metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["format"], "assembly.normalized_streams.csv")
            self.assertEqual(metadata["format_version"], 1)
            by_id = {item["stream_id"]: item for item in metadata["streams"]}
            self.assertEqual(by_id[self.known.stream_id]["field_keys"], ["x", "y"])
            self.assertEqual(by_id[self.known.stream_id]["nominal_rate_hz"], 100.0)
            self.assertIsNone(by_id[self.unknown.stream_id]["nominal_rate_hz"])

            known_path = output / by_id[self.known.stream_id]["file"]
            with known_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(
                rows[0],
                ["runtime_index", "host_monotonic_ns", "host_unix_ns", "x", "y"],
            )
            self.assertEqual(rows[1], ["1", "200", "2000", "3.0", "4.0"])
            self.assertEqual(rows[2], ["2", "300", "3000", "5.0", "6.0"])

            unknown_path = output / by_id[self.unknown.stream_id]["file"]
            with unknown_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(
                rows,
                [
                    ["runtime_index", "host_monotonic_ns", "host_unix_ns", "ax"],
                    ["0", "250", "2500", "0.25"],
                ],
            )

    def test_existing_csv_output_directory_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "capture"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("existing", encoding="utf-8")
            recorder = CSVStreamRecorder()
            with self.assertRaises(FileExistsError):
                recorder.start(output, (self.known,))
            self.assertEqual(marker.read_text(encoding="utf-8"), "existing")
            self.assertEqual(recorder.state, RecorderState.STOPPED)


class SelectableStreamRecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = StreamSchema("test.signal", ("value",), nominal_rate_hz=1000.0)

    def test_backend_can_change_while_stopped_and_is_locked_while_recording(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            recorder = SelectableStreamRecorder("csv")
            self.assertEqual(recorder.format, "csv")
            output = recorder.start(Path(temp_dir) / "capture.h5", (self.schema,))
            self.assertTrue(output.is_dir())
            self.assertEqual(output.name, "capture")

            with self.assertRaisesRegex(RuntimeError, "while recording"):
                recorder.set_format("hdf5")

            recorder.stop()
            recorder.set_format("hdf5")
            self.assertEqual(recorder.format, "hdf5")
            h5_path = recorder.start(Path(temp_dir) / "second", (self.schema,))
            self.assertEqual(h5_path.suffix, ".h5")
            recorder.stop()
            self.assertTrue(h5_path.is_file())

    def test_store_tap_does_not_change_when_backend_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RealtimeStreamStore((self.schema,))
            recorder = SelectableStreamRecorder("csv")
            tap = StreamStoreTap(store, recorder)

            recorder.start(Path(temp_dir) / "capture", store.schemas())
            row = tap.append(
                self.schema.stream_id,
                host_monotonic_ns=10,
                host_unix_ns=20,
                values=(0.5,),
            )
            recorder.stop()

            self.assertEqual(row.runtime_index, 0)
            self.assertEqual(store.row_count, 1)
            self.assertEqual(recorder.rows_written, 1)
            self.assertEqual(recorder.rows_by_stream(), {self.schema.stream_id: 1})


if __name__ == "__main__":
    unittest.main()
