"""Shared recording lifecycle for acquisition and optional stimulus labels."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fundamental.acquisition import AcquisitionController
from fundamental.messages import AcquisitionState
from fundamental.stimulus_model import StimulusController, StimulusState


LogSink = Callable[[str], None]


class RecordingSession:
    """Coordinate acquisition, stimulus annotations, and saving.

    The command/window shell still owns user interaction. This class owns the
    shared lifecycle that should not live in one specific UI block.
    """

    def __init__(
        self,
        acquisition: AcquisitionController,
        stimulus: StimulusController,
    ) -> None:
        self.acquisition = acquisition
        self.stimulus = stimulus
        self._stimulus_enabled_for_capture = False

    @property
    def has_stimulus_labels(self) -> bool:
        return self._stimulus_enabled_for_capture

    def on_frame(self, log_sink: LogSink | None = None) -> int:
        """Drain acquisition data and keep stimulus state aligned to sample time."""

        was_acquisition_running = self.acquisition.state == AcquisitionState.RUNNING
        was_stimulus_running = self.stimulus.state == StimulusState.RUNNING
        was_stimulus_active = self._is_stimulus_active()

        appended = self.acquisition.drain_queues(log_sink)
        if self._stimulus_enabled_for_capture:
            self.stimulus.update(self.acquisition.buffer.latest_time_s)

        if (
            was_stimulus_running
            and self.stimulus.state == StimulusState.STOPPED
            and self.acquisition.state == AcquisitionState.RUNNING
        ):
            self._log(log_sink, self.acquisition.stop())
            self._log(log_sink, "Stimulus schedule completed.")
        elif (
            was_acquisition_running
            and self.acquisition.state == AcquisitionState.STOPPED
            and was_stimulus_active
            and self._is_stimulus_active()
        ):
            self._log(log_sink, self.stimulus.stop(self.acquisition.buffer.latest_time_s))
            self._log(log_sink, "Stimulus timeline stopped because acquisition stopped.")

        return appended

    def start_acquisition(self) -> str:
        if self.acquisition.state == AcquisitionState.STOPPED:
            if self._is_stimulus_active():
                self.stimulus.stop(self.acquisition.buffer.latest_time_s)
            self._stimulus_enabled_for_capture = False
            self.stimulus.reset_timeline()
        return self.acquisition.start()

    def start_stimulus(self) -> list[str]:
        messages: list[str] = []
        if self.acquisition.state != AcquisitionState.RUNNING:
            messages.append(self.acquisition.start())
        self._stimulus_enabled_for_capture = True
        messages.append(self.stimulus.start(self.acquisition.buffer.latest_time_s))
        return messages

    def pause(self) -> list[str]:
        messages = [self.acquisition.pause()]
        if self._stimulus_enabled_for_capture and self.stimulus.state == StimulusState.RUNNING:
            messages.append(self.stimulus.pause(self.acquisition.buffer.latest_time_s))
        return messages

    def resume(self) -> list[str]:
        messages = [self.acquisition.start()]
        if self._stimulus_enabled_for_capture and self.stimulus.state == StimulusState.PAUSED:
            messages.append(self.stimulus.resume(self.acquisition.buffer.latest_time_s))
        return messages

    def stop(self) -> list[str]:
        messages = [self.acquisition.stop()]
        if self._stimulus_enabled_for_capture and self._is_stimulus_active():
            messages.append(self.stimulus.stop(self.acquisition.buffer.latest_time_s))
        return messages

    def restart_event(self) -> str:
        return self.stimulus.restart_event(self.acquisition.buffer.latest_time_s)

    def save(self, path: str | Path | None = None) -> str:
        if self._stimulus_enabled_for_capture:
            self.stimulus.update(self.acquisition.buffer.latest_time_s)
            return self.acquisition.save(
                path,
                stimulus_code_for_time=self.stimulus.stimulus_code_at,
                stimulus_log_rows=self.stimulus.event_log_rows(),
            )
        return self.acquisition.save(path)

    def _is_stimulus_active(self) -> bool:
        return self.stimulus.state in (StimulusState.RUNNING, StimulusState.PAUSED)

    @staticmethod
    def _log(log_sink: LogSink | None, message: str) -> None:
        if log_sink is not None and message:
            log_sink(message)
