"""Shared recording lifecycle for acquisition and stimulus annotations."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

from fundamental.acquisition import AcquisitionController
from fundamental.guided_sequence import (
    GuidedSequenceCommand,
    GuidedSequenceController,
    GuidedSequenceState,
)
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
        self.guided_sequence = GuidedSequenceController()
        self._selected_paradigm: StimulusParadigmId = TIMED_SCHEDULE_PARADIGM_ID
        self._capture_paradigm: StimulusParadigmId | None = None
        self._pending_stimulus_start: StimulusParadigmId | None = None
        self._miil_configuration_dirty = False
        self._guided_sequence_enabled = False
        self._guided_sequence_configuration_dirty = True
        self._guided_sequence_plan_revision = 0
        self._capture_guided_sequence: GuidedSequenceController | None = None
        self._capture_guided_sequence_plan_revision: int | None = None
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
    def guided_sequence_enabled(self) -> bool:
        return self._guided_sequence_enabled

    @property
    def guided_sequence_configuration_dirty(self) -> bool:
        return self._guided_sequence_configuration_dirty

    @property
    def capture_guided_sequence(self) -> GuidedSequenceController | None:
        """The frozen runner for the current/most-recent capture, if enabled."""

        return self._capture_guided_sequence

    @property
    def guided_sequence_runtime(self) -> GuidedSequenceController:
        """Expose capture progress when present, otherwise the configured plan."""

        if (
            self.acquisition.state == AcquisitionState.STOPPED
            and self._capture_guided_sequence is not None
            and self._capture_guided_sequence_plan_revision
            != self._guided_sequence_plan_revision
        ):
            return self.guided_sequence
        return self._capture_guided_sequence or self.guided_sequence

    @property
    def guided_sequence_completed(self) -> bool:
        runner = self._capture_guided_sequence
        return runner is not None and runner.state == GuidedSequenceState.COMPLETED

    @property
    def can_pause_and_save_guided_sequence(self) -> bool:
        return (
            self._capture_paradigm == MIIL_PARADIGM_ID
            and self._capture_guided_sequence is not None
            and self.acquisition.state == AcquisitionState.RUNNING
        )

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
        if self.guided_sequence.plan is not None:
            self._guided_sequence_configuration_dirty = True
        return f"Applied {len(self.miil.actions)} MIIL action(s)."

    def mark_miil_configuration_dirty(self) -> None:
        if self.acquisition.state == AcquisitionState.STOPPED:
            self._miil_configuration_dirty = True

    def set_guided_sequence_enabled(self, enabled: bool) -> str:
        if self.acquisition.state != AcquisitionState.STOPPED or self._runtime_is_active():
            return "Stop acquisition before changing Guided Sequence mode."
        if enabled and self._selected_paradigm != MIIL_PARADIGM_ID:
            return "Select MIIL before enabling Guided Sequence."
        if enabled and self._miil_configuration_dirty:
            return "Apply the edited MIIL actions before enabling Guided Sequence."
        self._guided_sequence_enabled = bool(enabled)
        if self._guided_sequence_enabled:
            return (
                "Guided Sequence enabled. Apply its action order before Start."
                if self._guided_sequence_configuration_dirty
                else "Guided Sequence enabled with the applied plan."
            )
        return "Guided Sequence disabled; MIIL manual controls will be used."

    def apply_guided_sequence(self, pattern: list[int], repeat_count: int) -> str:
        if self.acquisition.state != AcquisitionState.STOPPED:
            return "Stop acquisition before applying a Guided Sequence plan."
        if self._selected_paradigm != MIIL_PARADIGM_ID:
            return "Select MIIL before applying a Guided Sequence plan."
        if self._miil_configuration_dirty:
            return "Apply the edited MIIL actions before applying Guided Sequence."
        if not self._guided_sequence_enabled:
            return "Enable Guided Sequence before applying its plan."
        error = self.guided_sequence.apply_plan(
            pattern,
            repeat_count,
            (action.code for action in self.miil.actions),
        )
        if error:
            self._guided_sequence_configuration_dirty = True
            return error
        self._guided_sequence_configuration_dirty = False
        self._guided_sequence_plan_revision += 1
        plan = self.guided_sequence.plan
        assert plan is not None
        return (
            f"Applied Guided Sequence: {len(plan.pattern)} step(s) per group, "
            f"{plan.repeat_count} group(s), {plan.total_steps} total step(s)."
        )

    def clear_guided_sequence(self) -> str:
        if self.acquisition.state != AcquisitionState.STOPPED:
            return "Stop acquisition before clearing the Guided Sequence plan."
        error = self.guided_sequence.clear_plan()
        if error:
            return error
        self._guided_sequence_configuration_dirty = True
        self._guided_sequence_plan_revision += 1
        return "Guided Sequence plan cleared."

    def mark_guided_sequence_configuration_dirty(self) -> None:
        if self.acquisition.state == AcquisitionState.STOPPED:
            self._guided_sequence_configuration_dirty = True

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
            self._stop_guided_sequence(aborted=True)
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
        guided_error = self._guided_start_error()
        if guided_error:
            return guided_error
        if self.acquisition.state == AcquisitionState.STOPPED:
            self._prepare_new_capture()
            self._prepare_guided_capture()
        message = self.acquisition.start()

        if self._selected_paradigm == MIIL_PARADIGM_ID:
            self._capture_paradigm = MIIL_PARADIGM_ID
            if self.acquisition.state == AcquisitionState.STARTING:
                self._pending_stimulus_start = MIIL_PARADIGM_ID
                return f"{message} MIIL will begin with no_stimulus when all devices are ready."
            if self.acquisition.state == AcquisitionState.RUNNING:
                runtime_messages = self._start_miil_capture(self._capture_origin())
                return " ".join((message, *runtime_messages))
            self._capture_paradigm = None
            self._capture_guided_sequence = None
            self._capture_guided_sequence_plan_revision = None

        return message

    def start_stimulus(self) -> list[str]:
        """Start the selected paradigm through the same acquisition lifecycle."""

        if self._selected_paradigm == MIIL_PARADIGM_ID:
            guided_error = self._guided_start_error()
            if guided_error:
                return [guided_error]
            if self.acquisition.state == AcquisitionState.STOPPED:
                return [self.start_acquisition()]
            if self.acquisition.state == AcquisitionState.STARTING:
                self._capture_paradigm = MIIL_PARADIGM_ID
                self._pending_stimulus_start = MIIL_PARADIGM_ID
                if self._capture_guided_sequence is None:
                    self._prepare_guided_capture()
                return ["MIIL will start after all acquisition devices are ready."]
            if self.acquisition.state == AcquisitionState.RUNNING:
                if self.miil.state == StimulusState.RUNNING:
                    return ["MIIL is already running."]
                self.miil.reset_timeline()
                self._capture_paradigm = MIIL_PARADIGM_ID
                self._prepare_guided_capture()
                return self._start_miil_capture(self._snapshot_position())
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
            runner = self._capture_guided_sequence
            if runner is not None:
                guided_message = runner.pause()
                if guided_message.accepted:
                    messages.append(guided_message.message)
        elif (
            self._capture_paradigm == TIMED_SCHEDULE_PARADIGM_ID
            and self.stimulus.state == StimulusState.RUNNING
        ):
            messages.append(self.stimulus.pause(self.sample_time_s))
        return messages

    def resume(self) -> list[str]:
        if self.guided_sequence_completed:
            return ["Guided Sequence is complete; save or stop this experiment."]
        messages = [self.acquisition.start()]
        if self.acquisition.state != AcquisitionState.RUNNING:
            return messages
        if self._capture_paradigm == MIIL_PARADIGM_ID:
            if self.miil.state == StimulusState.PAUSED:
                messages.append(self.miil.resume(self._current_position()))
            runner = self._capture_guided_sequence
            if runner is not None:
                guided_message = runner.resume()
                if guided_message.accepted:
                    messages.append(guided_message.message)
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
        guided_message = self._stop_guided_sequence(aborted=False)
        if guided_message:
            messages.append(guided_message)
        return messages

    def restart_event(self) -> str:
        if self._selected_paradigm == MIIL_PARADIGM_ID:
            return "Restart Event belongs to Timed Schedule; use Drop Current Interval in MIIL."
        return self.stimulus.restart_event(self.sample_time_s)

    def select_miil_action(self, code: int) -> str:
        error = self._ensure_miil_command_available()
        if error:
            return error
        if self._capture_guided_sequence is not None:
            return (
                "Manual MIIL action selection is disabled while Guided Sequence "
                "controls this capture."
            )
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
        runner = self._capture_guided_sequence
        if runner is not None:
            command = runner.select_no_stimulus(position.time_s)
            return self._apply_guided_command(command, position)
        return self.miil.select_no_stimulus(position)

    def drop_miil_current(self) -> str:
        error = self._ensure_miil_command_available()
        if error:
            return error
        position = self._snapshot_position()
        failure = self._miil_snapshot_failure()
        if failure:
            return failure
        runner = self._capture_guided_sequence
        if runner is not None:
            command = runner.drop_current(position.time_s)
            return self._apply_guided_command(command, position)
        return self.miil.drop_current(position)

    def advance_guided_sequence(self) -> str:
        runner = self._capture_guided_sequence
        if runner is None:
            return "Guided Sequence is not active for this capture."
        error = self._ensure_miil_command_available()
        if error:
            return error
        position = self._snapshot_position()
        failure = self._miil_snapshot_failure()
        if failure:
            return failure
        command = runner.advance(position.time_s)
        return self._apply_guided_command(command, position)

    def save(self, path: str | Path | None = None) -> str:
        pause_messages: list[str] = []
        if self.can_pause_and_save_guided_sequence:
            pause_messages = self.pause()
            self.on_frame()
            if self.acquisition.state not in (
                AcquisitionState.PAUSED,
                AcquisitionState.STOPPED,
            ):
                return self._abort_guided_effect(
                    pause_messages,
                    "Acquisition could not pause before the checkpoint save.",
                )
        if self._capture_paradigm == MIIL_PARADIGM_ID:
            result = self.acquisition.save(
                path,
                stimulus_code_for_sample=self.miil.sample_code,
                stimulus_log_rows=self._miil_event_log_rows(),
                stimulus_metadata=self._miil_metadata_snapshot(),
            )
        elif self._capture_paradigm == TIMED_SCHEDULE_PARADIGM_ID:
            self.stimulus.update(self.sample_time_s)
            result = self.acquisition.save(
                path,
                stimulus_code_for_time=self.stimulus.stimulus_code_at,
                stimulus_log_rows=self.stimulus.event_log_rows(),
                stimulus_metadata=self._timed_schedule_metadata(),
            )
        else:
            result = self.acquisition.save(path)
        if pause_messages:
            runner = self._capture_guided_sequence
            save_note = (
                "Guided Sequence aborted during checkpoint pause; saved stopped capture."
                if runner is not None and runner.state == GuidedSequenceState.ABORTED
                else "Guided Sequence checkpoint."
            )
            return " ".join((*pause_messages, save_note, result))
        return result

    def _prepare_new_capture(self) -> None:
        if self._runtime_is_active():
            self._stop_annotation_runner()
        self._capture_paradigm = None
        self._pending_stimulus_start = None
        self._capture_guided_sequence = None
        self._capture_guided_sequence_plan_revision = None
        self.stimulus.reset_timeline()
        self.miil.reset_timeline()

    def _start_pending_runner_if_ready(self, log_sink: LogSink | None) -> None:
        paradigm = self._pending_stimulus_start
        if paradigm is None:
            return
        if self.acquisition.state == AcquisitionState.RUNNING:
            self._pending_stimulus_start = None
            if paradigm == MIIL_PARADIGM_ID:
                messages = self._start_miil_capture(self._capture_origin())
            else:
                messages = [self.stimulus.start(self.sample_time_s)]
            for message in messages:
                self._log(log_sink, message)
        elif self.acquisition.state == AcquisitionState.STOPPED:
            self._pending_stimulus_start = None
            self._capture_paradigm = None
            self._capture_guided_sequence = None
            self._capture_guided_sequence_plan_revision = None

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
        self._stop_guided_sequence(aborted=True)
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

    def _guided_start_error(self) -> str | None:
        if (
            self._selected_paradigm == MIIL_PARADIGM_ID
            and self._guided_sequence_enabled
            and (
                self._guided_sequence_configuration_dirty
                or self.guided_sequence.plan is None
            )
        ):
            return "Apply the Guided Sequence action order before starting acquisition."
        return None

    def _prepare_guided_capture(self) -> None:
        self._capture_guided_sequence = None
        self._capture_guided_sequence_plan_revision = None
        if (
            self._selected_paradigm != MIIL_PARADIGM_ID
            or not self._guided_sequence_enabled
        ):
            return
        plan = self.guided_sequence.plan
        if plan is None:
            raise RuntimeError("Guided Sequence was enabled without an applied plan.")
        runner = GuidedSequenceController()
        error = runner.apply_plan(
            plan.pattern,
            plan.repeat_count,
            (action.code for action in self.miil.actions),
        )
        if error:
            raise RuntimeError(f"Applied Guided Sequence became invalid: {error}")
        self._capture_guided_sequence = runner
        self._capture_guided_sequence_plan_revision = self._guided_sequence_plan_revision

    def _start_miil_capture(self, position: CapturePosition) -> list[str]:
        messages = [self.miil.start(position)]
        runner = self._capture_guided_sequence
        if runner is not None:
            command = runner.start(position.time_s)
            if command.message:
                messages.append(command.message)
        return messages

    def _apply_guided_command(
        self,
        command: GuidedSequenceCommand,
        position: CapturePosition,
    ) -> str:
        if not command.accepted:
            return command.message

        messages = [command.message]
        runner = self._capture_guided_sequence
        if command.select_action_code is not None:
            messages.append(self.miil.select_action(command.select_action_code, position))
            interval = self.miil.current_interval
            if (
                self.miil.state != StimulusState.RUNNING
                or self.miil.current_code != command.select_action_code
                or interval is None
            ):
                return self._abort_guided_effect(
                    messages,
                    "MIIL did not activate the requested planned action.",
                )
            if runner is not None:
                bind_error = runner.bind_active_miil_event(interval.event_index)
                if bind_error:
                    messages.append(bind_error)
                    return self._abort_guided_effect(
                        messages,
                        "The planned attempt could not be linked to its MIIL interval.",
                    )
        elif command.select_no_stimulus:
            messages.append(self.miil.select_no_stimulus(position))
            if (
                self.miil.state != StimulusState.RUNNING
                or self.miil.current_code != 0
            ):
                return self._abort_guided_effect(
                    messages,
                    "MIIL did not enter no_stimulus at the planned boundary.",
                )
        elif command.drop_current_interval:
            messages.append(self.miil.drop_current(position))
            if (
                self.miil.state != StimulusState.RUNNING
                or self.miil.current_code != -1
            ):
                return self._abort_guided_effect(
                    messages,
                    "MIIL did not mark the current attempt as drop_stimulus.",
                )

        if command.pause_acquisition:
            messages.extend(self.pause())
            if (
                self.acquisition.state != AcquisitionState.PAUSED
                or self.miil.state != StimulusState.PAUSED
            ):
                return self._abort_guided_effect(
                    messages,
                    "Acquisition did not reach the required completed Pause state.",
                )
        return " ".join(message for message in messages if message)

    def _abort_guided_effect(
        self,
        messages: list[str],
        reason: str,
    ) -> str:
        messages.append(f"Guided Sequence safety stop: {reason}")
        if self.acquisition.state != AcquisitionState.STOPPED:
            messages.append(self.acquisition.stop())
        runner_message = self._stop_annotation_runner()
        if runner_message:
            messages.append(runner_message)
        guided_message = self._stop_guided_sequence(aborted=True)
        if guided_message:
            messages.append(guided_message)
        return " ".join(message for message in messages if message)

    def _stop_guided_sequence(self, *, aborted: bool) -> str | None:
        runner = self._capture_guided_sequence
        if runner is None:
            return None
        command = runner.stop(aborted=aborted, at_s=self.timeline_time_s)
        return command.message if command.accepted else None

    def _miil_metadata_snapshot(self) -> dict[str, object]:
        metadata = self.miil.metadata_snapshot()
        runner = self._capture_guided_sequence
        if runner is None:
            return metadata

        guided = runner.metadata_snapshot()
        codebook = {
            int(item["stimulus_code"]): {
                "action": item["action"],
                "label": item["label"],
            }
            for item in metadata["codebook"]  # type: ignore[union-attr]
        }
        plan = runner.plan
        guided["enabled"] = True
        guided["advance_mode"] = "operator_enter_key"
        guided["status_at_save"] = self._guided_status_at_save(runner)
        guided["snapshot_position"] = {
            "time_s": self.timeline_time_s,
            "row_counts": dict(self.acquisition.buffer.stream_row_counts()),
        }
        guided["pattern_actions"] = (
            []
            if plan is None
            else [
                {
                    "stimulus_code": code,
                    "action": codebook.get(code, {}).get("action", ""),
                    "label": codebook.get(code, {}).get("label", ""),
                }
                for code in plan.pattern
            ]
        )
        metadata["guided_sequence"] = guided
        return metadata

    def _miil_event_log_rows(self) -> list[dict[str, object]]:
        rows = [dict(row) for row in self.miil.event_log_rows()]
        runner = self._capture_guided_sequence
        if runner is None:
            return rows
        attempt_rows = runner.metadata_snapshot()["attempts"]
        by_event_index = {
            int(attempt["miil_event_index"]): attempt
            for attempt in attempt_rows  # type: ignore[union-attr]
            if attempt.get("miil_event_index") is not None
        }
        for row in rows:
            attempt = by_event_index.get(int(row["event_index"]))
            if attempt is None:
                continue
            row.update(
                {
                    "guided_role": "planned_action",
                    "guided_group": attempt["group_number"],
                    "guided_step": attempt["step_number"],
                    "guided_attempt": attempt["step_attempt_number"],
                    "guided_outcome": attempt["outcome"],
                    "guided_ended_by": attempt["ended_by"],
                }
            )
        return rows

    @staticmethod
    def _guided_status_at_save(runner: GuidedSequenceController) -> str:
        if runner.state == GuidedSequenceState.COMPLETED:
            return "completed"
        if runner.state == GuidedSequenceState.PAUSED:
            return "partial_checkpoint"
        if runner.state == GuidedSequenceState.ABORTED:
            return "aborted"
        if runner.state == GuidedSequenceState.STOPPED:
            return "stopped_early"
        return runner.state.value

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
