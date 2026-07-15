from __future__ import annotations

import tempfile
import queue
import threading
import unittest
from pathlib import Path

from DeviceInterface.ads1299_protocol import (
    ADS1299StreamParser,
    CHANNEL_COUNT,
    FRAME_LEN,
    int24_be_to_signed,
    parse_frame,
)
from fundamental.capture_store import CaptureStore
from fundamental.csv_writer import save_capture, save_stimulus_log, stimulus_log_path
from fundamental.messages import (
    DEFAULT_BAUD_RATE,
    SerialConfig,
)
from fundamental.sources.serial_ads1299 import ADS1299_STREAM_SPEC, SerialWorker
from fundamental.streams import CaptureResumeState, StreamBlock, StreamCursor
from fundamental.stimulus_model import INVALID_STIMULUS_CODE, StimulusController, StimulusEvent, StimulusState


VALUES = (1, -1, 8388607, -8388608, 123456, -123456, 0, 42)


def int24_bytes(value: int) -> bytes:
    if value < 0:
        value = (1 << 24) + value
    return value.to_bytes(3, "big")


def make_frame(counter: int, values: tuple[int, ...] = VALUES, emg_channel_count: int = 4) -> bytes:
    payload = bytearray([0xAA, emg_channel_count])
    for value in values:
        payload.extend(int24_bytes(value))
    payload.extend(counter.to_bytes(8, "big"))
    payload.append(0xBB)
    return bytes(payload)


def make_legacy_frame(counter: int, values: tuple[int, ...] = VALUES) -> bytes:
    payload = bytearray([0xAA])
    for value in values:
        payload.extend(int24_bytes(value))
    payload.extend(counter.to_bytes(8, "big"))
    payload.append(0xBB)
    return bytes(payload)


class ADS1299ProtocolTests(unittest.TestCase):
    def test_int24_conversion(self) -> None:
        self.assertEqual(int24_be_to_signed(0x00, 0x00, 0x01), 1)
        self.assertEqual(int24_be_to_signed(0x7F, 0xFF, 0xFF), 8388607)
        self.assertEqual(int24_be_to_signed(0x80, 0x00, 0x00), -8388608)
        self.assertEqual(int24_be_to_signed(0xFF, 0xFF, 0xFF), -1)

    def test_parse_fixed_frame(self) -> None:
        parsed = parse_frame(make_frame(10))
        self.assertEqual(parsed.counter, 10)
        self.assertEqual(parsed.emg_channel_count, 4)
        self.assertEqual(parsed.channels_code, VALUES)
        self.assertEqual(parsed.emg_channels_code, VALUES[:4])

    def test_parse_legacy_frame_without_channel_count(self) -> None:
        parsed = parse_frame(make_legacy_frame(9))

        self.assertEqual(parsed.counter, 9)
        self.assertEqual(parsed.emg_channel_count, 8)
        self.assertEqual(parsed.channels_code, VALUES)

    def test_stream_auto_detects_legacy_and_current_frames(self) -> None:
        parser = ADS1299StreamParser()

        frames = parser.feed(make_legacy_frame(10) + make_legacy_frame(11) + make_frame(12))

        self.assertEqual([frame.counter for frame in frames], [10, 11, 12])
        self.assertEqual([frame.emg_channel_count for frame in frames], [8, 8, 4])
        self.assertEqual(parser.legacy_frame_count, 2)
        self.assertEqual(parser.current_frame_count, 1)

    def test_rejects_invalid_emg_channel_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid emg channel count"):
            parse_frame(make_frame(10, emg_channel_count=0))

    def test_stream_resync_and_counter_gap(self) -> None:
        parser = ADS1299StreamParser()
        self.assertEqual(parser.feed(b"noise" + make_frame(10)[:12]), [])

        frames = parser.feed(make_frame(10)[12:] + make_frame(11) + make_frame(14))

        self.assertEqual([frame.counter for frame in frames], [10, 11, 14])
        self.assertEqual([frame.dropped_frames_before for frame in frames], [0, 0, 2])
        self.assertEqual(parser.skipped_bytes, 5)

    def test_counter_regression_marks_unknown_discontinuity(self) -> None:
        parser = ADS1299StreamParser()
        frames = parser.feed(make_frame(20) + make_frame(18))
        self.assertEqual([frame.dropped_frames_before for frame in frames], [0, -1])

    def test_bad_tail_resyncs_to_next_valid_frame(self) -> None:
        bad_tail = bytearray(make_frame(30))
        bad_tail[-1] = 0x00
        parser = ADS1299StreamParser()

        frames = parser.feed(bytes(bad_tail) + make_frame(31))

        self.assertEqual([frame.counter for frame in frames], [31])
        self.assertEqual(parser.bad_tail_count, 1)

    def test_bad_channel_count_resyncs_to_next_valid_frame(self) -> None:
        parser = ADS1299StreamParser()

        frames = parser.feed(make_frame(30, emg_channel_count=0) + make_frame(31))

        self.assertEqual([frame.counter for frame in frames], [31])
        self.assertEqual(parser.bad_channel_count, 1)


