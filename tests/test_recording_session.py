from __future__ import annotations

import queue
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from fundamental.capture_store import CaptureStore
from fundamental.messages import AcquisitionState
from fundamental.miil_model import MIILAction
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

    @property
    def timeline_time_s(self) -> float:
        return self.buffer.latest_time_s

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
        stimulus_code_for_sample=None,
        stimulus_metadata=None,
    ) -> str:
        self.save_calls.append(
            {
                "path": path,
                "stimulus_code_for_time": stimulus_code_for_time,
                "stimulus_code_for_sample": stimulus_code_for_sample,
                "stimulus_log_rows": stimulus_log_rows,
                "stimulus_metadata": stimulus_metadata,
            }
        )
        return "Saved."

    def drain_queues(self, log_sink=None, max_batches: int = 64) -> int:
        if self.fail_on_drain:
            self.state = AcquisitionState.STOPPED
            if log_sink is not None:
                log_sink("Serial failed.")
        return 0


class StartingFakeAcquisition(FakeAcquisition):
    def __init__(self) -> None:
        super().__init__()
        self.finish_start_on_drain = False

    def start(self) -> str:
        if self.state == AcquisitionState.STOPPED:
            self.buffer.reset()
        self.state = AcquisitionState.STARTING
        self.finish_start_on_drain = True
        return "Acquisition connecting."

    def drain_queues(self, log_sink=None, max_batches: int = 64) -> int:
        if self.finish_start_on_drain:
            self.finish_start_on_drain = False
            self.state = AcquisitionState.RUNNING
        return super().drain_queues(log_sink, max_batches)


class DivergentClockFakeAcquisition(FakeAcquisition):
    @property
    def timeline_time_s(self) -> float:
        return 100.0


class QueuedFakeAcquisition(FakeAcquisition):
    def __init__(self) -> None:
        super().__init__()
        self.data_queue: queue.Queue[StreamBlock] = queue.Queue()

    def drain_queues(self, log_sink=None, max_batches: int = 64) -> int:
        appended = 0
        for _ in range(max_batches):
            try:
                block = self.data_queue.get_nowait()
            except queue.Empty:
                break
            appended += self.buffer.append_block(block)
        return appended


def append_frame(acquisition: FakeAcquisition, time_s: float, counter: int = 1) -> None:
    acquisition.buffer.append_block(
        StreamBlock(
            ADS1299_STREAM_SPEC,
            (time_s,),
            ((counter, 0, 4, *VALUES),),
        )
    )


