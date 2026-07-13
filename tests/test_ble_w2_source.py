from __future__ import annotations

import asyncio
import queue
import struct
import threading
import unittest

from DeviceInterface.w2_protocol import W2CommandBuilder, W2RawPacket, W2RmsPacket, W2StreamParser
import fundamental.sources.ble_w2 as ble_w2_module
from fundamental.messages import CHANNEL_COUNT
from fundamental.sources.ble_w2 import BLEW2Source, BLEW2Worker, W2BLEConfig, W2SampleAdapter


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


class W2SampleAdapterTests(unittest.TestCase):
    def test_adapter_pads_w2_single_channel_samples_for_existing_buffer(self) -> None:
        adapter = W2SampleAdapter(sample_rate_hz=1000.0)
        frames = adapter.packet_to_frames(W2RawPacket(W2CommandBuilder.MODE_EMG_RAW, (1.2, 2.7)))

        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].counter, 0)
        self.assertEqual(frames[0].time_s, 0.0)
        self.assertEqual(frames[1].counter, 1)
        self.assertEqual(frames[1].time_s, 0.001)
        self.assertEqual(frames[0].emg_channel_count, 1)
        self.assertEqual(len(frames[0].values), CHANNEL_COUNT)
        self.assertEqual(frames[0].values, (1, 0, 0, 0, 0, 0, 0, 0))
        self.assertEqual(frames[1].values, (3, 0, 0, 0, 0, 0, 0, 0))


class BLEW2SourceTests(unittest.TestCase):
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


class BLEW2WorkerTests(unittest.TestCase):
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

    def test_stop_client_flushes_frames_even_when_cleanup_commands_fail(self) -> None:
        data_queue: queue.Queue = queue.Queue()
        event_queue: queue.Queue = queue.Queue()
        worker = BLEW2Worker(
            config=W2BLEConfig(),
            data_queue=data_queue,
            event_queue=event_queue,
            stop_event=threading.Event(),
        )
        worker._frames.extend(W2SampleAdapter().packet_to_frames(W2RmsPacket(42)))

        asyncio.run(worker._stop_client(FailingStopClient()))

        batch = data_queue.get_nowait()
        self.assertEqual(batch.frames[0].values[0], 42)
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


if __name__ == "__main__":
    unittest.main()
