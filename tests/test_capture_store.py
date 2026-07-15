from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fundamental.capture_store import CaptureStore
from fundamental.csv_writer import save_capture
from fundamental.sources.myo import MYO_EMG_STREAM_SPEC, MYO_IMU_STREAM_SPEC
from fundamental.sources.serial_ads1299 import ADS1299_STREAM_SPEC
from fundamental.streams import StreamBlock


class CaptureStoreTests(unittest.TestCase):
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
