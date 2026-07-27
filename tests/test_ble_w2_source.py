from __future__ import annotations

import asyncio
import queue
import struct
import threading
import unittest
from types import SimpleNamespace

from DeviceInterface.w2_protocol import W2CommandBuilder, W2RawPacket, W2RmsPacket, W2StreamParser
import fundamental.sources.ble_w2 as ble_w2_module
from fundamental.sources.ble_w2 import (
    BLEW2Source,
    BLEW2Worker,
    DEFAULT_W2_SERIAL_BAUD_RATE,
    SerialW2Worker,
    W2BLEConfig,
    W2SerialDeviceConfig,
    W2StreamAdapter,
    W2WorkerGroup,
    w2_stream_spec,
)


def make_w2_raw_frame(mode: int, initial: float, deltas: tuple[int, ...]) -> bytes:
    frame_len_field = 13 + 2 * len(deltas)
    frame = bytearray([0xA5, frame_len_field, 0x11, frame_len_field ^ 0x11, mode])
    frame.extend(bytes(6))
    frame.extend(struct.pack("<f", initial))
    for delta in deltas:
        frame.extend(struct.pack("<h", delta))
    frame.append(0x5A)
    assert len(frame) == frame_len_field + 3
    return bytes(frame)


def make_w2_rms_frame(rms: int) -> bytes:
    frame_len_field = 17
    frame = bytearray([0xA5, frame_len_field, 0x11, frame_len_field ^ 0x11, 0x01])
    frame.extend(bytes(12))
    frame.extend([(rms >> 8) & 0xFF, rms & 0xFF])
    frame.append(0x5A)
    assert len(frame) == frame_len_field + 3
    return bytes(frame)


class W2CommandBuilderTests(unittest.TestCase):
    def test_start_and_stop_command_bytes_match_demo_protocol(self) -> None:
        self.assertEqual(
            W2CommandBuilder.stop_collect(),
            bytes.fromhex("AA 0C 80 11 00 00 00 00 00 00 00 00 00 26 BB"),
        )
        self.assertEqual(
            W2CommandBuilder.start_emg_rms(),
            bytes.fromhex("AA 0C 80 11 01 00 00 00 00 00 00 00 00 27 BB"),
        )
        self.assertEqual(
            W2CommandBuilder.start_emg_raw(),
            bytes.fromhex("AA 0C 80 11 03 00 00 00 00 00 00 00 00 25 BB"),
        )
        self.assertEqual(
            W2CommandBuilder.start_eeg_raw(),
            bytes.fromhex("AA 0C 80 11 04 00 00 00 00 00 00 00 00 22 BB"),
        )

    def test_read_command_uses_read_opcode(self) -> None:
        self.assertEqual(W2CommandBuilder.read(W2CommandBuilder.ADDRESS_POWER), bytes.fromhex("AA 03 81 0B 32 BB"))


class W2StreamParserTests(unittest.TestCase):
    def test_parser_buffers_split_raw_frame(self) -> None:
        parser = W2StreamParser()
        frame = make_w2_raw_frame(W2CommandBuilder.MODE_EMG_RAW, 10.0, (3, -6))

        self.assertEqual(parser.feed(frame[:8]), [])
        packets = parser.feed(frame[8:])

        self.assertEqual(len(packets), 1)
        packet = packets[0]
        self.assertIsInstance(packet, W2RawPacket)
        assert isinstance(packet, W2RawPacket)
        self.assertEqual(packet.mode, W2CommandBuilder.MODE_EMG_RAW)
        self.assertEqual(len(packet.values), 3)
        self.assertAlmostEqual(packet.values[0], 10.0)
        self.assertAlmostEqual(packet.values[1], 10.0 + 3 / 3.1457)
        self.assertAlmostEqual(packet.values[2], 10.0 - 3 / 3.1457)

    def test_parser_handles_merged_raw_and_rms_frames(self) -> None:
        parser = W2StreamParser()
        raw_frame = make_w2_raw_frame(W2CommandBuilder.MODE_EEG_RAW, 1.5, (4,))
        rms_frame = make_w2_rms_frame(513)

        packets = parser.feed(raw_frame + rms_frame)

        self.assertEqual(len(packets), 2)
        self.assertIsInstance(packets[0], W2RawPacket)
        self.assertIsInstance(packets[1], W2RmsPacket)
        assert isinstance(packets[1], W2RmsPacket)
        self.assertEqual(packets[1].rms, 513)

    def test_bad_checksum_resyncs_to_next_valid_frame(self) -> None:
        parser = W2StreamParser()
        bad_frame = bytearray(make_w2_rms_frame(10))
        bad_frame[3] ^= 0xFF

        packets = parser.feed(bytes(bad_frame) + make_w2_rms_frame(11))

        self.assertEqual(len(packets), 1)
        self.assertEqual(parser.bad_checksum_count, 1)
        assert isinstance(packets[0], W2RmsPacket)
        self.assertEqual(packets[0].rms, 11)


