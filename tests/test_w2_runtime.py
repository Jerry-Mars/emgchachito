from __future__ import annotations

import queue
import struct
import time
import unittest

from DeviceInterface.w2_protocol import W2CommandBuilder
from assembly.acquisition.runtime.queue_pump import QueuePump
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore
from assembly.acquisition.runtime.worker_group import WorkerGroup
from assembly.acquisition.serial.w2_ingest import (
    W2RecordIngestor,
    make_w2_stream_schema,
    w2_stream_id,
)
from assembly.acquisition.serial.w2_worker import (
    ResolvedW2SerialConfig,
    SerialW2Worker,
    W2Record,
    W2SerialConfig,
    resolve_w2_config,
    w2_device_id_from_name,
)
from assembly.plot.models import SeriesSpec
from assembly.plot.realtime_provider import BufferedPlotProvider


def make_w2_raw_frame(initial: float, deltas: tuple[int, ...]) -> bytes:
    frame_len_field = 13 + 2 * len(deltas)
    frame = bytearray(
        [
            0xA5,
            frame_len_field,
            0x11,
            frame_len_field ^ 0x11,
            W2CommandBuilder.MODE_EMG_RAW,
        ]
    )
    frame.extend(bytes(6))
    frame.extend(struct.pack("<f", initial))
    for delta in deltas:
        frame.extend(struct.pack("<h", delta))
    frame.append(0x5A)
    return bytes(frame)


class FakeSerialHandle:
    def __init__(self, frames: list[bytes]) -> None:
        self.frames = list(frames)
        self.writes: list[bytes] = []
        self.closed = False
        self.input_reset = False

    def reset_input_buffer(self) -> None:
        self.input_reset = True

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        return None

    def read(self, _size: int) -> bytes:
        if self.frames:
            return self.frames.pop(0)
        time.sleep(0.002)
        return b""

    def close(self) -> None:
        self.closed = True


class FakeSerialBackend:
    EIGHTBITS = 8
    PARITY_NONE = "N"
    STOPBITS_ONE = 1

    def __init__(self, frames_by_port: dict[str, list[bytes]]) -> None:
        self.frames_by_port = frames_by_port
        self.handles: dict[str, FakeSerialHandle] = {}
        self.opened: dict[str, dict[str, object]] = {}

    def Serial(self, port: str, baud_rate: int, **kwargs):
        self.opened[port] = {"baud_rate": baud_rate, **kwargs}
        handle = FakeSerialHandle(self.frames_by_port.get(port, []))
        self.handles[port] = handle
        return handle



def make_w2_identity_frame(device_name: str) -> bytes:
    payload = device_name.encode("utf-8")
    length_field = len(payload) + 2
    frame_type = W2CommandBuilder.ADDRESS_DEVICE_NAME
    return bytes([0xA5, length_field, frame_type, length_field ^ frame_type]) + payload + bytes([0x5A])


def resolved(device_name: str, port: str) -> ResolvedW2SerialConfig:
    return ResolvedW2SerialConfig(
        device_name=device_name,
        device_id=w2_device_id_from_name(device_name),
        port=port,
    )

