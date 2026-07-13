"""Stimulus schedule and sample-time labeling."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


IDLE_STIMULUS_CODE = 0
INVALID_STIMULUS_CODE = -1


class StimulusState(Enum):
    """Stimulus timeline lifecycle."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass(frozen=True)
class StimulusEvent:
    """One planned stimulus event."""

    code: int
    label: str
    duration_s: float


@dataclass(frozen=True)
class StimulusEventAttempt:
    """One actual event interval in sample time."""

    event_index: int
    code: int
    label: str
    start_time_s: float
    end_time_s: float | None
    status: str

    @property
    def saved_code(self) -> int:
        if self.status == "restarted_invalid":
            return INVALID_STIMULUS_CODE
        return self.code


class StimulusController:
    """Own a planned schedule and actual event attempts."""

    def __init__(self) -> None:
        self.schedule: list[StimulusEvent] = [
            StimulusEvent(1, "rest", 5.0),
            StimulusEvent(2, "grip", 3.0),
            StimulusEvent(1, "rest", 5.0),
        ]
        self.state = StimulusState.IDLE
        self.current_event_index = 0
        self.attempts: list[StimulusEventAttempt] = []

    @property
    def current_event(self) -> StimulusEvent | None:
        if self.current_event_index < 0 or self.current_event_index >= len(self.schedule):
            return None
        return self.schedule[self.current_event_index]

    @property
    def current_attempt(self) -> StimulusEventAttempt | None:
        if not self.attempts:
            return None
        attempt = self.attempts[-1]
        if attempt.end_time_s is not None:
            return None
        return attempt

    def set_schedule(self, events: list[StimulusEvent]) -> str | None:
        if self.state in (StimulusState.RUNNING, StimulusState.PAUSED):
            return "Stop stimulus before changing the schedule."

        validated: list[StimulusEvent] = []
        for event in events:
            label = event.label.strip()
            if not label:
                return "Stimulus label cannot be empty."
            if event.code in (IDLE_STIMULUS_CODE, INVALID_STIMULUS_CODE):
                return "Stimulus code cannot be -1 or 0."
            if event.duration_s <= 0.0:
                return "Stimulus duration must be positive."
            validated.append(StimulusEvent(int(event.code), label, float(event.duration_s)))

        if not validated:
            return "Stimulus schedule cannot be empty."

        self.schedule = validated
        if self.state in (StimulusState.IDLE, StimulusState.STOPPED):
            self.current_event_index = 0
        return None

    def reset_timeline(self) -> str | None:
        if self.state in (StimulusState.RUNNING, StimulusState.PAUSED):
            return "Stop stimulus before resetting the timeline."
        self.state = StimulusState.IDLE
        self.current_event_index = 0
        self.attempts = []
        return None

    def start(self, sample_time_s: float) -> str:
        if self.state == StimulusState.RUNNING:
            return "Stimulus timeline is already running."
        if self.state == StimulusState.PAUSED:
            return self.resume(sample_time_s)
        if not self.schedule:
            return "Stimulus schedule is empty."

        self.attempts = []
        self.current_event_index = 0
        self.state = StimulusState.RUNNING
        self._start_current_attempt(sample_time_s)
        return "Stimulus timeline started."

    def pause(self, sample_time_s: float) -> str:
        self.update(sample_time_s)
        if self.state != StimulusState.RUNNING:
            return "Stimulus timeline is not running."
        self.state = StimulusState.PAUSED
        return "Stimulus timeline paused."

    def resume(self, sample_time_s: float) -> str:
        if self.state != StimulusState.PAUSED:
            return "Stimulus timeline is not paused."
        if self.current_attempt is None:
            self._start_current_attempt(sample_time_s)
        self.state = StimulusState.RUNNING
        return "Stimulus timeline resumed."

    def stop(self, sample_time_s: float) -> str:
        if self.state not in (StimulusState.RUNNING, StimulusState.PAUSED):
            return "Stimulus timeline is not active."
        self._close_current_attempt(sample_time_s, "stopped")
        self.state = StimulusState.STOPPED
        return "Stimulus timeline stopped."

    def restart_event(self, sample_time_s: float) -> str:
        self.update(sample_time_s)
        if self.state != StimulusState.RUNNING:
            return "Stimulus timeline is not running."
        if self.current_event is None or self.current_attempt is None:
            return "No active stimulus event to restart."

        self._close_current_attempt(sample_time_s, "restarted_invalid")
        self._start_current_attempt(sample_time_s)
        return f"Restarted event {self.current_event_index + 1}."

    def update(self, sample_time_s: float) -> None:
        if self.state != StimulusState.RUNNING:
            return

        while self.current_attempt is not None and self.current_event is not None:
            attempt = self.current_attempt
            event = self.current_event
            end_time = attempt.start_time_s + event.duration_s
            if sample_time_s < end_time:
                return

            self._close_current_attempt(end_time, "completed")
            self.current_event_index += 1
            if self.current_event_index >= len(self.schedule):
                self.state = StimulusState.STOPPED
                return
            self._start_current_attempt(end_time)

    def stimulus_code_at(self, sample_time_s: float) -> int:
        for attempt in self.attempts:
            end_time_s = attempt.end_time_s
            if end_time_s is None:
                if sample_time_s >= attempt.start_time_s:
                    return attempt.saved_code
            elif attempt.start_time_s <= sample_time_s < end_time_s:
                return attempt.saved_code
        return IDLE_STIMULUS_CODE

    def event_log_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for attempt in self.attempts:
            status = attempt.status
            if attempt == self.current_attempt and self.state == StimulusState.PAUSED:
                status = "paused"
            rows.append(
                {
                    "event_index": attempt.event_index,
                    "stimulus_code": attempt.saved_code,
                    "planned_code": attempt.code,
                    "label": attempt.label,
                    "start_time_s": attempt.start_time_s,
                    "end_time_s": attempt.end_time_s,
                    "status": status,
                }
            )
        return rows

    def _start_current_attempt(self, sample_time_s: float) -> None:
        event = self.current_event
        if event is None:
            return
        self.attempts.append(
            StimulusEventAttempt(
                event_index=self.current_event_index + 1,
                code=event.code,
                label=event.label,
                start_time_s=max(0.0, float(sample_time_s)),
                end_time_s=None,
                status="running",
            )
        )

    def _close_current_attempt(self, sample_time_s: float, status: str) -> None:
        attempt = self.current_attempt
        if attempt is None:
            return
        self.attempts[-1] = StimulusEventAttempt(
            event_index=attempt.event_index,
            code=attempt.code,
            label=attempt.label,
            start_time_s=attempt.start_time_s,
            end_time_s=max(attempt.start_time_s, float(sample_time_s)),
            status=status,
        )