class W2StreamAdapterTests(unittest.TestCase):
    def test_adapter_keeps_one_native_value_series_without_zero_padding(self) -> None:
        config = W2BLEConfig(sample_rate_hz=1000.0)
        adapter = W2StreamAdapter(w2_stream_spec(config), sample_rate_hz=1000.0)
        block = adapter.packet_to_block(W2RawPacket(W2CommandBuilder.MODE_EMG_RAW, (1.2, 2.7)))

        self.assertEqual(block.time_s, (0.0, 0.001))
        self.assertEqual(block.rows, ((1.2,), (2.7,)))
        self.assertEqual(block.spec.stream_id, "ble_w2.signal")


class BLEW2SourceTests(unittest.TestCase):
    def test_default_config_scans_by_name_instead_of_using_demo_address(self) -> None:
        config = W2BLEConfig()

        self.assertEqual(config.address, "")
        self.assertEqual(config.device_name_filter, "RunE W2")

    def test_source_builds_worker_with_config(self) -> None:
        config = W2BLEConfig(address="AA:BB", mode="emg_rms")
        source = BLEW2Source(config=config)

        worker = source.create_worker(
            data_queue=queue.Queue(),
            event_queue=queue.Queue(),
            stop_event=threading.Event(),
        )

        self.assertIsInstance(worker, BLEW2Worker)
        self.assertEqual(worker.config.address, "AA:BB")
        self.assertEqual(worker.config.mode, "emg_rms")

    def test_serial_source_builds_one_unique_stream_and_worker_per_port(self) -> None:
        config = W2BLEConfig(
            transport="serial",
            serial_devices=(
                W2SerialDeviceConfig(channel_id="left", port="COM7"),
                W2SerialDeviceConfig(channel_id="right", port="COM8"),
            ),
        )
        source = BLEW2Source(config=config)

        specs = source.stream_specs()
        worker = source.create_worker(
            data_queue=queue.Queue(),
            event_queue=queue.Queue(),
            stop_event=threading.Event(),
        )

        self.assertEqual(
            tuple(spec.stream_id for spec in specs),
            ("ble_w2.serial.left.signal", "ble_w2.serial.right.signal"),
        )
        self.assertEqual(tuple(spec.fields[0].label for spec in specs), ("left EMG Raw", "right EMG Raw"))
        self.assertIsInstance(worker, W2WorkerGroup)
        assert isinstance(worker, W2WorkerGroup)
        self.assertEqual(len(worker.workers), 2)
        self.assertTrue(all(isinstance(child, SerialW2Worker) for child in worker.workers))
        self.assertEqual(source.capture_metadata()["transport"], "serial")


class W2SerialConfigTests(unittest.TestCase):
    def test_w2_serial_default_baud_is_25600_without_changing_other_defaults(self) -> None:
        device = W2SerialDeviceConfig()
        config = W2BLEConfig()

        self.assertEqual(DEFAULT_W2_SERIAL_BAUD_RATE, 25600)
        self.assertEqual(config.serial_baud_rate, 25600)
        self.assertEqual(device.port, "COM5")
        self.assertEqual(config.serial_timeout_s, 0.05)


class FailingStopClient:
    async def write_gatt_char(self, _uuid, _data) -> None:
        raise RuntimeError("write failed")

    async def stop_notify(self, _uuid) -> None:
        raise RuntimeError("notify failed")


class UnexpectedBleakClient:
    created = False

    def __init__(self, _address) -> None:
        type(self).created = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        return None


class TrackingStopClient:
    def __init__(self) -> None:
        self.write_count = 0
        self.stop_notify_count = 0

    async def write_gatt_char(self, _uuid, _data) -> None:
        self.write_count += 1

    async def stop_notify(self, _uuid) -> None:
        self.stop_notify_count += 1


class FindingScanner:
    device = SimpleNamespace(name="RunE W2 T1", address="AA:BB:CC")

    @classmethod
    async def find_device_by_filter(cls, predicate, timeout: float):
        advertisement = SimpleNamespace(local_name="RunE W2 T1")
        return cls.device if predicate(cls.device, advertisement) else None

    @classmethod
    async def find_device_by_address(cls, address: str, timeout: float):
        return cls.device if address == cls.device.address else None


