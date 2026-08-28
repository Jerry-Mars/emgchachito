from __future__ import annotations

import queue
import struct
import time
import unittest
from types import SimpleNamespace

from assembly.acquisition.BLE.bwt901_ingest import (
    BWT901RecordIngestor,
    bwt901_stream_id,
    make_bwt901_stream_schema,
)
from assembly.acquisition.BLE.bwt901_worker import (
    BWT901BLEConfig,
    BWT901BLEWorker,
    BWT901Record,
)
from assembly.acquisition.runtime.queue_pump import QueuePump
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore
from assembly.acquisition.runtime.worker_group import WorkerGroup
from assembly.plot.models import SeriesSpec
from assembly.plot.realtime_provider import BufferedPlotProvider


def make_bwt901_frame(raw: tuple[int, int, int, int, int, int, int, int, int]) -> bytes:
    return b"\x55\x61" + struct.pack("<9h", *raw)


class FakeScanner:
    def __init__(self, callback, devices) -> None:
        self.callback = callback
        self.devices = devices
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True
        for device in self.devices:
            advertisement = SimpleNamespace(local_name=device.name)
            self.callback(device, advertisement)

    async def stop(self) -> None:
        self.stopped = True


class FakeScannerFactory:
    def __init__(self, devices) -> None:
        self.devices = tuple(devices)
        self.instances: list[FakeScanner] = []

    def __call__(self, callback):
        scanner = FakeScanner(callback, self.devices)
        self.instances.append(scanner)
        return scanner


class FakeClient:
    def __init__(self, device, frame: bytes, disconnected_callback=None, **_kwargs) -> None:
        self.device = device
        self.frame = frame
        self.disconnected_callback = disconnected_callback
        self.is_connected = False
        self.notify_started = False
        self.notify_stopped = False
        self.disconnected = False

    async def connect(self) -> None:
        self.is_connected = True

    async def start_notify(self, _uuid: str, callback) -> None:
        self.notify_started = True
        callback(None, bytearray(self.frame))

    async def stop_notify(self, _uuid: str) -> None:
        self.notify_stopped = True

    async def disconnect(self) -> None:
        was_connected = self.is_connected
        self.is_connected = False
        self.disconnected = True
        if was_connected and self.disconnected_callback is not None:
            self.disconnected_callback(self)


class FakeClientFactory:
    def __init__(self, frames_by_address: dict[str, bytes]) -> None:
        self.frames_by_address = frames_by_address
        self.instances: list[FakeClient] = []

    def __call__(self, device, **kwargs):
        client = FakeClient(
            device,
            self.frames_by_address[device.address],
            **kwargs,
        )
        self.instances.append(client)
        return client


