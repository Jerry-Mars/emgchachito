from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assembly.acquisition.BLE.bwt901_ingest import BWT901RecordIngestor
from assembly.acquisition.BLE.bwt901_worker import BWT901BLEConfig
from assembly.acquisition.BLE.myo_ingest import MyoRecordIngestor, myo_emg_stream_id, myo_imu_stream_id
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore
from assembly.acquisition.serial.w2_ingest import W2RecordIngestor, w2_stream_id
from assembly.acquisition.serial.w2_worker import W2SerialConfig
from assembly.live_random_device_plot_save import (
    MyoDeviceConfig,
    _plot_specs,
    _schemas,
    _validate_configs,
)
from assembly.plot.realtime_provider import BufferedPlotProvider
from assembly.save.recorder import H5StreamRecorder
from assembly.save.store_tap import StreamStoreTap


class LiveRandomDevicePlotSaveTests(unittest.TestCase):
    def test_mixed_device_schemas_plot_and_recorder_share_one_runtime(self) -> None:
        myos = (MyoDeviceConfig("arm", "AA:BB:CC:DD:EE:FF"),)
        w2s = (W2SerialConfig("muscle", "COM9"),)
        bwts = (BWT901BLEConfig("imu", address="11:22:33:44:55:66"),)

        _validate_configs(myos, w2s, bwts)
        schemas = _schemas(myos, w2s, bwts)
        specs = _plot_specs(myos, w2s, bwts)

        self.assertEqual(
            {schema.stream_id for schema in schemas},
            {
                myo_emg_stream_id("arm"),
                myo_imu_stream_id("arm"),
                w2_stream_id("muscle"),
                "bwt901.imu.imu",
            },
        )

        store = RealtimeStreamStore(schemas)
        provider = BufferedPlotProvider(store, specs)
        recorder = H5StreamRecorder()
        tapped_store = StreamStoreTap(store, recorder)

        myo = MyoRecordIngestor(tapped_store, "arm")  # type: ignore[arg-type]
        w2 = W2RecordIngestor(tapped_store, "muscle")  # type: ignore[arg-type]
        bwt = BWT901RecordIngestor(tapped_store, "imu")  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "mixed.h5"
            recorder.start(output, schemas)

            myo.ingest(
                {
                    "stream": "emg",
                    "notification_index": 0,
                    "host_monotonic_ns": 100,
                    "host_unix_ns": 1000,
                    "samples": (tuple(range(8)), tuple(range(8, 16))),
                }
            )
            myo.ingest(
                {
                    "stream": "imu",
                    "sample_index": 0,
                    "host_monotonic_ns": 110,
                    "host_unix_ns": 1010,
                    "quaternion": (1.0, 0.0, 0.0, 0.0),
                    "accelerometer_g": (0.1, 0.2, 0.3),
                    "gyroscope_dps": (1.0, 2.0, 3.0),
                }
            )
            w2.ingest(
                {
                    "packet_index": 0,
                    "mode": "emg_raw",
                    "host_monotonic_ns": 120,
                    "host_unix_ns": 1020,
                    "samples": (10.0, 11.0),
                }
            )
            bwt.ingest(
                {
                    "decoder_sequence": 0,
                    "host_monotonic_ns": 130,
                    "host_unix_ns": 1030,
                    "acceleration_g": (0.1, 0.2, 0.3),
                    "gyroscope_dps": (1.0, 2.0, 3.0),
                    "euler_angle_deg": (4.0, 5.0, 6.0),
                    "raw_int16": tuple(range(9)),
                }
            )
            recorder.stop()

            self.assertTrue(output.exists())

        self.assertEqual(provider.stream_count, 4)
        self.assertEqual(store.row_count, 6)
        self.assertEqual(
            recorder.rows_by_stream(),
            {
                myo_emg_stream_id("arm"): 2,
                myo_imu_stream_id("arm"): 1,
                w2_stream_id("muscle"): 2,
                "bwt901.imu.imu": 1,
            },
        )

    def test_requires_at_least_one_device(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one"):
            _validate_configs((), (), ())


if __name__ == "__main__":
    unittest.main()