class RecordingSessionTests(unittest.TestCase):
    def test_miil_selected_acquisition_start_automatically_opens_no_stimulus(self) -> None:
        acquisition = FakeAcquisition()
        session = RecordingSession(acquisition, StimulusController())  # type: ignore[arg-type]
        self.assertIsNone(session.set_paradigm("miil"))

        message = session.start_acquisition()

        self.assertIn("no_stimulus", message)
        self.assertTrue(session.has_stimulus_labels)
        self.assertEqual(session.miil.current_code, 0)
        self.assertEqual(session.miil.state, StimulusState.RUNNING)

    def test_unapplied_miil_editor_changes_block_both_start_paths(self) -> None:
        acquisition = FakeAcquisition()
        session = RecordingSession(acquisition, StimulusController())  # type: ignore[arg-type]
        session.set_paradigm("miil")
        session.mark_miil_configuration_dirty()

        self.assertIn("Apply", session.start_acquisition())
        self.assertEqual(acquisition.state, AcquisitionState.STOPPED)
        self.assertTrue(any("Apply" in message for message in session.start_stimulus()))
        self.assertEqual(acquisition.state, AcquisitionState.STOPPED)

        session.apply_miil_actions([MIILAction("rest", "Rest", 1)])
        self.assertIn("no_stimulus", session.start_acquisition())

    def test_miil_uses_independent_stream_row_boundaries_and_saves_metadata(self) -> None:
        acquisition = FakeAcquisition()
        imu_spec = replace(ADS1299_STREAM_SPEC, stream_id="imu.test", display_name="IMU")
        acquisition.buffer.configure_streams((ADS1299_STREAM_SPEC, imu_spec), clear=True)
        session = RecordingSession(acquisition, StimulusController())  # type: ignore[arg-type]
        session.set_paradigm("miil")
        session.start_acquisition()
        append_frame(acquisition, 0.0)
        acquisition.buffer.append_block(
            StreamBlock(
                imu_spec,
                (0.0, 0.1),
                ((1, 0, 4, *VALUES), (2, 0, 4, *VALUES)),
            )
        )

        session.select_miil_action(2)
        append_frame(acquisition, 0.1, counter=2)
        acquisition.buffer.append_block(
            StreamBlock(imu_spec, (0.2,), ((3, 0, 4, *VALUES),))
        )
        session.stop()
        session.save("miil")

        save_call = acquisition.save_calls[-1]
        resolver = save_call["stimulus_code_for_sample"]
        self.assertIsNotNone(resolver)
        self.assertEqual(resolver(ADS1299_STREAM_SPEC.stream_id, 0, 99.0), 0)
        self.assertEqual(resolver(ADS1299_STREAM_SPEC.stream_id, 1, 0.0), 2)
        self.assertEqual(resolver("imu.test", 1, 99.0), 0)
        self.assertEqual(resolver("imu.test", 2, 0.0), 2)
        metadata = save_call["stimulus_metadata"]
        self.assertEqual(metadata["paradigm"], "miil")
        self.assertEqual(
            metadata["boundary_method"],
            "per_stream_row_cursor_with_shared_time_fallback",
        )

    def test_miil_boundary_drains_only_the_packets_already_queued_at_click(self) -> None:
        acquisition = QueuedFakeAcquisition()
        session = RecordingSession(acquisition, StimulusController())  # type: ignore[arg-type]
        session.set_paradigm("miil")
        session.start_acquisition()
        acquisition.data_queue.put(
            StreamBlock(ADS1299_STREAM_SPEC, (0.0,), ((1, 0, 4, *VALUES),))
        )
        acquisition.data_queue.put(
            StreamBlock(ADS1299_STREAM_SPEC, (0.1,), ((2, 0, 4, *VALUES),))
        )

        session.select_miil_action(1)
        append_frame(acquisition, 0.2, counter=3)
        session.stop()

        resolver = session.miil.sample_code
        stream_id = ADS1299_STREAM_SPEC.stream_id
        self.assertEqual(resolver(stream_id, 1, 99.0), 0)
        self.assertEqual(resolver(stream_id, 2, 0.0), 1)

    def test_miil_drop_invalidates_interval_and_pause_does_not_split_it(self) -> None:
        acquisition = FakeAcquisition()
        session = RecordingSession(acquisition, StimulusController())  # type: ignore[arg-type]
        session.set_paradigm("miil")
        session.apply_miil_actions([MIILAction("rest", "Rest", 1)])
        session.start_acquisition()
        append_frame(acquisition, 0.2)
        session.select_miil_action(1)
        append_frame(acquisition, 1.0, counter=2)

        session.pause()
        interval_count = len(session.miil.intervals)
        self.assertEqual(session.miil.current_elapsed_s(100.0), 0.8)
        session.resume()
        self.assertEqual(len(session.miil.intervals), interval_count)
        append_frame(acquisition, 1.5, counter=3)
        self.assertIn("from its beginning", session.drop_miil_current())
        session.stop()

        action_interval = session.miil.intervals[1]
        self.assertEqual(action_interval.original_code, 1)
        self.assertEqual(action_interval.effective_code, -1)
        self.assertEqual(
            session.miil.sample_code(ADS1299_STREAM_SPEC.stream_id, 1, 0.2),
            -1,
        )

    def test_miil_start_waits_for_managed_ready_barrier(self) -> None:
        acquisition = StartingFakeAcquisition()
        session = RecordingSession(acquisition, StimulusController())  # type: ignore[arg-type]
        session.set_paradigm("miil")

        message = session.start_acquisition()
        self.assertEqual(acquisition.state, AcquisitionState.STARTING)
        self.assertEqual(session.miil.state, StimulusState.IDLE)
        self.assertIn("all devices are ready", message)

        session.on_frame()
        self.assertEqual(session.miil.state, StimulusState.RUNNING)
        self.assertEqual(session.miil.current_code, 0)

    def test_stimulus_waits_until_all_acquisition_devices_are_ready(self) -> None:
        acquisition = StartingFakeAcquisition()
        stimulus = StimulusController()
        stimulus.set_schedule([StimulusEvent(4, "ready", 1.0)])
        session = RecordingSession(acquisition, stimulus)  # type: ignore[arg-type]

        messages = session.start_stimulus()
        self.assertEqual(acquisition.state, AcquisitionState.STARTING)
        self.assertEqual(stimulus.state, StimulusState.IDLE)
        self.assertTrue(any("after all acquisition devices are ready" in item for item in messages))

        session.on_frame()
        self.assertEqual(acquisition.state, AcquisitionState.RUNNING)
        self.assertEqual(stimulus.state, StimulusState.RUNNING)

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

    def test_timed_schedule_metadata_is_frozen_for_the_capture(self) -> None:
        acquisition = FakeAcquisition()
        acquisition.state = AcquisitionState.RUNNING
        stimulus = StimulusController()
        stimulus.set_schedule([StimulusEvent(7, "grip", 2.0)])
        session = RecordingSession(acquisition, stimulus)  # type: ignore[arg-type]
        session.start_stimulus()
        session.stop()
        stimulus.set_schedule([StimulusEvent(9, "next experiment", 5.0)])

        session.save("out.csv")

        schedule = acquisition.save_calls[-1]["stimulus_metadata"]["schedule"]
        self.assertEqual(schedule, [{"stimulus_code": 7, "label": "grip", "duration_s": 2.0}])

    def test_timed_schedule_remains_in_saved_sample_time_domain(self) -> None:
        acquisition = DivergentClockFakeAcquisition()
        acquisition.state = AcquisitionState.RUNNING
        append_frame(acquisition, 0.5)
        stimulus = StimulusController()
        stimulus.set_schedule([StimulusEvent(7, "grip", 2.0)])
        session = RecordingSession(acquisition, stimulus)  # type: ignore[arg-type]

        session.start_stimulus()

        self.assertEqual(stimulus.current_attempt.start_time_s, 0.5)  # type: ignore[union-attr]

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

    def test_on_frame_stops_paused_stimulus_when_managed_connection_fails(self) -> None:
        acquisition = FakeAcquisition()
        acquisition.state = AcquisitionState.RUNNING
        stimulus = StimulusController()
        stimulus.set_schedule([StimulusEvent(8, "lift", 2.0)])
        session = RecordingSession(acquisition, stimulus)  # type: ignore[arg-type]
        session.start_stimulus()
        session.pause()
        acquisition.fail_on_drain = True

        session.on_frame()

        self.assertEqual(acquisition.state, AcquisitionState.STOPPED)
        self.assertEqual(stimulus.state, StimulusState.STOPPED)

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