def wait_until(predicate, timeout_s: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


class BWT901RuntimeTests(unittest.TestCase):
    def test_schema_is_explicitly_unknown_rate(self) -> None:
        schema = make_bwt901_stream_schema("imu_1")
        self.assertEqual(schema.stream_id, "bwt901.imu_1.imu")
        self.assertIsNone(schema.nominal_rate_hz)
        self.assertEqual(len(schema.field_keys), 9)

    def test_ingestor_preserves_host_observation_time_without_decoder_sequence_field(self) -> None:
        schema = make_bwt901_stream_schema("imu_1")
        store = RealtimeStreamStore((schema,))
        ingestor = BWT901RecordIngestor(store, "imu_1")
        record: BWT901Record = {
            "decoder_sequence": 42,
            "host_monotonic_ns": 1_250_000_000,
            "host_unix_ns": 9_000_000_000,
            "acceleration_g": (1.0, 2.0, 3.0),
            "gyroscope_dps": (4.0, 5.0, 6.0),
            "euler_angle_deg": (7.0, 8.0, 9.0),
            "raw_int16": tuple(range(9)),
        }

        ingestor.ingest(record)
        row = store.latest(schema.stream_id)
        assert row is not None
        self.assertEqual(row.runtime_index, 0)
        self.assertEqual(row.host_monotonic_ns, 1_250_000_000)
        self.assertEqual(row.values, tuple(float(value) for value in range(1, 10)))
        self.assertNotIn("decoder_sequence", schema.field_keys)

    def test_unknown_rate_plot_uses_direct_host_observation_time(self) -> None:
        schema = make_bwt901_stream_schema("imu_1")
        store = RealtimeStreamStore((schema,))
        ingestor = BWT901RecordIngestor(store, "imu_1")
        for host_ns, value in ((1_000_000_000, 1.0), (1_275_000_000, 2.0)):
            ingestor.ingest(
                {
                    "decoder_sequence": 1,
                    "host_monotonic_ns": host_ns,
                    "host_unix_ns": host_ns + 10_000,
                    "acceleration_g": (value, 0.0, 0.0),
                    "gyroscope_dps": (0.0, 0.0, 0.0),
                    "euler_angle_deg": (0.0, 0.0, 0.0),
                    "raw_int16": (0,) * 9,
                }
            )

        spec = SeriesSpec(
            series_id="imu_1/acc_x",
            stream_id=schema.stream_id,
            field_key="acc_x_g",
            label="Accel X",
            unit="g",
            signal_kind="acceleration",
            default_plot=True,
            fixed_range=(-16.0, 16.0),
        )
        provider = BufferedPlotProvider(store, (spec,))
        window = provider.get_series_window(spec.series_id, 1.0)
        assert window is not None

        self.assertEqual(window.time_s, [1.0, 1.275])
        self.assertEqual(window.values, [1.0, 2.0])
        self.assertEqual(window.reference_time_s, 1.275)

    def test_worker_uses_existing_decoder_and_cleans_up_ble(self) -> None:
        device = SimpleNamespace(address="AA:BB", name="WT901BLE-test")
        raw = (16384, -16384, 0, 16384, -16384, 0, 16384, -16384, 0)
        frame = make_bwt901_frame(raw)
        scanner_factory = FakeScannerFactory((device,))
        client_factory = FakeClientFactory({"AA:BB": frame})
        records: queue.Queue[BWT901Record] = queue.Queue()
        worker = BWT901BLEWorker(
            BWT901BLEConfig("imu_1", address="AA:BB"),
            records,
            scanner_factory=scanner_factory,
            client_factory=client_factory,
        )

        worker.start()
        self.assertTrue(worker.startup_event.wait(0.5))
        self.assertIsNone(worker.error)
        self.assertTrue(worker.is_alive())
        self.assertTrue(wait_until(lambda: not records.empty()))
        record = records.get_nowait()

        self.assertEqual(record["decoder_sequence"], 1)
        self.assertEqual(record["acceleration_g"], (8.0, -8.0, 0.0))
        self.assertEqual(record["gyroscope_dps"], (1000.0, -1000.0, 0.0))
        self.assertEqual(record["euler_angle_deg"], (90.0, -90.0, 0.0))
        self.assertEqual(record["raw_int16"], raw)
        self.assertIsInstance(record["host_monotonic_ns"], int)
        self.assertIsInstance(record["host_unix_ns"], int)

        worker.close(0.5)
        client = client_factory.instances[0]
        self.assertTrue(client.notify_started)
        self.assertTrue(client.notify_stopped)
        self.assertTrue(client.disconnected)
        self.assertTrue(scanner_factory.instances[0].stopped)

    def test_callback_failure_during_notify_is_startup_failure(self) -> None:
        device = SimpleNamespace(address="AA:BB", name="WT901BLE-test")
        frame = make_bwt901_frame((0,) * 9) + make_bwt901_frame((1,) * 9)
        scanner_factory = FakeScannerFactory((device,))
        client_factory = FakeClientFactory({"AA:BB": frame})
        records: queue.Queue[BWT901Record] = queue.Queue(maxsize=1)
        worker = BWT901BLEWorker(
            BWT901BLEConfig("imu_1", address="AA:BB"),
            records,
            scanner_factory=scanner_factory,
            client_factory=client_factory,
        )

        worker.start()
        self.assertTrue(worker.stopped_event.wait(0.5))
        self.assertIsNotNone(worker.error)
        self.assertFalse(worker.is_alive())
        self.assertTrue(client_factory.instances[0].disconnected)

    def test_two_ble_workers_share_worker_group_and_store(self) -> None:
        left_device = SimpleNamespace(address="AA:01", name="WT-left")
        right_device = SimpleNamespace(address="AA:02", name="WT-right")
        scanner_factory = FakeScannerFactory((left_device, right_device))
        frames = {
            "AA:01": make_bwt901_frame((100, 0, 0, 0, 0, 0, 0, 0, 0)),
            "AA:02": make_bwt901_frame((200, 0, 0, 0, 0, 0, 0, 0, 0)),
        }
        client_factory = FakeClientFactory(frames)
        configs = (
            BWT901BLEConfig("left", address="AA:01"),
            BWT901BLEConfig("right", address="AA:02"),
        )
        schemas = tuple(make_bwt901_stream_schema(config.device_id) for config in configs)
        store = RealtimeStreamStore(schemas)
        workers: dict[str, BWT901BLEWorker] = {}
        pumps: list[QueuePump[BWT901Record]] = []
        records_by_id: dict[str, queue.Queue[BWT901Record]] = {}

        for config in configs:
            records: queue.Queue[BWT901Record] = queue.Queue()
            records_by_id[config.device_id] = records
            worker = BWT901BLEWorker(
                config,
                records,
                scanner_factory=scanner_factory,
                client_factory=client_factory,
            )
            workers[f"bwt901.{config.device_id}"] = worker
            ingestor = BWT901RecordIngestor(store, config.device_id)
            pumps.append(QueuePump(records, ingestor.ingest))

        group = WorkerGroup(workers)
        group.start()
        group.wait_ready(1.0)
        self.assertTrue(
            wait_until(lambda: all(not records.empty() for records in records_by_id.values()))
        )
        for pump in pumps:
            pump.drain()

        left = store.latest(bwt901_stream_id("left"))
        right = store.latest(bwt901_stream_id("right"))
        assert left is not None and right is not None
        self.assertNotEqual(left.values[0], right.values[0])
        self.assertEqual(group.failures(), {})

        group.close(1.0)
        self.assertTrue(all(client.disconnected for client in client_factory.instances))


if __name__ == "__main__":
    unittest.main()
