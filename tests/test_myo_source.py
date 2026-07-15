from __future__ import annotations

import asyncio
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fundamental.messages import WorkerEvent
from fundamental.sources.myo import (
    MYO_EMG_STREAM_ID,
    MYO_EMG_STREAM_SPEC,
    MYO_IMU_STREAM_ID,
    MYO_IMU_STREAM_SPEC,
    MyoBLEConfig,
    MyoSource,
    MyoWorker,
)
from fundamental.streams import CaptureResumeState, StreamCursor


class MyoSourceTests(unittest.TestCase):
    def test_source_declares_two_independent_streams(self) -> None:
        source = MyoSource()

        self.assertEqual(
            [spec.stream_id for spec in source.stream_specs()],
            [MYO_EMG_STREAM_ID, MYO_IMU_STREAM_ID],
        )
        self.assertEqual(len(MYO_EMG_STREAM_SPEC.signal_fields), 8)
        self.assertEqual(len(MYO_IMU_STREAM_SPEC.signal_fields), 10)

    def test_callbacks_reconstruct_native_rates_and_keep_host_receive_time(self) -> None:
        data_queue: queue.Queue = queue.Queue()
        worker = MyoWorker(
            MyoBLEConfig(),
            data_queue,
            queue.Queue(),
            threading.Event(),
        )
        orientation = SimpleNamespace(w=1.0, x=0.1, y=0.2, z=0.3)

        with patch(
            "fundamental.sources.myo.time.perf_counter_ns",
            side_effect=(1_000_000_000, 1_002_000_000),
        ):
            worker._on_emg(((1, 2, 3, 4, 5, 6, 7, 8), (8, 7, 6, 5, 4, 3, 2, 1)))
            worker._on_imu(orientation, (0.1, 0.2, 0.3), (1.0, 2.0, 3.0))
        worker._flush_all()

        blocks = [data_queue.get_nowait(), data_queue.get_nowait()]
        emg = next(block for block in blocks if block.spec.stream_id == MYO_EMG_STREAM_ID)
        imu = next(block for block in blocks if block.spec.stream_id == MYO_IMU_STREAM_ID)
        self.assertEqual(emg.time_s, (0.0, 0.005))
        self.assertEqual(emg.rows[0][0], 0.0)
        self.assertEqual(imu.time_s, (0.002,))
        self.assertEqual(imu.rows[0][0], 0.002)
        self.assertEqual(imu.rows[0][-3:], (1.0, 2.0, 3.0))

    def test_resumed_streams_continue_from_their_own_last_sample(self) -> None:
        emg_cursor = StreamCursor(
            MYO_EMG_STREAM_SPEC,
            2,
            1.005,
            (1.0, 1, 2, 3, 4, 5, 6, 7, 8),
        )
        imu_cursor = StreamCursor(
            MYO_IMU_STREAM_SPEC,
            1,
            1.0,
            (1.0, 1.0, 0.0, 0.0, 0.0, 0.1, 0.2, 0.3, 1.0, 2.0, 3.0),
        )
        worker = MyoWorker(
            MyoBLEConfig(),
            queue.Queue(),
            queue.Queue(),
            threading.Event(),
            resume_state=CaptureResumeState(
                1.005,
                {MYO_EMG_STREAM_ID: emg_cursor, MYO_IMU_STREAM_ID: imu_cursor},
            ),
        )

        with patch("fundamental.sources.myo.time.perf_counter_ns", return_value=2_000_000_000):
            worker._on_emg(((1, 2, 3, 4, 5, 6, 7, 8), (1, 2, 3, 4, 5, 6, 7, 8)))

        self.assertAlmostEqual(worker._emg_time_s[0], 1.010)
        self.assertAlmostEqual(worker._emg_time_s[1], 1.015)


class TrackingMyo:
    def __init__(self) -> None:
        self.mode_calls: list[tuple[object, object, object]] = []
        self.sleep_calls: list[object] = []
        self.disconnect_count = 0

    async def set_mode(self, emg_mode, imu_mode, classifier_mode) -> None:
        self.mode_calls.append((emg_mode, imu_mode, classifier_mode))

    async def set_sleep_mode(self, mode) -> None:
        self.sleep_calls.append(mode)

    async def disconnect(self) -> None:
        self.disconnect_count += 1


class MyoCleanupTests(unittest.TestCase):
    def test_cleanup_stops_streams_restores_sleep_and_disconnects(self) -> None:
        events: queue.Queue[WorkerEvent] = queue.Queue()
        worker = MyoWorker(MyoBLEConfig(), queue.Queue(), events, threading.Event())
        client = TrackingMyo()
        worker.myo = client

        asyncio.run(worker._cleanup_connection())

        self.assertEqual(len(client.mode_calls), 1)
        self.assertEqual(len(client.sleep_calls), 1)
        self.assertEqual(client.disconnect_count, 1)
        self.assertIn("Disconnected Myo", events.get_nowait().message)


if __name__ == "__main__":
    unittest.main()