def wait_until(predicate, timeout_s: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


class W2RuntimeTests(unittest.TestCase):
    def test_protocol_device_name_derives_stable_device_id_independent_of_port(self) -> None:
        for port in ("COM7", "COM12"):
            backend = FakeSerialBackend({port: [make_w2_identity_frame("RunE W2 5")]})
            config = resolve_w2_config(
                W2SerialConfig(port),
                serial_backend=backend,
                identity_timeout_s=0.1,
            )
            self.assertEqual(config.device_name, "RunE W2 5")
            self.assertEqual(config.device_id, "w2_5")
            self.assertEqual(config.port, port)
            self.assertEqual(
                backend.handles[port].writes,
                [
                    W2CommandBuilder.stop_collect(),
                    W2CommandBuilder.read(W2CommandBuilder.ADDRESS_DEVICE_NAME),
                ],
            )

    def test_invalid_protocol_device_name_is_rejected(self) -> None:
        backend = FakeSerialBackend({"COM7": [make_w2_identity_frame("RunE W2") ]})
        with self.assertRaisesRegex(ValueError, "device_name"):
            resolve_w2_config(
                W2SerialConfig("COM7"),
                serial_backend=backend,
                identity_timeout_s=0.1,
            )

    def test_worker_ready_means_serial_and_collect_command_ready_not_first_sample(self) -> None:
        backend = FakeSerialBackend({"COM7": []})
        worker = SerialW2Worker(
            resolved("RunE W2 5", "COM7"),
            serial_backend=backend,
        )

        worker.start()
        self.assertTrue(worker.startup_event.wait(0.5))
        self.assertIsNone(worker.error)
        self.assertTrue(worker.is_alive())
        self.assertEqual(
            backend.handles["COM7"].writes,
            [W2CommandBuilder.start_emg_raw()],
        )
        worker.close(0.5)
        self.assertEqual(
            backend.handles["COM7"].writes,
            [W2CommandBuilder.start_emg_raw(), W2CommandBuilder.stop_collect()],
        )

    def test_serial_worker_preserves_packet_observation_and_protocol_settings(self) -> None:
        frame = make_w2_raw_frame(10.0, (3, -6))
        backend = FakeSerialBackend({"COM7": [frame]})
        records: queue.Queue[W2Record] = queue.Queue()
        worker = SerialW2Worker(
            resolved("RunE W2 5", "COM7"),
            records,
            serial_backend=backend,
        )

        worker.start()
        self.assertTrue(worker.startup_event.wait(0.5))
        self.assertTrue(wait_until(lambda: not records.empty()))
        record = records.get_nowait()
        worker.close(0.5)

        opened = backend.opened["COM7"]
        self.assertEqual(opened["baud_rate"], 256000)
        self.assertEqual(opened["bytesize"], 8)
        self.assertEqual(opened["parity"], "N")
        self.assertEqual(opened["stopbits"], 1)
        self.assertEqual(opened["timeout"], 0.05)
        self.assertEqual(record["packet_index"], 0)
        samples = tuple(record["samples"])  # type: ignore[arg-type]
        self.assertEqual(len(samples), 3)
        self.assertAlmostEqual(samples[0], 10.0)
        self.assertAlmostEqual(samples[1], 10.0 + 3 / 3.1457)
        self.assertAlmostEqual(samples[2], 10.0 - 3 / 3.1457)
        self.assertIsInstance(record["host_monotonic_ns"], int)
        self.assertIsInstance(record["host_unix_ns"], int)
        self.assertTrue(backend.handles["COM7"].input_reset)
        self.assertTrue(backend.handles["COM7"].closed)

    def test_ingestor_expands_packet_as_batch_without_inventing_sample_time(self) -> None:
        schema = make_w2_stream_schema("left")
        store = RealtimeStreamStore((schema,))
        ingestor = W2RecordIngestor(store, "left")
        ingestor.ingest(
            {
                "packet_index": 99,
                "mode": "emg_raw",
                "host_monotonic_ns": 123,
                "host_unix_ns": 456,
                "samples": (1.0, 2.0, 3.0),
            }
        )

        rows = store.tail_samples(w2_stream_id("left"), 10).rows
        self.assertEqual([row.runtime_index for row in rows], [0, 1, 2])
        self.assertEqual([row.host_monotonic_ns for row in rows], [123, 123, 123])
        self.assertEqual([row.values[0] for row in rows], [1.0, 2.0, 3.0])

    def test_two_serial_w2_workers_share_store_without_stream_merging(self) -> None:
        backend = FakeSerialBackend(
            {
                "COM7": [make_w2_raw_frame(10.0, (3,))],
                "COM8": [make_w2_raw_frame(20.0, (-3,))],
            }
        )
        configs = (
            resolved("RunE W2 5", "COM7"),
            resolved("RunE W2 4", "COM8"),
        )
        schemas = tuple(
            make_w2_stream_schema(
                config.device_id,
                nominal_rate_hz=config.nominal_rate_hz,
            )
            for config in configs
        )
        store = RealtimeStreamStore(schemas)

        workers: dict[str, SerialW2Worker] = {}
        pumps: list[QueuePump[W2Record]] = []
        specs: list[SeriesSpec] = []
        records_by_id: dict[str, queue.Queue[W2Record]] = {}

        for config in configs:
            records: queue.Queue[W2Record] = queue.Queue()
            records_by_id[config.device_id] = records
            worker = SerialW2Worker(config, records, serial_backend=backend)
            workers[f"w2.{config.device_id}"] = worker
            ingestor = W2RecordIngestor(store, config.device_id)
            pumps.append(QueuePump(records, ingestor.ingest))
            stream_id = w2_stream_id(config.device_id)
            specs.append(
                SeriesSpec(
                    series_id=f"{stream_id}/value",
                    stream_id=stream_id,
                    field_key="value",
                    label=config.device_id,
                    unit="code",
                    signal_kind="emg",
                    default_plot=True,
                    fixed_range=None,
                )
            )

        group = WorkerGroup(workers)
        provider = BufferedPlotProvider(store, tuple(specs))
        group.start()
        group.wait_ready(0.5)
        self.assertTrue(
            wait_until(lambda: all(not records.empty() for records in records_by_id.values()))
        )

        for pump in pumps:
            pump.drain()

        self.assertEqual(store.stream_count, 2)
        left_rows = store.tail_samples(w2_stream_id("w2_5"), 10).rows
        right_rows = store.tail_samples(w2_stream_id("w2_4"), 10).rows
        self.assertEqual(len(left_rows), 2)
        self.assertEqual(len(right_rows), 2)
        self.assertAlmostEqual(left_rows[0].values[0], 10.0)
        self.assertAlmostEqual(right_rows[0].values[0], 20.0)
        self.assertIsNotNone(provider.get_series_window(specs[0].series_id, 1.0))
        self.assertIsNotNone(provider.get_series_window(specs[1].series_id, 1.0))
        self.assertEqual(group.failures(), {})

        group.close(0.5)
        self.assertTrue(all(handle.closed for handle in backend.handles.values()))


if __name__ == "__main__":
    unittest.main()
