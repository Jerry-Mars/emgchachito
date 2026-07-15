from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from fundamental.capture_store import CaptureStore
from fundamental.messages import AcquisitionState
from fundamental.recording_session import RecordingSession
from fundamental.sources.serial_ads1299 import ADS1299_STREAM_SPEC
from fundamental.streams import StreamBlock
from fundamental.stimulus_model import StimulusController, StimulusEvent, StimulusState


VALUES = (1, 2, 3, 4, 5, 6, 7, 8)


class FakeAcquisition:
    def __init__(self) -> None:
        self.state = AcquisitionState.STOPPED
        self.buffer = CaptureStore(plot_buffer_size=16, stream_specs=(ADS1299_STREAM_SPEC,))
        self.last_save_path = "captures/fake.csv"
        self.fail_on_drain = False
        self.save_calls: list[dict[str, Any]] = []

    def start(self) -> str:
        if self.state == AcquisitionState.RUNNING:
            return "Acquisition is already running."
        if self.state == AcquisitionState.STOPPED:
            self.buffer.reset()
        self.state = AcquisitionState.RUNNING
        return "Acquisition started."

    def pause(self) -> str:
        if self.state != AcquisitionState.RUNNING:
            return "Acquisition is not running."
        self.state = AcquisitionState.PAUSED
        return "Acquisition paused."

    def stop(self) -> str:
        self.state = AcquisitionState.STOPPED
        return "Acquisition stopped."

    def save(
        self,
        path: str | Path | None = None,
        stimulus_code_for_time=None,
        stimulus_log_rows=None,
    ) -> str:
        self.save_calls.append(
            {
                "path": path,
                "stimulus_code_for_time": stimulus_code_for_time,
                "stimulus_log_rows": stimulus_log_rows,
            }
        )
        return "Saved."

    def drain_queues(self, log_sink=None) -> int:
        if self.fail_on_drain:
            self.state = AcquisitionState.STOPPED
            if log_sink is not None:
                log_sink("Serial failed.")
        return 0


def append_frame(acquisition: FakeAcquisition, time_s: float, counter: int = 1) -> None:
    acquisition.buffer.append_block(
        StreamBlock(
            ADS1299_STREAM_SPEC,
            (time_s,),
            ((counter, 0, 4, *VALUES),),
        )
    )


class RecordingSessionTests(unittest.TestCase):
    def test_save_includes_stimulus_labels_after_stimulus_session(self) -> None:
        acquisition = FakeAcquisition()
        acquisition.state = AcquisitionState.RUNNING
        stimulus = StimulusController()
        stimulus.set_schedule([StimulusEvent(7, "grip", 2.0)])
        append_frame(acquisition, 0.0)
        session = RecordingSession(acquisition, stimulus)  # type: ignore[arg-type]

        session.start_stimulus()
        append_frame(acquisition, 0.5, counter=2)
        session.stop()
        session.save("out.csv")

        save_call = acquisition.save_calls[-1]
        resolver = save_call["stimulus_code_for_time"]
        self.assertIsNotNone(resolver)
        self.assertEqual(resolver(0.25), 7)
        self.assertEqual(save_call["stimulus_log_rows"][0]["label"], "grip")

    def test_plain_acquisition_start_clears_stale_stimulus_labels(self) -> None:
        acquisition = FakeAcquisition()
        acquisition.state = AcquisitionState.RUNNING
        stimulus = StimulusController()
        stimulus.set_schedule([StimulusEvent(3, "pinch", 1.0)])
        append_frame(acquisition, 0.0)
        session = RecordingSession(acquisition, stimulus)  # type: ignore[arg-type]

        session.start_stimulus()
        session.stop()
        self.assertTrue(session.has_stimulus_labels)

        session.start_acquisition()
        session.save("plain.csv")

        self.assertFalse(session.has_stimulus_labels)
        self.assertEqual(stimulus.state, StimulusState.IDLE)
        self.assertEqual(stimulus.event_log_rows(), [])
        self.assertIsNone(acquisition.save_calls[-1]["stimulus_code_for_time"])

    def test_session_stop_closes_active_stimulus_at_latest_sample_time(self) -> None:
        acquisition = FakeAcquisition()
        acquisition.state = AcquisitionState.RUNNING
        stimulus = StimulusController()
        stimulus.set_schedule([StimulusEvent(5, "hold", 3.0)])
        append_frame(acquisition, 0.1)
        session = RecordingSession(acquisition, stimulus)  # type: ignore[arg-type]

        session.start_stimulus()
        append_frame(acquisition, 0.8, counter=2)
        session.stop()

        rows = stimulus.event_log_rows()
        self.assertEqual(acquisition.state, AcquisitionState.STOPPED)
        self.assertEqual(stimulus.state, StimulusState.STOPPED)
        self.assertEqual(rows[0]["end_time_s"], 0.8)

    def test_on_frame_stops_stimulus_when_acquisition_fails(self) -> None:
        acquisition = FakeAcquisition()
        acquisition.state = AcquisitionState.RUNNING
        acquisition.fail_on_drain = True
        stimulus = StimulusController()
        stimulus.set_schedule([StimulusEvent(8, "lift", 2.0)])
        append_frame(acquisition, 0.0)
        session = RecordingSession(acquisition, stimulus)  # type: ignore[arg-type]
        session.start_stimulus()
        append_frame(acquisition, 0.4, counter=2)

        log: list[str] = []
        session.on_frame(log.append)

        self.assertEqual(acquisition.state, AcquisitionState.STOPPED)
        self.assertEqual(stimulus.state, StimulusState.STOPPED)
        self.assertIn("Stimulus timeline stopped because acquisition stopped.", log)

    def test_on_frame_stops_acquisition_when_stimulus_schedule_completes(self) -> None:
        acquisition = FakeAcquisition()
        acquisition.state = AcquisitionState.RUNNING
        stimulus = StimulusController()
        stimulus.set_schedule([StimulusEvent(2, "tap", 0.1)])
        append_frame(acquisition, 0.0)
        session = RecordingSession(acquisition, stimulus)  # type: ignore[arg-type]
        session.start_stimulus()
        append_frame(acquisition, 0.2, counter=2)

        log: list[str] = []
        session.on_frame(log.append)

        self.assertEqual(acquisition.state, AcquisitionState.STOPPED)
        self.assertEqual(stimulus.state, StimulusState.STOPPED)
        self.assertIn("Stimulus schedule completed.", log)


if __name__ == "__main__":
    unittest.main()
