"""Stimulus paradigm configuration and experiment timeline window."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from fundamental.acquisition import AcquisitionController
from fundamental.app_shell import FundamentalApp
from fundamental.commands import CommandSpec
from fundamental.messages import AcquisitionState
from fundamental.miil_model import MIIL_PARADIGM_ID, MIILAction
from fundamental.recording_session import (
    STIMULUS_PARADIGMS,
    TIMED_SCHEDULE_PARADIGM_ID,
    RecordingSession,
)
from fundamental.stimulus_model import StimulusController, StimulusEvent, StimulusState
from fundamental.window_manager import ManagedWindow


STIMULUS_WINDOW_TAG = "fundamental.stimulus.window"
PARADIGM_SELECT_TAG = "fundamental.stimulus.paradigm"
STATUS_TEXT_TAG = "fundamental.stimulus.status"
CURRENT_TEXT_TAG = "fundamental.stimulus.current"
SAVE_PATH_INPUT_TAG = "fundamental.stimulus.save_path"
START_BUTTON_TAG = "fundamental.stimulus.start"
PAUSE_BUTTON_TAG = "fundamental.stimulus.pause"
RESUME_BUTTON_TAG = "fundamental.stimulus.resume"
STOP_BUTTON_TAG = "fundamental.stimulus.stop"
SAVE_BUTTON_TAG = "fundamental.stimulus.save"

TIMED_GROUP_TAG = "fundamental.stimulus.timed.group"
SCHEDULE_LIST_TAG = "fundamental.stimulus.schedule"
LOG_LIST_TAG = "fundamental.stimulus.log"
SELECTED_INDEX_TAG = "fundamental.stimulus.selected_index"
CODE_INPUT_TAG = "fundamental.stimulus.code"
LABEL_INPUT_TAG = "fundamental.stimulus.label"
DURATION_INPUT_TAG = "fundamental.stimulus.duration"
ADD_EVENT_BUTTON_TAG = "fundamental.stimulus.timed.add"
UPDATE_EVENT_BUTTON_TAG = "fundamental.stimulus.timed.update"
DELETE_EVENT_BUTTON_TAG = "fundamental.stimulus.timed.delete"
MOVE_UP_BUTTON_TAG = "fundamental.stimulus.timed.up"
MOVE_DOWN_BUTTON_TAG = "fundamental.stimulus.timed.down"
RESTART_BUTTON_TAG = "fundamental.stimulus.restart"

MIIL_GROUP_TAG = "fundamental.stimulus.miil.group"
MIIL_ACTION_EDITOR_TAG = "fundamental.stimulus.miil.action_editor"
MIIL_ADD_ACTION_BUTTON_TAG = "fundamental.stimulus.miil.add_action"
MIIL_APPLY_BUTTON_TAG = "fundamental.stimulus.miil.apply"
MIIL_ACTION_BUTTONS_TAG = "fundamental.stimulus.miil.action_buttons"
MIIL_NO_STIMULUS_BUTTON_TAG = "fundamental.stimulus.miil.no_stimulus"
MIIL_DROP_BUTTON_TAG = "fundamental.stimulus.miil.drop"
MIIL_CURRENT_PANEL_TAG = "fundamental.stimulus.miil.current_panel"
MIIL_CURRENT_TEXT_TAG = "fundamental.stimulus.miil.current"
MIIL_HISTORY_TAG = "fundamental.stimulus.miil.history"

PARADIGM_LABEL_BY_ID = dict(STIMULUS_PARADIGMS)
PARADIGM_ID_BY_LABEL = {label: paradigm for paradigm, label in STIMULUS_PARADIGMS}

_miil_editor_row_count = 0
_miil_button_signature: tuple[tuple[int, str], ...] = ()
_miil_history_signature: tuple[tuple[object, ...], ...] | None = None


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
            description="Open stimulus paradigm configuration and experiment timeline.",
            handler=lambda context: _open_window(context.app, session),
            aliases=("indication",),
        )
    )
    app.register_frame_callback(lambda frame_app: _on_frame(frame_app, session))


def _open_window(
    app: FundamentalApp,
    session: RecordingSession,
) -> str | None:
    app.open_window(STIMULUS_WINDOW_TAG)
    _sync_save_path(session.acquisition, force=True)
    _refresh_window(session)
    return None


def _build_window(
    app: FundamentalApp,
    session: RecordingSession,
) -> None:
    global _miil_history_signature
    _miil_history_signature = None
    acquisition = session.acquisition
    stimulus = session.stimulus
    with dpg.window(
        label="Stimulus",
        tag=STIMULUS_WINDOW_TAG,
        show=False,
        width=840,
        height=540,
        pos=(100, 30),
    ):
        dpg.add_combo(
            tag=PARADIGM_SELECT_TAG,
            label="Paradigm",
            items=[label for _, label in STIMULUS_PARADIGMS],
            default_value=PARADIGM_LABEL_BY_ID[session.selected_paradigm],
            width=370,
            callback=lambda *_: _on_paradigm_changed(app, session),
        )

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
                label="Save",
                tag=SAVE_BUTTON_TAG,
                width=90,
                callback=lambda *_: _run_action(app, lambda: _save(session)),
            )

        dpg.add_spacer(height=6)
        dpg.add_input_text(
            tag=SAVE_PATH_INPUT_TAG,
            label="Save Path",
            default_value=acquisition.last_save_path,
            width=610,
        )
        dpg.add_text("", tag=STATUS_TEXT_TAG)
        dpg.add_text("", tag=CURRENT_TEXT_TAG, color=(255, 214, 64))
        dpg.add_separator()

        with dpg.group(tag=TIMED_GROUP_TAG):
            dpg.add_text("Timed Schedule Configuration")
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
                    tag=ADD_EVENT_BUTTON_TAG,
                    width=80,
                    callback=lambda *_: _run_action(app, lambda: _add_event(stimulus)),
                )
                dpg.add_button(
                    label="Update",
                    tag=UPDATE_EVENT_BUTTON_TAG,
                    width=80,
                    callback=lambda *_: _run_action(app, lambda: _update_event(stimulus)),
                )
                dpg.add_button(
                    label="Delete",
                    tag=DELETE_EVENT_BUTTON_TAG,
                    width=80,
                    callback=lambda *_: _run_action(app, lambda: _delete_event(stimulus)),
                )
                dpg.add_button(
                    label="Up",
                    tag=MOVE_UP_BUTTON_TAG,
                    width=80,
                    callback=lambda *_: _run_action(
                        app, lambda: _move_event(stimulus, -1)
                    ),
                )
                dpg.add_button(
                    label="Down",
                    tag=MOVE_DOWN_BUTTON_TAG,
                    width=80,
                    callback=lambda *_: _run_action(
                        app, lambda: _move_event(stimulus, 1)
                    ),
                )
                dpg.add_button(
                    label="Restart Event",
                    tag=RESTART_BUTTON_TAG,
                    width=120,
                    callback=lambda *_: _run_action(
                        app, lambda: _restart_event(session)
                    ),
                )

            dpg.add_text("Schedule")
            with dpg.child_window(
                tag=SCHEDULE_LIST_TAG,
                width=-1,
                height=130,
                horizontal_scrollbar=True,
            ):
                pass
            dpg.add_text("Event History")
            with dpg.child_window(
                tag=LOG_LIST_TAG,
                width=-1,
                height=150,
                horizontal_scrollbar=True,
            ):
                pass

        with dpg.group(tag=MIIL_GROUP_TAG, show=False):
            dpg.add_text("MIIL Action Configuration")
            dpg.add_text(
                "Configure each Action and its positive, unique Code before Start. "
                "Codes 0 and -1 are reserved.",
                color=(170, 180, 195),
            )
            with dpg.child_window(
                tag=MIIL_ACTION_EDITOR_TAG,
                width=-1,
                height=150,
                horizontal_scrollbar=True,
            ):
                pass
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Add Action",
                    tag=MIIL_ADD_ACTION_BUTTON_TAG,
                    width=110,
                    callback=lambda *_: _add_miil_action(app, session),
                )
                dpg.add_button(
                    label="Apply Actions",
                    tag=MIIL_APPLY_BUTTON_TAG,
                    width=120,
                    callback=lambda *_: _run_action(
                        app, lambda: _apply_miil_actions(app, session)
                    ),
                )

            dpg.add_separator()
            dpg.add_text("Manual Instruction Controls")
            with dpg.child_window(
                tag=MIIL_ACTION_BUTTONS_TAG,
                width=-1,
                height=58,
                horizontal_scrollbar=True,
            ):
                pass
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="No Stimulus (0)",
                    tag=MIIL_NO_STIMULUS_BUTTON_TAG,
                    width=180,
                    callback=lambda *_: _run_action(
                        app, session.select_no_stimulus
                    ),
                )
                dpg.add_button(
                    label="Drop Current Interval (-1)",
                    tag=MIIL_DROP_BUTTON_TAG,
                    width=230,
                    callback=lambda *_: _run_action(
                        app, session.drop_miil_current
                    ),
                )

            with dpg.child_window(
                tag=MIIL_CURRENT_PANEL_TAG,
                width=-1,
                height=58,
                border=True,
            ):
                dpg.add_text(
                    "Current: No Stimulus | code 0 | elapsed 0.000s",
                    tag=MIIL_CURRENT_TEXT_TAG,
                    color=(255, 214, 64),
                )

            dpg.add_text("Interval History (automatically follows the latest interval)")
            with dpg.child_window(
                tag=MIIL_HISTORY_TAG,
                width=-1,
                height=170,
                horizontal_scrollbar=True,
            ):
                pass

    _sync_inputs_from_selected(stimulus)
    _set_miil_editor_actions(list(session.miil.actions), session)
    _rebuild_miil_action_buttons(session, app=app, force=True)
    _refresh_window(session)


def _on_frame(
    _app: FundamentalApp,
    session: RecordingSession,
) -> None:
    if dpg.does_item_exist(STIMULUS_WINDOW_TAG):
        _refresh_window(session)


def _on_paradigm_changed(app: FundamentalApp, session: RecordingSession) -> None:
    label = str(dpg.get_value(PARADIGM_SELECT_TAG)).strip()
    paradigm = PARADIGM_ID_BY_LABEL.get(label)
    if paradigm is None:
        app.log(f"Unknown stimulus paradigm: {label}")
    else:
        error = session.set_paradigm(paradigm)
        if error:
            app.log(error)
    dpg.set_value(
        PARADIGM_SELECT_TAG,
        PARADIGM_LABEL_BY_ID[session.selected_paradigm],
    )
    _refresh_window(session)


def _start(session: RecordingSession) -> list[str]:
    messages = session.start_stimulus()
    _sync_save_path(session.acquisition, force=True)
    _refresh_window(session)
    return messages


def _pause(session: RecordingSession) -> list[str]:
    messages = session.pause()
    _refresh_window(session)
    return messages


def _resume(session: RecordingSession) -> list[str]:
    messages = session.resume()
    _refresh_window(session)
    return messages


def _stop(session: RecordingSession) -> list[str]:
    messages = session.stop()
    _refresh_window(session)
    return messages


def _restart_event(session: RecordingSession) -> str:
    result = session.restart_event()
    _refresh_window(session)
    return result


def _save(session: RecordingSession) -> str:
    acquisition = session.acquisition
    path = _save_path_from_window(acquisition)
    result = session.save(path)
    _sync_save_path(acquisition, force=True)
    _refresh_window(session)
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
    if not dpg.does_item_exist(SELECTED_INDEX_TAG) or not stimulus.schedule:
        return
    event = stimulus.schedule[_selected_index(stimulus)]
    dpg.set_value(CODE_INPUT_TAG, event.code)
    dpg.set_value(LABEL_INPUT_TAG, event.label)
    dpg.set_value(DURATION_INPUT_TAG, event.duration_s)


def _miil_field_tag(index: int, field_name: str) -> str:
    return f"fundamental.stimulus.miil.action.{index}.{field_name}"


def _miil_actions_from_window() -> list[MIILAction]:
    actions: list[MIILAction] = []
    for index in range(_miil_editor_row_count):
        action_tag = _miil_field_tag(index, "action")
        if not dpg.does_item_exist(action_tag):
            continue
        actions.append(
            MIILAction(
                action=_normalized_action_key(
                    str(dpg.get_value(action_tag)),
                    int(dpg.get_value(_miil_field_tag(index, "code"))),
                ),
                label=str(dpg.get_value(action_tag)).strip(),
                code=int(dpg.get_value(_miil_field_tag(index, "code"))),
            )
        )
    return actions


def _set_miil_editor_actions(
    actions: list[MIILAction],
    session: RecordingSession,
) -> None:
    global _miil_editor_row_count
    if not dpg.does_item_exist(MIIL_ACTION_EDITOR_TAG):
        return
    dpg.delete_item(MIIL_ACTION_EDITOR_TAG, children_only=True)
    _miil_editor_row_count = len(actions)
    for index, action in enumerate(actions):
        with dpg.group(horizontal=True, parent=MIIL_ACTION_EDITOR_TAG):
            dpg.add_input_int(
                tag=_miil_field_tag(index, "code"),
                label="Code",
                default_value=action.code,
                width=105,
                min_value=1,
                min_clamped=True,
                callback=lambda *_: _mark_miil_editor_dirty(session),
            )
            dpg.add_input_text(
                tag=_miil_field_tag(index, "action"),
                label="Action",
                default_value=action.label,
                width=320,
                callback=lambda *_: _mark_miil_editor_dirty(session),
            )
            dpg.add_button(
                label="Remove",
                tag=_miil_field_tag(index, "remove"),
                user_data=index,
                callback=lambda _sender, _app_data, row: _remove_miil_action(
                    int(row), session
                ),
            )


def _add_miil_action(app: FundamentalApp, session: RecordingSession) -> None:
    actions = _miil_actions_from_window()
    used_codes = {action.code for action in actions}
    code = 1
    while code in used_codes:
        code += 1
    actions.append(MIILAction(f"action_{code}", f"Action {code}", code))
    _set_miil_editor_actions(actions, session)
    session.mark_miil_configuration_dirty()
    _refresh_window(session)
    app.log(f"Added MIIL action {code}; edit it and apply before Start.")


def _normalized_action_key(label: str, code: int) -> str:
    """Create the internal metadata key without exposing another UI field."""

    parts: list[str] = []
    pending_separator = False
    for character in label.strip().casefold():
        if character.isalnum():
            if pending_separator and parts:
                parts.append("_")
            parts.append(character)
            pending_separator = False
        else:
            pending_separator = True
    return "".join(parts).strip("_") or f"action_{code}"


def _remove_miil_action(index: int, session: RecordingSession) -> None:
    actions = _miil_actions_from_window()
    if 0 <= index < len(actions):
        del actions[index]
        _set_miil_editor_actions(actions, session)
        session.mark_miil_configuration_dirty()
        _refresh_window(session)


def _mark_miil_editor_dirty(session: RecordingSession) -> None:
    session.mark_miil_configuration_dirty()
    _refresh_window(session)


def _apply_miil_actions(app: FundamentalApp, session: RecordingSession) -> str:
    message = session.apply_miil_actions(_miil_actions_from_window())
    _rebuild_miil_action_buttons(session, app=app, force=True)
    _refresh_window(session)
    return message


def _rebuild_miil_action_buttons(
    session: RecordingSession,
    *,
    app: FundamentalApp | None = None,
    force: bool = False,
) -> None:
    global _miil_button_signature
    if not dpg.does_item_exist(MIIL_ACTION_BUTTONS_TAG):
        return
    signature = tuple((action.code, action.label) for action in session.miil.actions)
    if not force and signature == _miil_button_signature:
        return
    dpg.delete_item(MIIL_ACTION_BUTTONS_TAG, children_only=True)
    with dpg.group(horizontal=True, parent=MIIL_ACTION_BUTTONS_TAG):
        for action in session.miil.actions:
            dpg.add_button(
                label=f"{action.label} ({action.code})",
                tag=_miil_runtime_button_tag(action.code),
                width=max(125, min(220, 34 + len(action.label) * 9)),
                user_data=action.code,
                callback=lambda _sender, _app_data, code: _run_miil_action(
                    app, session, int(code)
                ),
            )
    _miil_button_signature = signature


def _run_miil_action(
    app: FundamentalApp | None,
    session: RecordingSession,
    code: int,
) -> None:
    result = session.select_miil_action(code)
    if app is not None and result:
        app.log(result)
    _refresh_window(session)


def _miil_runtime_button_tag(code: int) -> str:
    return f"fundamental.stimulus.miil.runtime.{code}"


def _refresh_window(session: RecordingSession) -> None:
    if not dpg.does_item_exist(STIMULUS_WINDOW_TAG):
        return

    acquisition = session.acquisition
    stimulus = session.stimulus
    selected = session.selected_paradigm
    selected_state = session.selected_stimulus_state
    dpg.set_value(
        STATUS_TEXT_TAG,
        f"Paradigm: {PARADIGM_LABEL_BY_ID[selected]} | "
        f"Stimulus: {selected_state.value.upper()} | "
        f"Acquisition: {acquisition.state.value.upper()} | "
        f"Rows: {acquisition.buffer.row_count}"
        + (
            " | MIIL ACTIONS NOT APPLIED"
            if selected == MIIL_PARADIGM_ID and session.miil_configuration_dirty
            else ""
        ),
    )
    if selected == MIIL_PARADIGM_ID:
        current_text = _miil_current_text(session)
    else:
        current_text = _timed_current_text(stimulus, session.sample_time_s)
    dpg.set_value(CURRENT_TEXT_TAG, current_text)
    _configure_if_exists(
        CURRENT_TEXT_TAG,
        show=selected == TIMED_SCHEDULE_PARADIGM_ID,
    )
    dpg.set_value(PARADIGM_SELECT_TAG, PARADIGM_LABEL_BY_ID[selected])

    _configure_if_exists(TIMED_GROUP_TAG, show=selected == TIMED_SCHEDULE_PARADIGM_ID)
    _configure_if_exists(MIIL_GROUP_TAG, show=selected == MIIL_PARADIGM_ID)
    _sync_save_path(acquisition)
    _refresh_schedule(stimulus)
    _refresh_timed_log(stimulus)
    _rebuild_miil_action_buttons(session)
    _refresh_miil(session)

    running = selected_state == StimulusState.RUNNING
    paused = selected_state == StimulusState.PAUSED
    acquisition_running = acquisition.state == AcquisitionState.RUNNING
    acquisition_starting = acquisition.state == AcquisitionState.STARTING
    acquisition_stopped = acquisition.state == AcquisitionState.STOPPED
    _configure_if_exists(
        START_BUTTON_TAG,
        enabled=(
            not running
            and not paused
            and not acquisition_starting
            and not (
                selected == MIIL_PARADIGM_ID
                and session.miil_configuration_dirty
            )
        ),
    )
    _configure_if_exists(PAUSE_BUTTON_TAG, enabled=running and acquisition_running)
    _configure_if_exists(RESUME_BUTTON_TAG, enabled=paused)
    _configure_if_exists(
        STOP_BUTTON_TAG,
        enabled=running or paused or acquisition_running or acquisition_starting,
    )
    _configure_if_exists(
        SAVE_BUTTON_TAG,
        enabled=(
            acquisition.state in (AcquisitionState.PAUSED, AcquisitionState.STOPPED)
            and acquisition.buffer.row_count > 0
        ),
    )
    _configure_if_exists(
        PARADIGM_SELECT_TAG,
        enabled=acquisition_stopped and not running and not paused,
    )
    _configure_timed_editor(
        selected == TIMED_SCHEDULE_PARADIGM_ID
        and stimulus.state not in (StimulusState.RUNNING, StimulusState.PAUSED)
    )
    _configure_if_exists(
        RESTART_BUTTON_TAG,
        enabled=(
            selected == TIMED_SCHEDULE_PARADIGM_ID
            and running
            and acquisition_running
        ),
    )
    _configure_miil_editor(
        acquisition_stopped and selected == MIIL_PARADIGM_ID
    )
    miil_runtime_enabled = (
        selected == MIIL_PARADIGM_ID
        and acquisition_running
        and session.miil.state == StimulusState.RUNNING
    )
    for action in session.miil.actions:
        _configure_if_exists(
            _miil_runtime_button_tag(action.code), enabled=miil_runtime_enabled
        )
    _configure_if_exists(MIIL_NO_STIMULUS_BUTTON_TAG, enabled=miil_runtime_enabled)
    _configure_if_exists(MIIL_DROP_BUTTON_TAG, enabled=miil_runtime_enabled)


def _timed_current_text(stimulus: StimulusController, sample_time_s: float) -> str:
    event = stimulus.current_event
    attempt = stimulus.current_attempt
    if event is None or attempt is None:
        return "Current: -"
    elapsed = max(0.0, sample_time_s - attempt.start_time_s)
    remaining = max(0.0, event.duration_s - elapsed)
    return (
        f"Current: #{stimulus.current_event_index + 1} "
        f"code {event.code} {event.label} | elapsed {elapsed:.3f}s | "
        f"remaining {remaining:.3f}s"
    )


def _miil_current_text(session: RecordingSession) -> str:
    miil = session.miil
    if miil.state == StimulusState.IDLE:
        return "Current: - | MIIL not started"
    if miil.state == StimulusState.STOPPED:
        return "Current: - | MIIL stopped"
    elapsed_s = miil.current_elapsed_s(session.timeline_time_s)
    state_note = " (PAUSED; timer frozen)" if miil.state == StimulusState.PAUSED else ""
    return (
        f"Current: {miil.current_label} | code {miil.current_code} | "
        f"elapsed {elapsed_s:.3f}s{state_note}"
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


def _refresh_timed_log(stimulus: StimulusController) -> None:
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


def _refresh_miil(session: RecordingSession) -> None:
    global _miil_history_signature
    if not dpg.does_item_exist(MIIL_CURRENT_TEXT_TAG):
        return
    current_text = _miil_current_text(session)
    dpg.set_value(MIIL_CURRENT_TEXT_TAG, current_text)
    color = (255, 110, 100) if session.miil.current_code == -1 else (255, 214, 64)
    _configure_if_exists(MIIL_CURRENT_TEXT_TAG, color=color)

    if not dpg.does_item_exist(MIIL_HISTORY_TAG):
        return
    rows = session.miil.event_log_rows()
    signature = tuple(
        (
            row.get("event_index"),
            row.get("stimulus_code"),
            row.get("original_code"),
            row.get("label"),
            row.get("start_time_s"),
            row.get("end_time_s"),
            row.get("status"),
        )
        for row in rows
    )
    current_tag = f"{MIIL_HISTORY_TAG}.current"
    if signature != _miil_history_signature:
        dpg.delete_item(MIIL_HISTORY_TAG, children_only=True)
        for row in rows:
            is_current = row["end_time_s"] is None
            dpg.add_text(
                _miil_history_line(session, row),
                parent=MIIL_HISTORY_TAG,
                tag=current_tag if is_current else 0,
            )
        if not rows:
            dpg.add_text("No MIIL intervals recorded yet.", parent=MIIL_HISTORY_TAG)
        _miil_history_signature = signature
        try:
            dpg.set_y_scroll(
                MIIL_HISTORY_TAG,
                dpg.get_y_scroll_max(MIIL_HISTORY_TAG),
            )
        except SystemError:
            pass
    elif rows and rows[-1]["end_time_s"] is None and dpg.does_item_exist(current_tag):
        dpg.set_value(current_tag, _miil_history_line(session, rows[-1]))


def _miil_history_line(session: RecordingSession, row: dict[str, object]) -> str:
    start_time = float(row["start_time_s"])
    end_time = row["end_time_s"]
    is_current = end_time is None
    if is_current:
        duration_s = session.miil.current_elapsed_s(session.timeline_time_s)
        end_text = "now"
    else:
        duration_s = max(0.0, float(end_time) - start_time)
        end_text = f"{float(end_time):.3f}"
    original_code = int(row.get("original_code", row["stimulus_code"]))
    effective_code = int(row["stimulus_code"])
    code_text = f"code {effective_code}"
    if effective_code != original_code:
        code_text += f" (original {original_code})"
    marker = ">" if is_current else " "
    return (
        f"{marker} {int(row['event_index']):02d} | {code_text:<22} | "
        f"{start_time:.3f}-{end_text}s | duration {duration_s:.3f}s | "
        f"{row['status']} | {row['label']}"
    )


def _configure_timed_editor(enabled: bool) -> None:
    for tag in (
        SELECTED_INDEX_TAG,
        CODE_INPUT_TAG,
        LABEL_INPUT_TAG,
        DURATION_INPUT_TAG,
        ADD_EVENT_BUTTON_TAG,
        UPDATE_EVENT_BUTTON_TAG,
        DELETE_EVENT_BUTTON_TAG,
        MOVE_UP_BUTTON_TAG,
        MOVE_DOWN_BUTTON_TAG,
    ):
        _configure_if_exists(tag, enabled=enabled)


def _configure_miil_editor(enabled: bool) -> None:
    _configure_if_exists(MIIL_ADD_ACTION_BUTTON_TAG, enabled=enabled)
    _configure_if_exists(MIIL_APPLY_BUTTON_TAG, enabled=enabled)
    for index in range(_miil_editor_row_count):
        for field_name in ("code", "action", "remove"):
            _configure_if_exists(_miil_field_tag(index, field_name), enabled=enabled)


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
