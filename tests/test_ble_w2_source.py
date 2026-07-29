from __future__ import annotations

import asyncio
import queue
import struct
import threading
import time
import unittest
from types import SimpleNamespace

from DeviceInterface.w2_protocol import W2CommandBuilder, W2RawPacket, W2RmsPacket, W2StreamParser
import fundamental.sources.ble_w2 as ble_w2_module
from fundamental.sources.base import CaptureClock, WorkerControl
from fundamental.sources.ble_w2 import (
    BLEW2Source,
    BLEW2Worker,
    DEFAULT_W2_DEVICES,
    DEFAULT_W2_SERIAL_BAUD_RATE,
    SerialW2Worker,
    W2BLEConfig,
    W2DeviceConfig,
    W2SerialDeviceConfig,
    W2StreamAdapter,
    W2WorkerGroup,
    default_w2_config,
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


def _wait_until(predicate, timeout_s: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


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
        self.assertEqual(block.spec.stream_id, "ble_w2.w2_1.signal")


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
            ("ble_w2.left.signal", "ble_w2.right.signal"),
        )
        self.assertEqual(tuple(spec.fields[0].label for spec in specs), ("left EMG Raw", "right EMG Raw"))
        self.assertIsInstance(worker, W2WorkerGroup)
        assert isinstance(worker, W2WorkerGroup)
        self.assertEqual(len(worker.workers), 2)
        self.assertTrue(all(isinstance(child, SerialW2Worker) for child in worker.workers))
        self.assertEqual(
            [device["transport"] for device in source.capture_metadata()["devices"]],
            ["serial", "serial"],
        )

    def test_explicit_devices_can_mix_serial_and_ble(self) -> None:
        source = BLEW2Source(
            W2BLEConfig(
                devices=(
                    W2DeviceConfig("left", "serial", port="COM7"),
                    W2DeviceConfig("right", "ble", address="AA:BB"),
                )
            )
        )
        group = source.create_worker(queue.Queue(), queue.Queue(), threading.Event())
        self.assertIsInstance(group, W2WorkerGroup)
        assert isinstance(group, W2WorkerGroup)
        self.assertIsInstance(group.workers[0], SerialW2Worker)
        self.assertIsInstance(group.workers[1], BLEW2Worker)


class W2SerialConfigTests(unittest.TestCase):
    def test_w2_defaults_match_the_current_five_serial_device_setup(self) -> None:
        device = W2SerialDeviceConfig()
        config = W2BLEConfig()
        experiment_config = default_w2_config()

        self.assertEqual(DEFAULT_W2_SERIAL_BAUD_RATE, 256000)
        self.assertEqual(config.serial_baud_rate, 256000)
        self.assertEqual(device.port, "COM5")
        self.assertEqual(config.serial_timeout_s, 0.05)
        self.assertEqual(experiment_config.devices, DEFAULT_W2_DEVICES)
        self.assertEqual(
            [
                (item.device_id, item.transport, item.port, item.device_name_filter)
                for item in experiment_config.devices
            ],
            [
                ("w2_1", "serial", "COM9", "RunE W21"),
                ("w2_2", "serial", "COM11", "RunE W22"),
                ("w2_3", "serial", "COM12", "RunE W23"),
                ("w2_4", "serial", "COM13", "RunE W24"),
                ("w2_5", "serial", "COM10", "RunE W25"),
            ],
        )


class BLEW2WorkerTests(unittest.TestCase):
    def test_start_cancelled_after_address_resolution_does_not_connect(self) -> None:
        event_queue: queue.Queue = queue.Queue()
        stop_event = threading.Event()
        class StopAfterConnectTransport:
            def __init__(self, _config, **_kwargs) -> None:
                self.device = SimpleNamespace(address="AA:BB")
                self.resolved_address = "AA:BB"
                self.disconnected_event = threading.Event()
                self.is_connected = True
                self.notify_started = False

            async def connect(self) -> None:
                stop_event.set()

            async def start_notify(self, _uuid, _callback) -> None:
                self.notify_started = True

            async def disconnect(self) -> None:
                self.is_connected = False

        transports: list[StopAfterConnectTransport] = []

        def transport_factory(config, **kwargs):
            transport = StopAfterConnectTransport(config, **kwargs)
            transports.append(transport)
            return transport

        worker = BLEW2Worker(
            config=W2BLEConfig(address="AA:BB"),
            data_queue=queue.Queue(),
            event_queue=event_queue,
            stop_event=stop_event,
            transport_factory=transport_factory,
        )
        asyncio.run(worker._run_async())

        self.assertFalse(transports[0].notify_started)

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

    def test_pause_keeps_ble_connected_and_resume_reuses_connection(self) -> None:
        stop_event = threading.Event()
        capture_event = threading.Event()
        capture_event.set()
        clock = CaptureClock()
        clock.resume()
        control = WorkerControl(stop_event, capture_event, clock)

        class FakeTransport:
            def __init__(self, _config, **_kwargs) -> None:
                self.device = SimpleNamespace(address="AA:BB")
                self.resolved_address = "AA:BB"
                self.disconnected_event = threading.Event()
                self.is_connected = False
                self.disconnected = False
                self.writes: list[bytes] = []

            async def connect(self) -> None:
                self.is_connected = True

            async def start_notify(self, _uuid, _callback) -> None:
                return None

            async def write(self, _uuid, data) -> None:
                self.writes.append(bytes(data))

            async def disconnect(self) -> None:
                self.is_connected = False
                self.disconnected = True

        transports: list[FakeTransport] = []

        def factory(config, **kwargs):
            transport = FakeTransport(config, **kwargs)
            transports.append(transport)
            return transport

        device = W2DeviceConfig("w2_1", "ble", address="AA:BB")
        config = W2BLEConfig(devices=(device,))
        worker = BLEW2Worker(
            config,
            queue.Queue(),
            queue.Queue(),
            stop_event,
            device=device,
            control=control,
            transport_factory=factory,
        )
        worker.start()
        self.assertTrue(worker.ready_event.wait(1.0))
        transport = transports[0]
        self.assertTrue(_wait_until(lambda: len(transport.writes) >= 1))

        capture_event.clear()
        clock.pause()
        self.assertTrue(_wait_until(lambda: len(transport.writes) >= 2))
        self.assertFalse(transport.disconnected)

        clock.resume()
        capture_event.set()
        self.assertTrue(_wait_until(lambda: len(transport.writes) >= 3))
        self.assertFalse(transport.disconnected)

        stop_event.set()
        capture_event.set()
        worker.join(timeout=1.0)
        self.assertEqual(
            transport.writes,
            [
                W2CommandBuilder.start_emg_raw(),
                W2CommandBuilder.stop_collect(),
                W2CommandBuilder.start_emg_raw(),
                W2CommandBuilder.stop_collect(),
            ],
        )
        self.assertTrue(transport.disconnected)


class SerialW2WorkerTests(unittest.TestCase):
    def test_health_rate_accumulates_active_time_across_pause_and_resume(self) -> None:
        worker = SerialW2Worker(
            config=W2BLEConfig(
                devices=(W2DeviceConfig("w2_1", "serial", port="COM7"),)
            ),
            serial_config=W2SerialDeviceConfig("w2_1", "COM7"),
            spec=w2_stream_spec(W2BLEConfig()),
            data_queue=queue.Queue(),
            event_queue=queue.Queue(),
            stop_event=threading.Event(),
        )
        worker._begin_active_interval(10.0)
        worker._end_active_interval(20.0)
        worker._begin_active_interval(30.0)
        worker.decoded_packet_count = 150

        worker._emit_health(35.0)

        event = worker.event_queue.get_nowait()
        assert event.data is not None
        self.assertAlmostEqual(event.data["observed_rate_hz"], 10.0)

    def test_serial_worker_uses_8n1_256000_and_the_existing_parser_and_commands(self) -> None:
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
        self.assertEqual(opened["baud_rate"], 256000)
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
        self.assertEqual(block.spec.stream_id, "ble_w2.ch1.signal")
        self.assertEqual(len(block.rows), 2)
        self.assertAlmostEqual(block.rows[0][0], 10.0)
        self.assertAlmostEqual(block.rows[1][0], 10.0 + 3 / 3.1457)

    def test_pause_keeps_serial_open_and_resume_reuses_same_connection(self) -> None:
        stop_event = threading.Event()
        capture_event = threading.Event()
        capture_event.set()
        clock = CaptureClock()
        clock.resume()
        control = WorkerControl(stop_event, capture_event, clock)

        class Handle:
            def __init__(self) -> None:
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
                stop_event.wait(0.005)
                return b""

            def close(self) -> None:
                self.closed = True

        handle = Handle()
        fake_serial = SimpleNamespace(
            Serial=lambda *_args, **_kwargs: handle,
            EIGHTBITS=8,
            PARITY_NONE="N",
            STOPBITS_ONE=1,
        )
        config = W2BLEConfig(
            devices=(W2DeviceConfig("w2_1", "serial", port="COM7"),)
        )
        worker = SerialW2Worker(
            config,
            W2SerialDeviceConfig("w2_1", "COM7"),
            BLEW2Source(config).stream_specs()[0],
            queue.Queue(),
            queue.Queue(),
            stop_event,
            control=control,
        )

        old_serial = ble_w2_module.serial
        ble_w2_module.serial = fake_serial  # type: ignore[assignment]
        try:
            worker.start()
            self.assertTrue(worker.ready_event.wait(1.0))
            self.assertTrue(_wait_until(lambda: len(handle.writes) >= 1))

            capture_event.clear()
            clock.pause()
            self.assertTrue(_wait_until(lambda: len(handle.writes) >= 2))
            self.assertFalse(handle.closed)

            clock.resume()
            capture_event.set()
            self.assertTrue(_wait_until(lambda: len(handle.writes) >= 3))
            self.assertFalse(handle.closed)
        finally:
            stop_event.set()
            capture_event.set()
            worker.join(timeout=1.0)
            ble_w2_module.serial = old_serial

        self.assertEqual(
            handle.writes,
            [
                W2CommandBuilder.start_emg_raw(),
                W2CommandBuilder.stop_collect(),
                W2CommandBuilder.start_emg_raw(),
                W2CommandBuilder.stop_collect(),
            ],
        )
        self.assertTrue(handle.closed)

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