class BLEW2WorkerTests(unittest.TestCase):
    def test_name_scan_resolves_demo_family_device_and_keeps_ble_device(self) -> None:
        worker = BLEW2Worker(
            config=W2BLEConfig(address="", device_name_filter="RunE W2"),
            data_queue=queue.Queue(),
            event_queue=queue.Queue(),
            stop_event=threading.Event(),
        )
        old_scanner = ble_w2_module.BleakScanner
        ble_w2_module.BleakScanner = FindingScanner  # type: ignore[assignment]
        try:
            address = asyncio.run(worker._resolve_address())
        finally:
            ble_w2_module.BleakScanner = old_scanner

        self.assertEqual(address, "AA:BB:CC")
        self.assertIs(worker._resolved_device, FindingScanner.device)

    def test_start_cancelled_after_address_resolution_does_not_connect(self) -> None:
        event_queue: queue.Queue = queue.Queue()
        stop_event = threading.Event()
        worker = BLEW2Worker(
            config=W2BLEConfig(address="AA:BB"),
            data_queue=queue.Queue(),
            event_queue=event_queue,
            stop_event=stop_event,
        )

        async def fake_resolve_address() -> str:
            stop_event.set()
            return "AA:BB"

        worker._resolve_address = fake_resolve_address  # type: ignore[method-assign]
        old_client = ble_w2_module.BleakClient
        UnexpectedBleakClient.created = False
        ble_w2_module.BleakClient = UnexpectedBleakClient  # type: ignore[assignment]
        try:
            asyncio.run(worker._run_async())
        finally:
            ble_w2_module.BleakClient = old_client

        self.assertFalse(UnexpectedBleakClient.created)
        self.assertIn("cancelled before connection", event_queue.get_nowait().message)

    def test_parser_counter_log_only_emits_when_counters_change(self) -> None:
        event_queue: queue.Queue = queue.Queue()
        worker = BLEW2Worker(
            config=W2BLEConfig(),
            data_queue=queue.Queue(),
            event_queue=event_queue,
            stop_event=threading.Event(),
        )

        worker.parser.bad_checksum_count = 1
        worker._log_parser_counters_if_changed()
        worker._log_parser_counters_if_changed()

        self.assertEqual(event_queue.qsize(), 1)
        self.assertIn("bad_checksum=1", event_queue.get_nowait().message)

    def test_stop_client_flushes_rows_even_when_cleanup_commands_fail(self) -> None:
        data_queue: queue.Queue = queue.Queue()
        event_queue: queue.Queue = queue.Queue()
        worker = BLEW2Worker(
            config=W2BLEConfig(),
            data_queue=data_queue,
            event_queue=event_queue,
            stop_event=threading.Event(),
        )
        block = worker.adapter.packet_to_block(W2RmsPacket(42))
        worker._times.extend(block.time_s)
        worker._rows.extend(block.rows)

        asyncio.run(worker._stop_client(FailingStopClient()))

        batch = data_queue.get_nowait()
        self.assertEqual(batch.rows[0][0], 42.0)
        messages = [event_queue.get_nowait().message for _ in range(event_queue.qsize())]
        self.assertTrue(any("Failed to send W2 stop command" in message for message in messages))
        self.assertTrue(any("Failed to stop W2 notifications" in message for message in messages))
        self.assertTrue(any("Stopped W2 BLE collection." in message for message in messages))

    def test_stop_client_can_skip_stop_command_before_collection_starts(self) -> None:
        client = TrackingStopClient()
        worker = BLEW2Worker(
            config=W2BLEConfig(),
            data_queue=queue.Queue(),
            event_queue=queue.Queue(),
            stop_event=threading.Event(),
        )

        asyncio.run(worker._stop_client(client, send_stop_command=False))

        self.assertEqual(client.write_count, 0)
        self.assertEqual(client.stop_notify_count, 1)


