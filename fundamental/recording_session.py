"""Shared recording lifecycle for acquisition and stimulus annotations."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

from fundamental.acquisition import AcquisitionController
from fundamental.messages import AcquisitionState
from fundamental.miil_model import (
    MIIL_PARADIGM_ID,
    CapturePosition,
    MIILAction,
    MIILController,
)
from fundamental.stimulus_model import StimulusController, StimulusEvent, StimulusState


LogSink = Callable[[str], None]
TIMED_SCHEDULE_PARADIGM_ID = "timed_schedule"
StimulusParadigmId = Literal["timed_schedule", "miil"]
STIMULUS_PARADIGMS: tuple[tuple[StimulusParadigmId, str], ...] = (
    (TIMED_SCHEDULE_PARADIGM_ID, "Timed Schedule"),
    (MIIL_PARADIGM_ID, "Manual Instruction Interval Labeling (MIIL)"),
)


class RecordingSession:
    """Coordinate acquisition and one selected annotation paradigm.

    Acquisition remains the sole owner of source start/pause/stop. Stimulus
    runners only observe the shared capture timeline and stream row cursors.
    """

    def __init__(
        self,
        acquisition: AcquisitionController,
        stimulus: StimulusController,
        miil: MIILController | None = None,
    ) -> None:
        self.acquisition = acquisition
        self.stimulus = stimulus
        self.miil = MIILController() if miil is None else miil
        self._selected_paradigm: StimulusParadigmId = TIMED_SCHEDULE_PARADIGM_ID
        self._capture_paradigm: StimulusParadigmId | None = None
        self._pending_stimulus_start: StimulusParadigmId | None = None
        self._miil_configuration_dirty = False
        self._timed_schedule_snapshot: tuple[StimulusEvent, ...] = tuple(
            self.stimulus.schedule
        )

    @property
    def selected_paradigm(self) -> StimulusParadigmId:
        return self._selected_paradigm

    @property
    def has_stimulus_labels(self) -> bool:
        return self._capture_paradigm is not None

    @property
    def miil_configuration_dirty(self) -> bool:
        return self._miil_configuration_dirty

    @property
    def timeline_time_s(self) -> float:
        value = getattr(self.acquisition, "timeline_time_s", None)
        if value is None:
            return max(0.0, float(self.acquisition.buffer.latest_time_s))
        return max(0.0, float(value))

    @property
    def sample_time_s(self) -> float:
        """Keep the legacy timed schedule in the saved samples' time domain."""

        return max(0.0, float(self.acquisition.buffer.latest_time_s))

    @property
    def selected_stimulus_state(self) -> StimulusState:
        if self._selected_paradigm == MIIL_PARADIGM_ID:
            return self.miil.state
        return self.stimulus.state

    def set_paradigm(self, paradigm: str) -> str | None:
        normalized = paradigm.strip().casefold()
        available = {item[0] for item in STIMULUS_PARADIGMS}
        if normalized not in available:
            return f"Unknown stimulus paradigm: {paradigm}"
        if self.acquisition.state != AcquisitionState.STOPPED or self._runtime_is_active():
            return "Stop acquisition before changing the stimulus paradigm."
        self._selected_paradigm = cast(StimulusParadigmId, normalized)
        return None

    def apply_miil_actions(self, actions: list[MIILAction]) -> str:
        if self.acquisition.state != AcquisitionState.STOPPED:
            return "Stop acquisition before applying MIIL actions."
        error = self.miil.apply_actions(actions)
        if error:
            self._miil_configuration_dirty = True
            return error
        self._miil_configuration_dirty = False
        return f"Applied {len(self.miil.actions)} MIIL action(s)."

    def mark_miil_configuration_dirty(self) -> None:
        if self.acquisition.state == AcquisitionState.STOPPED:
            self._miil_configuration_dirty = True

    def on_frame(self, log_sink: LogSink | None = None) -> int:
        """Drain source data and align the active annotation runner."""

        was_acquisition_active = self.acquisition.state in (
            AcquisitionState.STARTING,
            AcquisitionState.RUNNING,
            AcquisitionState.PAUSED,
        )
        was_timed_running = (
            self._capture_paradigm == TIMED_SCHEDULE_PARADIGM_ID
            and self.stimulus.state == StimulusState.RUNNING
        )
        was_runtime_active = self._runtime_is_active()

        appended = self.acquisition.drain_queues(log_sink)
        self._start_pending_runner_if_ready(log_sink)

        if self._capture_paradigm == TIMED_SCHEDULE_PARADIGM_ID:
            self.stimulus.update(self.sample_time_s)

        if (
            was_timed_running
            and self.stimulus.state == StimulusState.STOPPED
            and self.acquisition.state == AcquisitionState.RUNNING
        ):
            self._log(log_sink, self.acquisition.stop())
            self._log(log_sink, "Stimulus schedule completed.")
        elif (
            was_acquisition_active
            and self.acquisition.state == AcquisitionState.STOPPED
            and was_runtime_active
            and self._runtime_is_active()
        ):
            self._stop_annotation_runner()
            self._log(log_sink, "Stimulus timeline stopped because acquisition stopped.")

        return appended

    def start_acquisition(self) -> str:
        """Start all configured sources; MIIL, when selected, starts at code 0."""

        if self.acquisition.state == AcquisitionState.PAUSED:
            return " ".join(message for message in self.resume() if message)
        if (
            self.acquisition.state == AcquisitionState.STOPPED
            and self._selected_paradigm == MIIL_PARADIGM_ID
            and self._miil_configuration_dirty
        ):
            return "Apply the edited MIIL actions before starting acquisition."
        if self.acquisition.state == AcquisitionState.STOPPED:
            self._prepare_new_capture()
        message = self.acquisition.start()

        if self._selected_paradigm == MIIL_PARADIGM_ID:
            self._capture_paradigm = MIIL_PARADIGM_ID
            if self.acquisition.state == AcquisitionState.STARTING:
                self._pending_stimulus_start = MIIL_PARADIGM_ID
                return f"{message} MIIL will begin with no_stimulus when all devices are ready."
            if self.acquisition.state == AcquisitionState.RUNNING:
                miil_message = self.miil.start(self._capture_origin())
                return f"{message} {miil_message}"
            self._capture_paradigm = None

        return message

    def start_stimulus(self) -> list[str]:
        """Start the selected paradigm through the same acquisition lifecycle."""

        if self._selected_paradigm == MIIL_PARADIGM_ID:
            if self.acquisition.state == AcquisitionState.STOPPED:
                return [self.start_acquisition()]
            if self.acquisition.state == AcquisitionState.STARTING:
                self._capture_paradigm = MIIL_PARADIGM_ID
                self._pending_stimulus_start = MIIL_PARADIGM_ID
                return ["MIIL will start after all acquisition devices are ready."]
            if self.acquisition.state == AcquisitionState.RUNNING:
                if self.miil.state == StimulusState.RUNNING:
                    return ["MIIL is already running."]
                self.miil.reset_timeline()
                self._capture_paradigm = MIIL_PARADIGM_ID
                return [self.miil.start(self._snapshot_position())]
            return ["Resume or stop acquisition before starting MIIL."]

        messages: list[str] = []
        if self.acquisition.state == AcquisitionState.STOPPED:
            self._prepare_new_capture()
            messages.append(self.acquisition.start())
        elif self.acquisition.state != AcquisitionState.RUNNING:
            messages.append(self.acquisition.start())

        self._capture_paradigm = TIMED_SCHEDULE_PARADIGM_ID
        self._timed_schedule_snapshot = tuple(self.stimulus.schedule)
        if self.acquisition.state == AcquisitionState.STARTING:
            self._pending_stimulus_start = TIMED_SCHEDULE_PARADIGM_ID
            messages.append("Stimulus will start after all acquisition devices are ready.")
        elif self.acquisition.state == AcquisitionState.RUNNING:
            messages.append(self.stimulus.start(self.sample_time_s))
        else:
            self._capture_paradigm = None
        return messages

    def pause(self) -> list[str]:
        messages = [self.acquisition.pause()]
        if self.acquisition.state != AcquisitionState.PAUSED:
            return messages
        if self._capture_paradigm == MIIL_PARADIGM_ID:
            if self.miil.state == StimulusState.RUNNING:
                messages.append(self.miil.pause(self._current_position()))
        elif (
            self._capture_paradigm == TIMED_SCHEDULE_PARADIGM_ID
            and self.stimulus.state == StimulusState.RUNNING
        ):
            messages.append(self.stimulus.pause(self.sample_time_s))
        return messages

    def resume(self) -> list[str]:
        messages = [self.acquisition.start()]
        if self.acquisition.state != AcquisitionState.RUNNING:
            return messages
        if self._capture_paradigm == MIIL_PARADIGM_ID:
            if self.miil.state == StimulusState.PAUSED:
                messages.append(self.miil.resume(self._current_position()))
        elif (
            self._capture_paradigm == TIMED_SCHEDULE_PARADIGM_ID
            and self.stimulus.state == StimulusState.PAUSED
        ):
            messages.append(self.stimulus.resume(self.sample_time_s))
        return messages

    def stop(self) -> list[str]:
        self._pending_stimulus_start = None
        messages = [self.acquisition.stop()]
        runner_message = self._stop_annotation_runner()
        if runner_message:
            messages.append(runner_message)
        return messages

    def restart_event(self) -> str:
        if self._selected_paradigm == MIIL_PARADIGM_ID:
            return "Restart Event belongs to Timed Schedule; use Drop Current Interval in MIIL."
        return self.stimulus.restart_event(self.sample_time_s)

    def select_miil_action(self, code: int) -> str:
        error = self._ensure_miil_command_available()
        if error:
            return error
        position = self._snapshot_position()
        failure = self._miil_snapshot_failure()
        if failure:
            return failure
        return self.miil.select_action(int(code), position)

    def select_no_stimulus(self) -> str:
        error = self._ensure_miil_command_available()
        if error:
            return error
        position = self._snapshot_position()
        failure = self._miil_snapshot_failure()
        if failure:
            return failure
        return self.miil.select_no_stimulus(position)

    def drop_miil_current(self) -> str:
        error = self._ensure_miil_command_available()
        if error:
            return error
        position = self._snapshot_position()
        failure = self._miil_snapshot_failure()
        if failure:
            return failure
        return self.miil.drop_current(position)

    def save(self, path: str | Path | None = None) -> str:
        if self._capture_paradigm == MIIL_PARADIGM_ID:
            return self.acquisition.save(
                path,
                stimulus_code_for_sample=self.miil.sample_code,
                stimulus_log_rows=self.miil.event_log_rows(),
                stimulus_metadata=self.miil.metadata_snapshot(),
            )
        if self._capture_paradigm == TIMED_SCHEDULE_PARADIGM_ID:
            self.stimulus.update(self.sample_time_s)
            return self.acquisition.save(
                path,
                stimulus_code_for_time=self.stimulus.stimulus_code_at,
                stimulus_log_rows=self.stimulus.event_log_rows(),
                stimulus_metadata=self._timed_schedule_metadata(),
            )
        return self.acquisition.save(path)

    def _prepare_new_capture(self) -> None:
        if self._runtime_is_active():
            self._stop_annotation_runner()
        self._capture_paradigm = None
        self._pending_stimulus_start = None
        self.stimulus.reset_timeline()
        self.miil.reset_timeline()

    def _start_pending_runner_if_ready(self, log_sink: LogSink | None) -> None:
        paradigm = self._pending_stimulus_start
        if paradigm is None:
            return
        if self.acquisition.state == AcquisitionState.RUNNING:
            self._pending_stimulus_start = None
            if paradigm == MIIL_PARADIGM_ID:
                message = self.miil.start(self._capture_origin())
            else:
                message = self.stimulus.start(self.sample_time_s)
            self._log(log_sink, message)
        elif self.acquisition.state == AcquisitionState.STOPPED:
            self._pending_stimulus_start = None
            self._capture_paradigm = None

    def _stop_annotation_runner(self) -> str | None:
        if self._capture_paradigm == MIIL_PARADIGM_ID:
            if self.miil.state in (StimulusState.RUNNING, StimulusState.PAUSED):
                return self.miil.stop(self._current_position())
        elif self._capture_paradigm == TIMED_SCHEDULE_PARADIGM_ID:
            if self.stimulus.state in (StimulusState.RUNNING, StimulusState.PAUSED):
                return self.stimulus.stop(self.sample_time_s)
        return None

    def _runtime_is_active(self) -> bool:
        if self._capture_paradigm == MIIL_PARADIGM_ID:
            return self.miil.state in (StimulusState.RUNNING, StimulusState.PAUSED)
        if self._capture_paradigm == TIMED_SCHEDULE_PARADIGM_ID:
            return self.stimulus.state in (StimulusState.RUNNING, StimulusState.PAUSED)
        return False

    def _ensure_miil_command_available(self) -> str | None:
        if self._capture_paradigm != MIIL_PARADIGM_ID:
            return "MIIL is not active for this capture."
        if self.acquisition.state != AcquisitionState.RUNNING:
            return "MIIL actions can only be changed while acquisition is running."
        if self.miil.state != StimulusState.RUNNING:
            return "MIIL is not running."
        return None

    def _miil_snapshot_failure(self) -> str | None:
        if self.acquisition.state == AcquisitionState.RUNNING:
            return None
        self._stop_annotation_runner()
        return "Acquisition stopped while recording the MIIL boundary."

    def _snapshot_position(self) -> CapturePosition:
        """Capture an operator boundary after draining already queued packets."""

        event_time_s = self.timeline_time_s
        data_queue = getattr(self.acquisition, "data_queue", None)
        queued_batches = 1
        if data_queue is not None:
            queued_batches = max(1, int(data_queue.qsize()))
        self.acquisition.drain_queues(max_batches=queued_batches)
        return CapturePosition(event_time_s, self.acquisition.buffer.stream_row_counts())

    def _current_position(self) -> CapturePosition:
        return CapturePosition(
            self.timeline_time_s,
            self.acquisition.buffer.stream_row_counts(),
        )

    def _capture_origin(self) -> CapturePosition:
        return CapturePosition(
            0.0,
            {
                stream_id: 0
                for stream_id in self.acquisition.buffer.stream_row_counts()
            },
        )

    def _timed_schedule_metadata(self) -> dict[str, object]:
        return {
            "paradigm": TIMED_SCHEDULE_PARADIGM_ID,
            "paradigm_name": "Timed Schedule",
            "state": self.stimulus.state.value,
            "code_semantics": {"-1": "invalid restarted attempt", "0": "no stimulus"},
            "schedule": [
                {
                    "stimulus_code": event.code,
                    "label": event.label,
                    "duration_s": event.duration_s,
                }
                for event in self._timed_schedule_snapshot
            ],
        }

    @staticmethod
    def _log(log_sink: LogSink | None, message: str | None) -> None:
        if log_sink is not None and message:
            log_sink(message)