class AcquisitionDataContractTests(unittest.TestCase):
    def test_defaults_match_hardware_protocol(self) -> None:
        self.assertEqual(FRAME_LEN, 35)
        self.assertEqual(CHANNEL_COUNT, 8)
        self.assertEqual(DEFAULT_BAUD_RATE, 921600)

    def test_buffer_and_csv_keep_counter_and_raw_codes(self) -> None:
        buffer = CaptureStore(plot_buffer_size=4, stream_specs=(ADS1299_STREAM_SPEC,))
        count = buffer.append_block(
            StreamBlock(
                ADS1299_STREAM_SPEC,
                (0.0, 0.1),
                (
                    (10, 0, 4, *VALUES),
                    (11, 0, 4, *VALUES),
                ),
            )
        )

        self.assertEqual(count, 2)
        self.assertEqual(buffer.row_count, 2)
        channel_window = buffer.get_series_window("serial_ads1299.emg/ch2_code", 1.0)
        self.assertIsNotNone(channel_window)
        assert channel_window is not None
        self.assertEqual(channel_window.time_s, [0.0, 0.1])
        self.assertEqual(channel_window.values, [-1.0, -1.0])
        self.assertIsNone(buffer.get_series_window("serial_ads1299.emg/ch5_code", 1.0))

        with tempfile.TemporaryDirectory() as tmp:
            result = save_capture(Path(tmp) / "capture.csv", buffer.snapshots())
            lines = result.streams[0].path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.total_rows, 2)
        self.assertEqual(
            lines[0],
            "time_s,frame_counter,dropped_frames_before,"
            "emg_channel_count,ch1_code,ch2_code,ch3_code,ch4_code,"
            "ch5_code,ch6_code,ch7_code,ch8_code",
        )
        self.assertEqual(
            lines[1],
            "0.000000,10,0,4,1,-1,8388607,-8388608,123456,-123456,0,42",
        )

    def test_labeled_csv_adds_stimulus_code_when_requested(self) -> None:
        buffer = CaptureStore(stream_specs=(ADS1299_STREAM_SPEC,))
        buffer.append_block(
            StreamBlock(
                ADS1299_STREAM_SPEC,
                (0.0, 1.0),
                ((10, 0, 4, *VALUES), (11, 0, 4, *VALUES)),
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            result = save_capture(
                Path(tmp) / "capture.csv",
                buffer.snapshots(),
                stimulus_code_for_time=lambda time_s: 1 if time_s < 0.5 else 2,
            )
            lines = result.streams[0].path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(result.total_rows, 2)
        self.assertEqual(
            lines[0],
            "time_s,frame_counter,dropped_frames_before,"
            "emg_channel_count,stimulus_code,ch1_code,ch2_code,ch3_code,ch4_code,"
            "ch5_code,ch6_code,ch7_code,ch8_code",
        )
        self.assertEqual(
            lines[1],
            "0.000000,10,0,4,1,1,-1,8388607,-8388608,123456,-123456,0,42",
        )
        self.assertEqual(
            lines[2],
            "1.000000,11,0,4,2,1,-1,8388607,-8388608,123456,-123456,0,42",
        )

    def test_worker_timestamps_follow_device_counter(self) -> None:
        worker = SerialWorker(
            config=SerialConfig(),
            data_queue=queue.Queue(),
            event_queue=queue.Queue(),
            stop_event=threading.Event(),
        )

        self.assertEqual(worker._timestamp_for_counter(100), 0.0)
        self.assertEqual(worker._timestamp_for_counter(101), 0.001)
        self.assertEqual(worker._timestamp_for_counter(104), 0.004)

    def test_worker_reports_open_port_with_no_received_bytes(self) -> None:
        event_queue: queue.Queue = queue.Queue()
        worker = SerialWorker(
            config=SerialConfig(),
            data_queue=queue.Queue(),
            event_queue=event_queue,
            stop_event=threading.Event(),
        )

        worker._report_no_frames_if_due(0.0)

        self.assertIn("no bytes have arrived", event_queue.get_nowait().message)

    def test_resumed_worker_timestamps_continue_after_previous_counter(self) -> None:
        cursor = StreamCursor(
            spec=ADS1299_STREAM_SPEC,
            row_count=1,
            last_time_s=2.0,
            last_row=(50, 0, 8, *VALUES),
        )
        worker = SerialWorker(
            config=SerialConfig(),
            data_queue=queue.Queue(),
            event_queue=queue.Queue(),
            stop_event=threading.Event(),
            resume_state=CaptureResumeState(2.0, {ADS1299_STREAM_SPEC.stream_id: cursor}),
        )

        self.assertEqual(worker._timestamp_for_counter(51), 2.001)
        self.assertEqual(worker._timestamp_for_counter(54), 2.004)

    def test_resume_counter_gap_does_not_insert_paused_wall_time(self) -> None:
        cursor = StreamCursor(
            spec=ADS1299_STREAM_SPEC,
            row_count=1,
            last_time_s=2.0,
            last_row=(50, 0, 8, *VALUES),
        )
        worker = SerialWorker(
            config=SerialConfig(),
            data_queue=queue.Queue(),
            event_queue=queue.Queue(),
            stop_event=threading.Event(),
            resume_state=CaptureResumeState(2.0, {ADS1299_STREAM_SPEC.stream_id: cursor}),
        )

        self.assertEqual(worker._timestamp_for_counter(80), 2.001)
        self.assertEqual(worker._timestamp_for_counter(81), 2.002)


class StimulusContractTests(unittest.TestCase):
    def test_stimulus_timeline_labels_by_sample_time(self) -> None:
        stimulus = StimulusController()
        error = stimulus.set_schedule(
            [
                StimulusEvent(1, "rest", 1.0),
                StimulusEvent(2, "grip", 1.0),
            ]
        )
        self.assertIsNone(error)

        self.assertEqual(stimulus.start(0.0), "Stimulus timeline started.")
        stimulus.update(1.25)

        self.assertEqual(stimulus.stimulus_code_at(0.5), 1)
        self.assertEqual(stimulus.stimulus_code_at(1.1), 2)
        stimulus.update(2.0)
        self.assertEqual(stimulus.state, StimulusState.STOPPED)

    def test_restart_event_marks_previous_attempt_invalid(self) -> None:
        stimulus = StimulusController()
        stimulus.set_schedule([StimulusEvent(2, "grip", 2.0)])

        stimulus.start(0.0)
        self.assertEqual(stimulus.restart_event(0.4), "Restarted event 1.")

        self.assertEqual(stimulus.stimulus_code_at(0.2), INVALID_STIMULUS_CODE)
        self.assertEqual(stimulus.stimulus_code_at(0.5), 2)
        rows = stimulus.event_log_rows()
        self.assertEqual(rows[0]["status"], "restarted_invalid")
        self.assertEqual(rows[0]["stimulus_code"], INVALID_STIMULUS_CODE)
        self.assertEqual(rows[1]["stimulus_code"], 2)

    def test_stimulus_log_sidecar_path_and_rows(self) -> None:
        rows = [
            {
                "event_index": 1,
                "stimulus_code": 1,
                "planned_code": 1,
                "label": "rest",
                "start_time_s": 0.0,
                "end_time_s": 1.0,
                "status": "completed",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            capture_path = Path(tmp) / "capture.csv"
            log_path = stimulus_log_path(capture_path)
            path, row_count = save_stimulus_log(log_path, rows)
            lines = path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(row_count, 1)
        self.assertEqual(path.name, "capture.stimulus.csv")
        self.assertEqual(
            lines[0],
            "event_index,stimulus_code,planned_code,label,start_time_s,end_time_s,status",
        )
        self.assertEqual(lines[1], "1,1,1,rest,0.000000,1.000000,completed")


if __name__ == "__main__":
    unittest.main()
