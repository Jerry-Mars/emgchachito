"""Stimulus schedule and experiment timeline window."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from fundamental.acquisition import AcquisitionController
from fundamental.app_shell import FundamentalApp
from fundamental.commands import CommandSpec
from fundamental.messages import AcquisitionState
from fundamental.recording_session import RecordingSession
from fundamental.stimulus_model import StimulusController, StimulusEvent, StimulusState
from fundamental.window_manager import ManagedWindow


STIMULUS_WINDOW_TAG = "fundamental.stimulus.window"
STATUS_TEXT_TAG = "fundamental.stimulus.status"
CURRENT_TEXT_TAG = "fundamental.stimulus.current"
SCHEDULE_LIST_TAG = "fundamental.stimulus.schedule"
LOG_LIST_TAG = "fundamental.stimulus.log"
SELECTED_INDEX_TAG = "fundamental.stimulus.selected_index"
CODE_INPUT_TAG = "fundamental.stimulus.code"
LABEL_INPUT_TAG = "fundamental.stimulus.label"
DURATION_INPUT_TAG = "fundamental.stimulus.duration"
SAVE_PATH_INPUT_TAG = "fundamental.stimulus.save_path"
START_BUTTON_TAG = "fundamental.stimulus.start"
PAUSE_BUTTON_TAG = "fundamental.stimulus.pause"
RESUME_BUTTON_TAG = "fundamental.stimulus.resume"
STOP_BUTTON_TAG = "fundamental.stimulus.stop"
RESTART_BUTTON_TAG = "fundamental.stimulus.restart"
SAVE_BUTTON_TAG = "fundamental.stimulus.save"


def register(
    app: FundamentalApp,
    session: RecordingSession,
) -> None:
    app.window_manager.register(
        ManagedWindow(
            tag=STIMULUS_WINDOW_TAG,
            title="Stimulus",
            build=lambda: _build_window(app, session),
        )
    )
    app.register_command(
        CommandSpec(
            name="stimulus",
            description="Open stimulus schedule and experiment timeline.",
            handler=lambda context: _open_window(context.app, session),
            aliases=("indication",),
        )
    )
    app.register_frame_callback(lambda frame_app: _on_frame(frame_app, session))


def _open_window(
    app: FundamentalApp,
    session: RecordingSession,
) -> str | None:
    acquisition = session.acquisition
    stimulus = session.stimulus
    app.open_window(STIMULUS_WINDOW_TAG)
    _sync_save_path(acquisition, force=True)
    _refresh_window(acquisition, stimulus)
    return None


def _build_window(
    app: FundamentalApp,
    session: RecordingSession,
) -> None:
    acquisition = session.acquisition
    stimulus = session.stimulus
    with dpg.window(
        label="Stimulus",
        tag=STIMULUS_WINDOW_TAG,
        show=False,
        width=720,
        height=560,
        pos=(140, 100),
    ):
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Start",
                tag=START_BUTTON_TAG,
                width=90,
                callback=lambda *_: _run_action(app, lambda: _start(session)),
            )
            dpg.add_button(
                label="Pause",
                tag=PAUSE_BUTTON_TAG,
                width=90,
                callback=lambda *_: _run_action(app, lambda: _pause(session)),
            )
            dpg.add_button(
                label="Resume",
                tag=RESUME_BUTTON_TAG,
                width=90,
                callback=lambda *_: _run_action(app, lambda: _resume(session)),
            )
            dpg.add_button(
                label="Stop",
                tag=STOP_BUTTON_TAG,
                width=90,
                callback=lambda *_: _run_action(app, lambda: _stop(session)),
            )
            dpg.add_button(
                label="Restart Event",
                tag=RESTART_BUTTON_TAG,
                width=120,
                callback=lambda *_: _run_action(app, lambda: _restart_event(session)),
            )
            dpg.add_button(
                label="Save",
                tag=SAVE_BUTTON_TAG,
                width=90,
                callback=lambda *_: _run_action(app, lambda: _save(session)),
            )

        dpg.add_spacer(height=8)
        dpg.add_input_text(
            tag=SAVE_PATH_INPUT_TAG,
            label="Save Path",
            default_value=acquisition.last_save_path,
            width=520,
        )
        dpg.add_spacer(height=8)
        dpg.add_text("", tag=STATUS_TEXT_TAG)
        dpg.add_text("", tag=CURRENT_TEXT_TAG)
        dpg.add_spacer(height=10)

        with dpg.group(horizontal=True):
            dpg.add_input_int(
                tag=SELECTED_INDEX_TAG,
                label="Event",
                default_value=1,
                width=120,
                min_value=1,
                min_clamped=True,
                callback=lambda *_: _sync_inputs_from_selected(stimulus),
            )
            dpg.add_input_int(
                tag=CODE_INPUT_TAG,
                label="Code",
                default_value=1,
                width=120,
            )
            dpg.add_input_text(
                tag=LABEL_INPUT_TAG,
                label="Label",
                default_value="rest",
                width=180,
            )
            dpg.add_input_float(
                tag=DURATION_INPUT_TAG,
                label="Duration (s)",
                default_value=5.0,
                width=150,
                min_value=0.001,
                min_clamped=True,
            )

        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Add",
                width=80,
                callback=lambda *_: _run_action(app, lambda: _add_event(stimulus)),
            )
            dpg.add_button(
                label="Update",
                width=80,
                callback=lambda *_: _run_action(app, lambda: _update_event(stimulus)),
            )
            dpg.add_button(
                label="Delete",
                width=80,
                callback=lambda *_: _run_action(app, lambda: _delete_event(stimulus)),
            )
            dpg.add_button(
                label="Up",
                width=80,
                callback=lambda *_: _run_action(app, lambda: _move_event(stimulus, -1)),
            )
            dpg.add_button(
                label="Down",
                width=80,
                callback=lambda *_: _run_action(app, lambda: _move_event(stimulus, 1)),
            )

        dpg.add_spacer(height=8)
        with dpg.child_window(tag=SCHEDULE_LIST_TAG, width=-1, height=130, horizontal_scrollbar=True):
            pass
        dpg.add_spacer(height=8)
        with dpg.child_window(tag=LOG_LIST_TAG, width=-1, height=120, horizontal_scrollbar=True):
            pass

    _sync_inputs_from_selected(stimulus)
    _refresh_window(acquisition, stimulus)


def _on_frame(
    _app: FundamentalApp,
    session: RecordingSession,
) -> None:
    if dpg.does_item_exist(STIMULUS_WINDOW_TAG):
        _refresh_window(session.acquisition, session.stimulus)


def _start(session: RecordingSession) -> list[str]:
    messages = session.start_stimulus()
    _sync_save_path(session.acquisition, force=True)
    _refresh_window(session.acquisition, session.stimulus)
    return messages


def _pause(session: RecordingSession) -> list[str]:
    messages = session.pause()
    _refresh_window(session.acquisition, session.stimulus)
    return messages


def _resume(session: RecordingSession) -> list[str]:
    messages = session.resume()
    _refresh_window(session.acquisition, session.stimulus)
    return messages


def _stop(session: RecordingSession) -> list[str]:
    messages = session.stop()
    _refresh_window(session.acquisition, session.stimulus)
    return messages


def _restart_event(session: RecordingSession) -> str:
    result = session.restart_event()
    _refresh_window(session.acquisition, session.stimulus)
    return result


def _save(session: RecordingSession) -> str:
    acquisition = session.acquisition
    path = _save_path_from_window(acquisition)
    result = session.save(path)
    _sync_save_path(acquisition, force=True)
    _refresh_window(acquisition, session.stimulus)
    return result


def _add_event(stimulus: StimulusController) -> str:
    events = list(stimulus.schedule)
    events.append(_event_from_inputs())
    return _set_schedule(stimulus, events)


def _update_event(stimulus: StimulusController) -> str:
    index = _selected_index(stimulus)
    events = list(stimulus.schedule)
    if index < 0 or index >= len(events):
        return "Selected event is out of range."
    events[index] = _event_from_inputs()
    return _set_schedule(stimulus, events)


def _delete_event(stimulus: StimulusController) -> str:
    index = _selected_index(stimulus)
    events = list(stimulus.schedule)
    if index < 0 or index >= len(events):
        return "Selected event is out of range."
    del events[index]
    return _set_schedule(stimulus, events)


def _move_event(stimulus: StimulusController, direction: int) -> str:
    index = _selected_index(stimulus)
    next_index = index + direction
    events = list(stimulus.schedule)
    if index < 0 or index >= len(events) or next_index < 0 or next_index >= len(events):
        return "Selected event cannot move further."
    events[index], events[next_index] = events[next_index], events[index]
    if dpg.does_item_exist(SELECTED_INDEX_TAG):
        dpg.set_value(SELECTED_INDEX_TAG, next_index + 1)
    return _set_schedule(stimulus, events)


def _set_schedule(stimulus: StimulusController, events: list[StimulusEvent]) -> str:
    error = stimulus.set_schedule(events)
    _sync_inputs_from_selected(stimulus)
    _refresh_schedule(stimulus)
    if error:
        return error
    return "Stimulus schedule updated."


def _event_from_inputs() -> StimulusEvent:
    code = int(dpg.get_value(CODE_INPUT_TAG))
    label = str(dpg.get_value(LABEL_INPUT_TAG)).strip()
    duration_s = float(dpg.get_value(DURATION_INPUT_TAG))
    return StimulusEvent(code=code, label=label, duration_s=duration_s)


def _selected_index(stimulus: StimulusController) -> int:
    if not dpg.does_item_exist(SELECTED_INDEX_TAG):
        return 0
    value = int(dpg.get_value(SELECTED_INDEX_TAG))
    if stimulus.schedule:
        value = min(max(1, value), len(stimulus.schedule))
        dpg.set_value(SELECTED_INDEX_TAG, value)
    return value - 1


def _sync_inputs_from_selected(stimulus: StimulusController) -> None:
    if not dpg.does_item_exist(SELECTED_INDEX_TAG):
        return
    if not stimulus.schedule:
        return
    event = stimulus.schedule[_selected_index(stimulus)]
    dpg.set_value(CODE_INPUT_TAG, event.code)
    dpg.set_value(LABEL_INPUT_TAG, event.label)
    dpg.set_value(DURATION_INPUT_TAG, event.duration_s)


def _refresh_window(acquisition: AcquisitionController, stimulus: StimulusController) -> None:
    if not dpg.does_item_exist(STIMULUS_WINDOW_TAG):
        return

    state = stimulus.state.value.upper()
    dpg.set_value(
        STATUS_TEXT_TAG,
        f"Stimulus: {state} | Acquisition: {acquisition.state.value.upper()} | Rows: {acquisition.buffer.row_count}",
    )
    dpg.set_value(CURRENT_TEXT_TAG, _current_text(stimulus, acquisition.buffer.latest_time_s))
    _sync_save_path(acquisition)
    _refresh_schedule(stimulus)
    _refresh_log(stimulus)

    running = stimulus.state == StimulusState.RUNNING
    paused = stimulus.state == StimulusState.PAUSED
    acquisition_running = acquisition.state == AcquisitionState.RUNNING
    _configure_if_exists(START_BUTTON_TAG, enabled=not running and not paused)
    _configure_if_exists(PAUSE_BUTTON_TAG, enabled=running)
    _configure_if_exists(RESUME_BUTTON_TAG, enabled=paused)
    _configure_if_exists(STOP_BUTTON_TAG, enabled=running or paused or acquisition_running)
    _configure_if_exists(RESTART_BUTTON_TAG, enabled=running)
    _configure_if_exists(
        SAVE_BUTTON_TAG,
        enabled=not acquisition_running and acquisition.buffer.row_count > 0,
    )


def _current_text(stimulus: StimulusController, sample_time_s: float) -> str:
    event = stimulus.current_event
    attempt = stimulus.current_attempt
    if event is None or attempt is None:
        return "Current: -"
    elapsed = max(0.0, sample_time_s - attempt.start_time_s)
    remaining = max(0.0, event.duration_s - elapsed)
    return (
        f"Current: #{stimulus.current_event_index + 1} "
        f"code {event.code} {event.label} | elapsed {elapsed:.3f}s | remaining {remaining:.3f}s"
    )


def _refresh_schedule(stimulus: StimulusController) -> None:
    if not dpg.does_item_exist(SCHEDULE_LIST_TAG):
        return
    dpg.delete_item(SCHEDULE_LIST_TAG, children_only=True)
    for index, event in enumerate(stimulus.schedule, start=1):
        dpg.add_text(
            f"{index:02d} | code {event.code:>3} | {event.duration_s:>7.3f}s | {event.label}",
            parent=SCHEDULE_LIST_TAG,
        )


def _refresh_log(stimulus: StimulusController) -> None:
    if not dpg.does_item_exist(LOG_LIST_TAG):
        return
    dpg.delete_item(LOG_LIST_TAG, children_only=True)
    for row in stimulus.event_log_rows():
        start_time = float(row["start_time_s"])
        end_time = row["end_time_s"]
        end_text = "-" if end_time is None else f"{float(end_time):.3f}"
        dpg.add_text(
            f"{int(row['event_index']):02d} | code {int(row['stimulus_code']):>3} "
            f"| {start_time:.3f}-{end_text}s | {row['status']} | {row['label']}",
            parent=LOG_LIST_TAG,
        )


def _run_action(app: FundamentalApp, action) -> None:
    result = action()
    if isinstance(result, list):
        for message in result:
            if message:
                app.log(message)
        return
    if result:
        app.log(result)


def _save_path_from_window(acquisition: AcquisitionController) -> str:
    if dpg.does_item_exist(SAVE_PATH_INPUT_TAG):
        value = str(dpg.get_value(SAVE_PATH_INPUT_TAG)).strip()
        if value:
            return value
    return acquisition.last_save_path


def _sync_save_path(acquisition: AcquisitionController, force: bool = False) -> None:
    if not dpg.does_item_exist(SAVE_PATH_INPUT_TAG):
        return
    current_value = str(dpg.get_value(SAVE_PATH_INPUT_TAG)).strip()
    if current_value and not force:
        return
    dpg.set_value(SAVE_PATH_INPUT_TAG, acquisition.last_save_path)


def _configure_if_exists(tag: str, **kwargs) -> None:
    if dpg.does_item_exist(tag):
        dpg.configure_item(tag, **kwargs)
