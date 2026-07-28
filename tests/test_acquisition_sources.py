from __future__ import annotations

import queue
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from fundamental.acquisition import AcquisitionController
from fundamental.messages import AcquisitionState, SerialConfig, WorkerEvent
from fundamental.sources.ble_w2 import (
    BLEW2Source,
    W2DeviceConfig,
    W2SerialDeviceConfig,
)
from fundamental.sources.bwt901 import BWT901DeviceConfig, BWT901Source
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


class FakeManagedWorker(FakeWorker):
    def __init__(self, stop_event: threading.Event) -> None:
        super().__init__()
        self.stop_event = stop_event
        self.ready_event = threading.Event()

    def start(self) -> None:
        super().start()
        self.ready_event.set()

    def join(self, timeout: float | None = None) -> None:
        self.stop_event.set()
        super().join(timeout)


class FakeManagedSource:
    supports_managed_lifecycle = True

    def __init__(self, name: str, stream_id: str) -> None:
        self.name = name
        self.display_name = name
        self.spec = replace(ADS1299_STREAM_SPEC, stream_id=stream_id, display_name=name)
        self.worker: FakeManagedWorker | None = None
        self.control = None

    def display_text(self) -> str:
        return self.display_name

    def inspect_data(self) -> tuple[str, ...]:
        return (self.display_name,)

    def stream_specs(self) -> tuple[StreamSpec, ...]:
        return (self.spec,)

    def capture_metadata(self) -> dict[str, object]:
        return {"fake": self.name}

    def create_worker(
        self,
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        resume_state: CaptureResumeState = CaptureResumeState(),
        *,
        control=None,
    ) -> FakeManagedWorker:
        self.control = control
        self.worker = FakeManagedWorker(stop_event)
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
            "Stop acquisition before changing W2 configuration.",
        )
        self.assertEqual(controller.w2_config.mode, "emg_raw")

    def test_w2_config_updates_ble_source(self) -> None:
        controller = AcquisitionController()

        error = controller.update_w2_config(
            devices=(
                W2DeviceConfig(
                    device_id="arm",
                    transport="ble",
                    address=" ",
                    device_name_filter=" RunE ",
                ),
            ),
            mode="emg_rms",
            sample_rate_hz=0.0,
            scan_timeout_s=0.0,
        )

        self.assertIsNone(error)
        self.assertEqual(controller.w2_config.effective_devices()[0].address, "")
        self.assertEqual(
            controller.w2_config.effective_devices()[0].device_name_filter,
            "RunE",
        )
        self.assertEqual(controller.w2_config.mode, "emg_rms")
        self.assertEqual(controller.w2_config.sample_rate_hz, 0.001)
        self.assertEqual(controller.w2_config.scan_timeout_s, 0.1)
        self.assertEqual(
            controller.update_w2_config(mode="bad"),
            "Unsupported W2 mode: bad",
        )
        self.assertEqual(
            controller.update_w2_config(
                devices=(
                    W2DeviceConfig(
                        device_id="arm",
                        transport="ble",
                        address="",
                        device_name_filter="",
                    ),
                )
            ),
            "W2 BLE address and name filter cannot both be empty.",
        )

    def test_w2_serial_config_supports_multiple_ports_as_one_source(self) -> None:
        controller = AcquisitionController()
        devices = (
            W2SerialDeviceConfig(channel_id="left", port="COM7"),
            W2SerialDeviceConfig(channel_id="right", port="COM8"),
        )

        error = controller.update_w2_config(transport="serial", serial_devices=devices)

        self.assertIsNone(error)
        self.assertEqual(controller.w2_config.transport, "serial")
        self.assertEqual(controller.w2_config.serial_devices, devices)
        self.assertEqual(
            tuple(spec.stream_id for spec in controller.w2_source.stream_specs()),
            ("ble_w2.left.signal", "ble_w2.right.signal"),
        )

    def test_w2_serial_config_rejects_duplicate_channels_and_ports(self) -> None:
        controller = AcquisitionController()

        self.assertEqual(
            controller.update_w2_config(
                transport="serial",
                serial_devices=(
                    W2SerialDeviceConfig(channel_id="ch1", port="COM7"),
                    W2SerialDeviceConfig(channel_id="CH1", port="COM8"),
                ),
            ),
            "W2 device IDs must be unique.",
        )
        self.assertEqual(
            controller.update_w2_config(
                transport="serial",
                serial_devices=(
                    W2SerialDeviceConfig(channel_id="ch1", port="COM7"),
                    W2SerialDeviceConfig(channel_id="ch2", port="com7"),
                ),
            ),
            "Each W2 serial device must use a different Port.",
        )

    def test_sources_expose_data_inspection_text(self) -> None:
        serial_lines = SerialADS1299Source().inspect_data()
        w2_lines = BLEW2Source().inspect_data()
        bwt_lines = BWT901Source().inspect_data()
        myo_lines = MyoSource().inspect_data()

        self.assertTrue(any("SerialWorker" in line for line in serial_lines))
        self.assertTrue(any("ADS1299StreamParser" in line for line in serial_lines))
        self.assertTrue(any("BleGattTransport" in line for line in w2_lines))
        self.assertTrue(any("W2StreamParser" in line for line in w2_lines))
        self.assertTrue(any("StreamBlock" in line for line in w2_lines))
        self.assertTrue(any("BWT901StreamDecoder" in line for line in bwt_lines))
        self.assertTrue(any("MyoWorker" in line for line in myo_lines))

    def test_w2_devices_can_mix_serial_and_ble_without_transport_specific_stream_ids(self) -> None:
        controller = AcquisitionController()
        error = controller.update_w2_config(
            devices=(
                W2DeviceConfig("left", "serial", port="COM7"),
                W2DeviceConfig("right", "ble", address="AA:BB"),
            )
        )
        self.assertIsNone(error)
        self.assertEqual(
            tuple(spec.stream_id for spec in controller.w2_source.stream_specs()),
            ("ble_w2.left.signal", "ble_w2.right.signal"),
        )

    def test_bwt_config_supports_two_explicit_addresses(self) -> None:
        controller = AcquisitionController()
        error = controller.update_bwt901_config(
            devices=(
                BWT901DeviceConfig("imu_1", "AA:BB"),
                BWT901DeviceConfig("imu_2", "CC:DD"),
            )
        )
        self.assertIsNone(error)
        self.assertEqual(len(controller.bwt901_source.stream_specs()), 2)
        self.assertEqual(
            controller.update_bwt901_config(
                devices=(
                    BWT901DeviceConfig("imu_1", "AA:BB"),
                    BWT901DeviceConfig("imu_2", "AA:BB"),
                )
            ),
            "BWT901 BLE addresses must be unique.",
        )

    def test_managed_w2_bwt_sources_share_start_pause_resume_stop_lifecycle(self) -> None:
        controller = AcquisitionController()
        self.assertIsNone(controller.select_sources((BLEW2Source.name, BWT901Source.name)))
        w2 = FakeManagedSource(BLEW2Source.name, "fake.w2")
        bwt = FakeManagedSource(BWT901Source.name, "fake.bwt")
        controller.w2_source = w2  # type: ignore[assignment]
        controller.bwt901_source = bwt  # type: ignore[assignment]

        message = controller.start()
        self.assertIn("connecting", message)
        self.assertEqual(controller.state, AcquisitionState.STARTING)
        self.assertIs(w2.control, bwt.control)
        first_workers = controller.workers

        controller.drain_queues()
        self.assertEqual(controller.state, AcquisitionState.RUNNING)
        self.assertTrue(controller.control.capture_event.is_set())  # type: ignore[union-attr]

        controller.pause()
        self.assertEqual(controller.state, AcquisitionState.PAUSED)
        self.assertFalse(controller.control.capture_event.is_set())  # type: ignore[union-attr]
        self.assertTrue(all(worker.is_alive() for worker in first_workers))

        controller.start()
        self.assertEqual(controller.state, AcquisitionState.RUNNING)
        self.assertEqual(controller.workers, first_workers)
        self.assertTrue(controller.control.capture_event.is_set())  # type: ignore[union-attr]

        controller.stop()
        self.assertEqual(controller.state, AcquisitionState.STOPPED)
        self.assertTrue(all(not worker.is_alive() for worker in first_workers))
        self.assertTrue(Path(controller.last_save_path).parent.name.startswith("experiment_"))

    def test_managed_source_error_stops_every_source(self) -> None:
        controller = AcquisitionController()
        controller.select_sources((BLEW2Source.name, BWT901Source.name))
        w2 = FakeManagedSource(BLEW2Source.name, "fake.w2")
        bwt = FakeManagedSource(BWT901Source.name, "fake.bwt")
        controller.w2_source = w2  # type: ignore[assignment]
        controller.bwt901_source = bwt  # type: ignore[assignment]
        controller.start()
        controller.drain_queues()
        workers = controller.workers

        controller.event_queue.put(
            WorkerEvent("error", "imu disconnected", source_id="bwt901.imu_1")
        )
        controller.drain_queues()

        self.assertEqual(controller.state, AcquisitionState.STOPPED)
        self.assertEqual(controller.last_error, "imu disconnected")
        self.assertTrue(all(not worker.is_alive() for worker in workers))

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
