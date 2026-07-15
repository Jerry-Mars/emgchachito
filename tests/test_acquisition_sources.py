from __future__ import annotations

import queue
import threading
import unittest

from fundamental.acquisition import AcquisitionController
from fundamental.messages import AcquisitionState, SerialConfig, WorkerEvent
from fundamental.sources.ble_w2 import BLEW2Source
from fundamental.sources.myo import MyoSource
from fundamental.sources.serial_ads1299 import ADS1299_STREAM_SPEC, SerialADS1299Source
from fundamental.streams import CaptureResumeState, StreamBlock, StreamSpec


class FakeWorker:
    def __init__(self) -> None:
        self.started = False
        self.alive = False

    def start(self) -> None:
        self.started = True
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        self.alive = False


class FakeSource:
    name = SerialADS1299Source.name
    display_name = "Fake Source"

    def __init__(self) -> None:
        self.worker = FakeWorker()
        self.created_with: dict[str, object] = {}

    def display_text(self) -> str:
        return "Fake source"

    def inspect_data(self) -> tuple[str, ...]:
        return ("Fake inspection",)

    def stream_specs(self) -> tuple[StreamSpec, ...]:
        return (ADS1299_STREAM_SPEC,)

    def capture_metadata(self) -> dict[str, object]:
        return {"fake": True}

    def create_worker(
        self,
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        resume_state: CaptureResumeState = CaptureResumeState(),
    ) -> FakeWorker:
        self.created_with = {
            "data_queue": data_queue,
            "event_queue": event_queue,
            "stop_event": stop_event,
            "resume_state": resume_state,
        }
        return self.worker


class AcquisitionSourceTests(unittest.TestCase):
    def test_controller_starts_worker_from_active_source(self) -> None:
        controller = AcquisitionController()
        fake_source = FakeSource()
        controller.serial_source = fake_source  # type: ignore[assignment]

        message = controller.start()

        self.assertEqual(message, "Acquisition started with Fake source.")
        self.assertEqual(controller.state, AcquisitionState.RUNNING)
        self.assertTrue(fake_source.worker.started)
        self.assertIs(fake_source.created_with["data_queue"], controller.data_queue)
        self.assertIs(fake_source.created_with["event_queue"], controller.event_queue)

        controller.stop()
        self.assertFalse(fake_source.worker.is_alive())

    def test_source_selection_is_blocked_until_stopped(self) -> None:
        controller = AcquisitionController()

        self.assertEqual(controller.source_name, SerialADS1299Source.name)
        self.assertIsNone(controller.select_source(BLEW2Source.name))
        self.assertEqual(controller.source_name, BLEW2Source.name)

        controller.state = AcquisitionState.PAUSED
        self.assertEqual(
            controller.select_source(SerialADS1299Source.name),
            "Stop acquisition before changing source.",
        )
        self.assertEqual(controller.source_name, BLEW2Source.name)

    def test_serial_config_compatibility_alias_updates_serial_source(self) -> None:
        controller = AcquisitionController()

        error = controller.update_config(port=" COM7 ", baud_rate=115200, timeout_s=0.0)

        self.assertIsNone(error)
        self.assertEqual(controller.config, SerialConfig(port="COM7", baud_rate=115200, timeout_s=0.001))

    def test_config_updates_are_blocked_while_paused(self) -> None:
        controller = AcquisitionController()
        controller.state = AcquisitionState.PAUSED

        self.assertEqual(
            controller.update_serial_config(port="COM8"),
            "Stop acquisition before changing serial configuration.",
        )
        self.assertEqual(controller.config.port, SerialConfig().port)

        self.assertEqual(
            controller.update_w2_config(mode="emg_rms"),
            "Stop acquisition before changing W2 BLE configuration.",
        )
        self.assertEqual(controller.w2_config.mode, "emg_raw")

    def test_w2_config_updates_ble_source(self) -> None:
        controller = AcquisitionController()

        error = controller.update_w2_config(
            address=" ",
            device_name_filter=" RunE ",
            mode="emg_rms",
            sample_rate_hz=0.0,
            scan_timeout_s=0.0,
        )

        self.assertIsNone(error)
        self.assertEqual(controller.w2_config.address, "")
        self.assertEqual(controller.w2_config.device_name_filter, "RunE")
        self.assertEqual(controller.w2_config.mode, "emg_rms")
        self.assertEqual(controller.w2_config.sample_rate_hz, 0.001)
        self.assertEqual(controller.w2_config.scan_timeout_s, 0.1)
        self.assertEqual(
            controller.update_w2_config(mode="bad"),
            "Unsupported W2 BLE mode: bad",
        )
        self.assertEqual(
            controller.update_w2_config(address="", device_name_filter=""),
            "W2 BLE address and name filter cannot both be empty.",
        )

    def test_sources_expose_data_inspection_text(self) -> None:
        serial_lines = SerialADS1299Source().inspect_data()
        w2_lines = BLEW2Source().inspect_data()
        myo_lines = MyoSource().inspect_data()

        self.assertTrue(any("SerialWorker" in line for line in serial_lines))
        self.assertTrue(any("ADS1299StreamParser" in line for line in serial_lines))
        self.assertTrue(any("BLEW2Worker" in line for line in w2_lines))
        self.assertTrue(any("W2StreamParser" in line for line in w2_lines))
        self.assertTrue(any("StreamBlock" in line for line in w2_lines))
        self.assertTrue(any("MyoWorker" in line for line in myo_lines))

    def test_myo_config_requires_one_stream(self) -> None:
        controller = AcquisitionController()

        self.assertEqual(
            controller.update_myo_config(enable_emg=False, enable_imu=False),
            "Enable at least one Myo data stream.",
        )
        self.assertIsNone(
            controller.update_myo_config(
                address=" AA:BB ",
                enable_emg=True,
                enable_imu=False,
            )
        )
        self.assertEqual(controller.myo_config.address, "AA:BB")
        self.assertTrue(controller.myo_config.enable_emg)
        self.assertFalse(controller.myo_config.enable_imu)


if __name__ == "__main__":
    unittest.main()
