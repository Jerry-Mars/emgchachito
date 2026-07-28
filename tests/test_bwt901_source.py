from __future__ import annotations

import queue
import struct
import threading
import time
import unittest
from types import SimpleNamespace

from DeviceInterface.bwt901_protocol import BWT901StreamDecoder
from fundamental.messages import WorkerEvent
from fundamental.sources.base import CaptureClock, WorkerControl
from fundamental.sources.bwt901 import (
    BWT901BLEConfig,
    BWT901BLEWorker,
    BWT901DeviceConfig,
    BWT901Source,
    DEFAULT_BWT901_ADDRESS,
    bwt901_stream_spec,
)
from fundamental.streams import StreamBlock


def make_bwt901_frame(values: tuple[int, ...]) -> bytes:
    return b"\x55\x61" + struct.pack("<9h", *values)


class BWT901ProtocolTests(unittest.TestCase):
    def test_decoder_handles_split_merged_and_misaligned_frames(self) -> None:
        decoder = BWT901StreamDecoder()
        first = make_bwt901_frame((2048, -2048, 4096, 100, -100, 200, 1000, -1000, 0))
        second = make_bwt901_frame((0, 0, 0, 0, 0, 0, 0, 0, 0))

        self.assertEqual(decoder.feed(b"noise" + first[:7]), [])
        packets = decoder.feed(first[7:] + second)

        self.assertEqual([packet.sequence for packet in packets], [1, 2])
        self.assertEqual(packets[0].raw[0], 2048)
        self.assertEqual(packets[0].acc_x_g, 1.0)
        self.assertEqual(packets[0].acc_y_g, -1.0)
        self.assertGreaterEqual(decoder.skipped_bytes, 5)

    def test_decoder_keeps_split_header_byte(self) -> None:
        decoder = BWT901StreamDecoder()
        frame = make_bwt901_frame((1, 2, 3, 4, 5, 6, 7, 8, 9))
        self.assertEqual(decoder.feed(b"junk\x55"), [])
        packets = decoder.feed(frame[1:])
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0].raw, (1, 2, 3, 4, 5, 6, 7, 8, 9))


class BWT901SourceTests(unittest.TestCase):
    def test_default_config_uses_verified_demo_address_and_unknown_rate(self) -> None:
        source = BWT901Source()
        self.assertEqual(source.config.devices[0].address, DEFAULT_BWT901_ADDRESS)
        spec = source.stream_specs()[0]
        self.assertIsNone(spec.nominal_rate_hz)
        self.assertEqual(spec.stream_id, "bwt901.imu_1.imu")
        self.assertEqual(len(spec.fields), 10)

    def test_two_devices_expose_unique_streams(self) -> None:
        source = BWT901Source(
            BWT901BLEConfig(
                devices=(
                    BWT901DeviceConfig("imu_left", "AA:BB"),
                    BWT901DeviceConfig("imu_right", "CC:DD"),
                )
            )
        )
        self.assertEqual(
            tuple(spec.stream_id for spec in source.stream_specs()),
            ("bwt901.imu_left.imu", "bwt901.imu_right.imu"),
        )

    def test_worker_uses_shared_clock_and_drops_paused_rows(self) -> None:
        data: queue.Queue[StreamBlock] = queue.Queue()
        events: queue.Queue[WorkerEvent] = queue.Queue()
        stop_event = threading.Event()
        capture_event = threading.Event()
        clock = CaptureClock()
        clock.resume()
        control = WorkerControl(stop_event, capture_event, clock)
        device = BWT901DeviceConfig()
        worker = BWT901BLEWorker(
            BWT901BLEConfig(),
            device,
            bwt901_stream_spec(device),
            data,
            events,
            stop_event,
            control=control,
        )
        frame = make_bwt901_frame((1, 2, 3, 4, 5, 6, 7, 8, 9))

        worker._on_notification(None, bytearray(frame))
        worker._flush()
        self.assertTrue(data.empty())

        capture_event.set()
        worker._on_notification(None, bytearray(frame))
        worker._flush()
        block = data.get_nowait()
        self.assertEqual(block.spec.stream_id, "bwt901.imu_1.imu")
        self.assertEqual(block.rows[0][0], 2)
        self.assertGreaterEqual(block.time_s[0], 0.0)

    def test_pause_keeps_bwt_ble_connection_and_only_gates_published_rows(self) -> None:
        data: queue.Queue[StreamBlock] = queue.Queue()
        events: queue.Queue[WorkerEvent] = queue.Queue()
        stop_event = threading.Event()
        capture_event = threading.Event()
        clock = CaptureClock()
        control = WorkerControl(stop_event, capture_event, clock)

        class FakeTransport:
            def __init__(self, _config, **_kwargs) -> None:
                self.device = SimpleNamespace(address=DEFAULT_BWT901_ADDRESS)
                self.resolved_address = DEFAULT_BWT901_ADDRESS
                self.disconnected_event = threading.Event()
                self.is_connected = False
                self.disconnected = False
                self.callback = None

            async def connect(self) -> None:
                self.is_connected = True

            async def start_notify(self, _uuid, callback) -> None:
                self.callback = callback

            async def disconnect(self) -> None:
                self.is_connected = False
                self.disconnected = True

        transports: list[FakeTransport] = []

        def factory(config, **kwargs):
            transport = FakeTransport(config, **kwargs)
            transports.append(transport)
            return transport

        device = BWT901DeviceConfig()
        worker = BWT901BLEWorker(
            BWT901BLEConfig(),
            device,
            bwt901_stream_spec(device),
            data,
            events,
            stop_event,
            control=control,
            transport_factory=factory,
        )
        worker.start()
        self.assertTrue(worker.ready_event.wait(1.0))
        transport = transports[0]
        self.assertFalse(transport.disconnected)

        frame = bytearray(make_bwt901_frame((1, 2, 3, 4, 5, 6, 7, 8, 9)))
        assert transport.callback is not None
        transport.callback(None, frame)
        worker._flush()
        self.assertTrue(data.empty())

        clock.resume()
        capture_event.set()
        transport.callback(None, frame)
        worker._flush()
        self.assertFalse(data.empty())

        capture_event.clear()
        clock.pause()
        time.sleep(0.02)
        self.assertFalse(transport.disconnected)

        stop_event.set()
        capture_event.set()
        worker.join(timeout=1.0)
        self.assertTrue(transport.disconnected)


if __name__ == "__main__":
    unittest.main()
