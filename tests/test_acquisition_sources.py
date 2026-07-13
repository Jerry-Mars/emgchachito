from __future__ import annotations

import queue
import threading
import unittest

from fundamental.acquisition import AcquisitionController
from fundamental.messages import AcquisitionState, SampleBatch, SerialConfig, WorkerEvent
from fundamental.sources.ble_w2 import BLEW2Source
from fundamental.sources.serial_ads1299 import SerialADS1299Source


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

    def create_worker(
        self,
        data_queue: queue.Queue[SampleBatch],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        timestamp_offset_s: float = 0.0,
        expected_counter: int | None = None,
    ) -> FakeWorker:
        self.created_with = {
            "data_queue": data_queue,
            "event_queue": event_queue,
            "stop_event": stop_event,
            "timestamp_offset_s": timestamp_offset_s,
            "expected_counter": expected_counter,
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

    def test_sources_expose_data_inspection_text(self) -> None:
        serial_lines = SerialADS1299Source().inspect_data()
        w2_lines = BLEW2Source().inspect_data()

        self.assertTrue(any("SerialWorker" in line for line in serial_lines))
        self.assertTrue(any("ADS1299StreamParser" in line for line in serial_lines))
        self.assertTrue(any("BLEW2Worker" in line for line in w2_lines))
        self.assertTrue(any("W2StreamParser" in line for line in w2_lines))
        self.assertTrue(any("SampleFrame" in line for line in w2_lines))


if __name__ == "__main__":
    unittest.main()
