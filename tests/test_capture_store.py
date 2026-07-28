from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path

from fundamental.capture_store import CaptureStore
from fundamental.csv_writer import default_capture_path, save_capture, save_stimulus_log
from fundamental.sources.ble_w2 import BLEW2Source, W2BLEConfig, W2DeviceConfig
from fundamental.sources.bwt901 import BWT901BLEConfig, BWT901DeviceConfig, BWT901Source
from fundamental.sources.myo import MYO_EMG_STREAM_SPEC, MYO_IMU_STREAM_SPEC
from fundamental.sources.serial_ads1299 import ADS1299_STREAM_SPEC
from fundamental.streams import StreamBlock


class CaptureStoreTests(unittest.TestCase):
    def test_default_capture_path_creates_one_experiment_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp):
            path = default_capture_path(create_directory=True)
            self.assertTrue(path.parent.is_dir())
            self.assertTrue(path.parent.name.startswith("experiment_"))
            self.assertEqual(path.name, "capture.csv")

    def test_w2_and_bwt_series_are_exposed_to_the_generic_plot_catalog(self) -> None:
        w2 = BLEW2Source(
            W2BLEConfig(
                devices=(
                    W2DeviceConfig("left", "serial", port="COM7"),
                    W2DeviceConfig("right", "serial", port="COM8"),
                )
            )
        )
        bwt = BWT901Source(
            BWT901BLEConfig(devices=(BWT901DeviceConfig("imu_1", "AA:BB"),))
        )
        store = CaptureStore(stream_specs=(*w2.stream_specs(), *bwt.stream_specs()))

        series_ids = {series.series_id for series in store.series_specs()}
        self.assertIn("ble_w2.left.signal/value", series_ids)
        self.assertIn("ble_w2.right.signal/value", series_ids)
        self.assertIn("bwt901.imu_1.imu/acc_x_g", series_ids)
        self.assertIn("bwt901.imu_1.imu/gyro_z_dps", series_ids)
        self.assertIn("bwt901.imu_1.imu/angle_y_deg", series_ids)

    def test_w2_and_bwt_save_as_independent_files_in_one_experiment_folder(self) -> None:
        w2_spec = BLEW2Source(
            W2BLEConfig(devices=(W2DeviceConfig("w2_1", "serial", port="COM7"),))
        ).stream_specs()[0]
        bwt_spec = BWT901Source().stream_specs()[0]
        store = CaptureStore(stream_specs=(w2_spec, bwt_spec))
        store.append_block(StreamBlock(w2_spec, (0.0,), ((12.5,),)))
        store.append_block(
            StreamBlock(
                bwt_spec,
                (0.001,),
                ((1, 0.1, 0.2, 0.3, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0),),
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            experiment = Path(tmp) / "experiment_test"
            result = save_capture(
                experiment / "capture.csv",
                store.snapshots(),
                stimulus_code_for_sample=lambda _stream_id, _row_index, _time_s: 3,
            )
            names = {stream.path.name for stream in result.streams}
            self.assertEqual(
                names,
                {"capture.ble_w2_w2_1_signal.csv", "capture.bwt901_imu_1_imu.csv"},
            )
            imu_path = next(
                stream.path for stream in result.streams if stream.stream_id.startswith("bwt901")
            )
            self.assertEqual(
                imu_path.read_text(encoding="utf-8").splitlines()[0],
                "time_s,sequence,stimulus_code,acc_x_g,acc_y_g,acc_z_g,gyro_x_dps,gyro_y_dps,"
                "gyro_z_dps,angle_x_deg,angle_y_deg,angle_z_deg",
            )
            w2_path = next(
                stream.path for stream in result.streams if stream.stream_id.startswith("ble_w2")
            )
            self.assertEqual(
                w2_path.read_text(encoding="utf-8").splitlines()[0],
                "time_s,stimulus_code,value",
            )

    def test_streams_keep_independent_rates_and_series_windows(self) -> None:
        store = CaptureStore(
            plot_buffer_size=8,
            stream_specs=(MYO_EMG_STREAM_SPEC, MYO_IMU_STREAM_SPEC),
        )
        store.append_block(
            StreamBlock(
                MYO_EMG_STREAM_SPEC,
                (0.0, 0.005),
                (
                    (0.001, 1, 2, 3, 4, 5, 6, 7, 8),
                    (0.001, 9, 10, 11, 12, 13, 14, 15, 16),
                ),
            )
        )
        store.append_block(
            StreamBlock(
                MYO_IMU_STREAM_SPEC,
                (0.002,),
                ((0.002, 1.0, 0.0, 0.0, 0.0, 0.1, 0.2, 0.3, 1.0, 2.0, 3.0),),
            )
        )

        self.assertEqual(store.row_count, 3)
        self.assertEqual(store.stream_row_counts(), {"myo.emg": 2, "myo.imu": 1})
        self.assertEqual(len(store.series_specs()), 18)
        emg_window = store.get_series_window("myo.emg/emg_ch1_code", 1.0)
        assert emg_window is not None
        self.assertEqual(emg_window.time_s, [0.0, 0.005])
        self.assertEqual(emg_window.values, [1.0, 9.0])
        gyro_window = store.get_series_window("myo.imu/gyro_z_dps", 1.0)
        assert gyro_window is not None
        self.assertEqual(gyro_window.values, [3.0])

        resume = store.resume_state()
        self.assertEqual(resume.latest_time_s, 0.005)
        self.assertEqual(resume.cursor("myo.emg").row_count, 2)  # type: ignore[union-attr]

    def test_schema_csv_writer_saves_two_raw_stream_files(self) -> None:
        store = CaptureStore(stream_specs=(MYO_EMG_STREAM_SPEC, MYO_IMU_STREAM_SPEC))
        store.append_block(
            StreamBlock(
                MYO_EMG_STREAM_SPEC,
                (0.0,),
                ((0.000123456, 1, 2, 3, 4, 5, 6, 7, 8),),
            )
        )
        store.append_block(
            StreamBlock(
                MYO_IMU_STREAM_SPEC,
                (0.002,),
                ((0.002123456, 1.0, 0.0, 0.0, 0.0, 0.1, 0.2, 0.3, 1.0, 2.0, 3.0),),
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "capture.csv"
            result = save_capture(
                base,
                store.snapshots(),
                stimulus_code_for_time=lambda _time_s: 7,
                metadata={"source": "ble_myo"},
            )

            self.assertEqual(
                {stream.path.name for stream in result.streams},
                {"capture.myo_emg.csv", "capture.myo_imu.csv"},
            )
            emg_path = next(stream.path for stream in result.streams if stream.stream_id == "myo.emg")
            emg_lines = emg_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                emg_lines[0],
                "time_s,host_rx_time_s,stimulus_code,emg_ch1_code,emg_ch2_code,"
                "emg_ch3_code,emg_ch4_code,emg_ch5_code,emg_ch6_code,emg_ch7_code,"
                "emg_ch8_code",
            )
            self.assertIn("0.000123456,7,1,2,3,4,5,6,7,8", emg_lines[1])
            metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["source"], "ble_myo")
            self.assertEqual({stream["stream_id"] for stream in metadata["streams"]}, {"myo.emg", "myo.imu"})

    def test_sample_stimulus_resolver_uses_each_stream_row_boundary(self) -> None:
        store = CaptureStore(stream_specs=(MYO_EMG_STREAM_SPEC, MYO_IMU_STREAM_SPEC))
        store.append_block(
            StreamBlock(
                MYO_EMG_STREAM_SPEC,
                (0.0, 0.1, 0.2),
                (
                    (0.0, 1, 2, 3, 4, 5, 6, 7, 8),
                    (0.1, 1, 2, 3, 4, 5, 6, 7, 8),
                    (0.2, 1, 2, 3, 4, 5, 6, 7, 8),
                ),
            )
        )
        store.append_block(
            StreamBlock(
                MYO_IMU_STREAM_SPEC,
                (0.0, 0.1, 0.2),
                (
                    (0.0, 1.0, 0.0, 0.0, 0.0, 0.1, 0.2, 0.3, 1.0, 2.0, 3.0),
                    (0.1, 1.0, 0.0, 0.0, 0.0, 0.1, 0.2, 0.3, 1.0, 2.0, 3.0),
                    (0.2, 1.0, 0.0, 0.0, 0.0, 0.1, 0.2, 0.3, 1.0, 2.0, 3.0),
                ),
            )
        )
        boundaries = {"myo.emg": 2, "myo.imu": 1}

        with tempfile.TemporaryDirectory() as tmp:
            result = save_capture(
                Path(tmp) / "capture.csv",
                store.snapshots(),
                stimulus_code_for_sample=lambda stream_id, row_index, _time_s: (
                    1 if row_index < boundaries[stream_id] else 2
                ),
            )
            codes_by_stream: dict[str, list[str]] = {}
            for saved_stream in result.streams:
                lines = saved_stream.path.read_text(encoding="utf-8").splitlines()
                header = lines[0].split(",")
                code_index = header.index("stimulus_code")
                codes_by_stream[saved_stream.stream_id] = [
                    line.split(",")[code_index] for line in lines[1:]
                ]

        self.assertEqual(codes_by_stream["myo.emg"], ["1", "1", "2"])
        self.assertEqual(codes_by_stream["myo.imu"], ["1", "2", "2"])

    def test_time_and_sample_stimulus_resolvers_are_mutually_exclusive(self) -> None:
        store = CaptureStore(stream_specs=(MYO_EMG_STREAM_SPEC,))
        store.append_block(
            StreamBlock(
                MYO_EMG_STREAM_SPEC,
                (0.0,),
                ((0.0, 1, 2, 3, 4, 5, 6, 7, 8),),
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "either"):
                save_capture(
                    Path(tmp) / "capture.csv",
                    store.snapshots(),
                    stimulus_code_for_time=lambda _time_s: 1,
                    stimulus_code_for_sample=lambda _stream_id, _row_index, _time_s: 1,
                )

    def test_stimulus_sidecar_adds_deterministic_scalar_miil_fields(self) -> None:
        rows = [
            {
                "event_index": 1,
                "stimulus_code": -1,
                "planned_code": 2,
                "label": "knee_flexion",
                "start_time_s": 0.0,
                "end_time_s": 5.0,
                "status": "dropped",
                "original_code": 2,
                "action_key": "knee_flexion",
                "drop_pressed_at_time_s": 3.5,
                "start_rows": {"myo.emg": 0},
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            path, row_count = save_stimulus_log(Path(tmp) / "capture.stimulus.csv", rows)
            lines = path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(row_count, 1)
        self.assertEqual(
            lines[0],
            "event_index,stimulus_code,planned_code,label,start_time_s,end_time_s,status,"
            "action_key,drop_pressed_at_time_s,original_code",
        )
        self.assertNotIn("start_rows", lines[0])
        self.assertEqual(
            lines[1],
            "1,-1,2,knee_flexion,0.000000,5.000000,dropped,knee_flexion,3.500000,2",
        )

    def test_ads_generic_export_keeps_existing_header(self) -> None:
        store = CaptureStore(stream_specs=(ADS1299_STREAM_SPEC,))
        store.append_block(
            StreamBlock(
                ADS1299_STREAM_SPEC,
                (0.0,),
                ((10, 0, 4, 1, 2, 3, 4, 5, 6, 7, 8),),
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            result = save_capture(Path(tmp) / "ads.csv", store.snapshots())
            lines = result.streams[0].path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(
            lines[0],
            "time_s,frame_counter,dropped_frames_before,emg_channel_count,"
            "ch1_code,ch2_code,ch3_code,ch4_code,ch5_code,ch6_code,ch7_code,ch8_code",
        )


if __name__ == "__main__":
    unittest.main()