class SerialW2WorkerTests(unittest.TestCase):
    def test_serial_worker_uses_8n1_25600_and_the_existing_parser_and_commands(self) -> None:
        stop_event = threading.Event()
        frame = make_w2_raw_frame(W2CommandBuilder.MODE_EMG_RAW, 10.0, (3,))

        class FakeSerialException(Exception):
            pass

        class FakeSerialHandle:
            def __init__(self) -> None:
                self.reads = [frame]
                self.writes: list[bytes] = []
                self.input_reset = False
                self.closed = False

            def reset_input_buffer(self) -> None:
                self.input_reset = True

            def write(self, data: bytes) -> int:
                self.writes.append(bytes(data))
                return len(data)

            def flush(self) -> None:
                return None

            def read(self, _size: int) -> bytes:
                if self.reads:
                    return self.reads.pop(0)
                stop_event.set()
                return b""

            def close(self) -> None:
                self.closed = True

        handle = FakeSerialHandle()
        opened: dict[str, object] = {}

        def open_serial(port: str, baud_rate: int, **kwargs):
            opened.update(port=port, baud_rate=baud_rate, **kwargs)
            return handle

        fake_serial_module = SimpleNamespace(
            Serial=open_serial,
            SerialException=FakeSerialException,
            EIGHTBITS=8,
            PARITY_NONE="N",
            STOPBITS_ONE=1,
        )
        config = W2BLEConfig(
            transport="serial",
            serial_devices=(W2SerialDeviceConfig(channel_id="ch1", port="COM7"),),
        )
        spec = BLEW2Source(config).stream_specs()[0]
        data_queue: queue.Queue = queue.Queue()
        worker = SerialW2Worker(
            config=config,
            serial_config=config.serial_devices[0],
            spec=spec,
            data_queue=data_queue,
            event_queue=queue.Queue(),
            stop_event=stop_event,
        )

        old_serial = ble_w2_module.serial
        ble_w2_module.serial = fake_serial_module  # type: ignore[assignment]
        try:
            worker.run()
        finally:
            ble_w2_module.serial = old_serial

        self.assertEqual(opened["port"], "COM7")
        self.assertEqual(opened["baud_rate"], 25600)
        self.assertEqual(opened["bytesize"], 8)
        self.assertEqual(opened["parity"], "N")
        self.assertEqual(opened["stopbits"], 1)
        self.assertEqual(opened["timeout"], 0.05)
        self.assertTrue(handle.input_reset)
        self.assertEqual(
            handle.writes,
            [W2CommandBuilder.start_emg_raw(), W2CommandBuilder.stop_collect()],
        )
        self.assertTrue(handle.closed)
        block = data_queue.get_nowait()
        self.assertEqual(block.spec.stream_id, "ble_w2.serial.ch1.signal")
        self.assertEqual(len(block.rows), 2)
        self.assertAlmostEqual(block.rows[0][0], 10.0)
        self.assertAlmostEqual(block.rows[1][0], 10.0 + 3 / 3.1457)

    def test_worker_group_starts_and_stops_every_serial_port_as_one_source(self) -> None:
        stop_event = threading.Event()
        all_opened = threading.Event()
        opened_lock = threading.Lock()
        handles: dict[str, object] = {}

        class FakeSerialException(Exception):
            pass

        class WaitingSerialHandle:
            def __init__(self, port: str) -> None:
                self.port = port
                self.writes: list[bytes] = []
                self.closed = False

            def reset_input_buffer(self) -> None:
                return None

            def write(self, data: bytes) -> int:
                self.writes.append(bytes(data))
                return len(data)

            def flush(self) -> None:
                return None

            def read(self, _size: int) -> bytes:
                stop_event.wait(0.01)
                return b""

            def close(self) -> None:
                self.closed = True

        def open_serial(port: str, _baud_rate: int, **_kwargs):
            handle = WaitingSerialHandle(port)
            with opened_lock:
                handles[port] = handle
                if len(handles) == 2:
                    all_opened.set()
            return handle

        fake_serial_module = SimpleNamespace(
            Serial=open_serial,
            SerialException=FakeSerialException,
            EIGHTBITS=8,
            PARITY_NONE="N",
            STOPBITS_ONE=1,
        )
        source = BLEW2Source(
            W2BLEConfig(
                transport="serial",
                serial_devices=(
                    W2SerialDeviceConfig(channel_id="left", port="COM7"),
                    W2SerialDeviceConfig(channel_id="right", port="COM8"),
                ),
            )
        )
        group = source.create_worker(
            data_queue=queue.Queue(),
            event_queue=queue.Queue(),
            stop_event=stop_event,
        )
        self.assertIsInstance(group, W2WorkerGroup)
        assert isinstance(group, W2WorkerGroup)

        old_serial = ble_w2_module.serial
        ble_w2_module.serial = fake_serial_module  # type: ignore[assignment]
        try:
            group.start()
            self.assertTrue(all_opened.wait(1.0))
            self.assertTrue(group.is_alive())
        finally:
            stop_event.set()
            group.join(timeout=1.0)
            ble_w2_module.serial = old_serial

        self.assertFalse(group.is_alive())
        self.assertEqual(set(handles), {"COM7", "COM8"})
        for handle in handles.values():
            assert isinstance(handle, WaitingSerialHandle)
            self.assertEqual(
                handle.writes,
                [W2CommandBuilder.start_emg_raw(), W2CommandBuilder.stop_collect()],
            )
            self.assertTrue(handle.closed)


if __name__ == "__main__":
    unittest.main()
