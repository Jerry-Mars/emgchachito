"""Stimulus paradigm configuration and experiment timeline window."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from fundamental.acquisition import AcquisitionController
from fundamental.app_shell import FundamentalApp
from fundamental.commands import CommandSpec
from fundamental.guided_sequence import GuidedSequenceState
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
MIIL_SETUP_GROUP_TAG = "fundamental.stimulus.miil.setup"
MIIL_SETUP_HEADER_TAG = "fundamental.stimulus.miil.setup_header"
MIIL_ACTION_EDITOR_TAG = "fundamental.stimulus.miil.action_editor"
MIIL_ADD_ACTION_BUTTON_TAG = "fundamental.stimulus.miil.add_action"
MIIL_APPLY_BUTTON_TAG = "fundamental.stimulus.miil.apply"
MIIL_MANUAL_CONTROLS_GROUP_TAG = "fundamental.stimulus.miil.manual_controls"
MIIL_ACTION_BUTTONS_TAG = "fundamental.stimulus.miil.action_buttons"
MIIL_EXCEPTION_CONTROLS_GROUP_TAG = "fundamental.stimulus.miil.exception_controls"
MIIL_NO_STIMULUS_BUTTON_TAG = "fundamental.stimulus.miil.no_stimulus"
MIIL_DROP_BUTTON_TAG = "fundamental.stimulus.miil.drop"
MIIL_CURRENT_PANEL_TAG = "fundamental.stimulus.miil.current_panel"
MIIL_CURRENT_TEXT_TAG = "fundamental.stimulus.miil.current"
MIIL_HISTORY_TAG = "fundamental.stimulus.miil.history"

MIIL_GUIDED_ENABLE_TAG = "fundamental.stimulus.miil.guided.enable"
MIIL_GUIDED_CONFIG_GROUP_TAG = "fundamental.stimulus.miil.guided.config"
MIIL_GUIDED_PALETTE_TAG = "fundamental.stimulus.miil.guided.palette"
MIIL_GUIDED_SEQUENCE_LIST_TAG = "fundamental.stimulus.miil.guided.sequence_list"
MIIL_GUIDED_REPEAT_TAG = "fundamental.stimulus.miil.guided.repeat"
MIIL_GUIDED_PREVIEW_TAG = "fundamental.stimulus.miil.guided.preview"
MIIL_GUIDED_APPLY_TAG = "fundamental.stimulus.miil.guided.apply"
MIIL_GUIDED_CLEAR_TAG = "fundamental.stimulus.miil.guided.clear"
MIIL_GUIDED_CONFIG_STATUS_TAG = "fundamental.stimulus.miil.guided.config_status"

MIIL_OPERATOR_MODE_TAG = "fundamental.stimulus.miil.operator.mode"
MIIL_OPERATOR_STATE_TAG = "fundamental.stimulus.miil.operator.state"
MIIL_OPERATOR_ACTION_TAG = "fundamental.stimulus.miil.operator.action"
MIIL_OPERATOR_CODE_TAG = "fundamental.stimulus.miil.operator.code"
MIIL_OPERATOR_ELAPSED_TAG = "fundamental.stimulus.miil.operator.elapsed"
MIIL_GUIDED_DETAILS_GROUP_TAG = "fundamental.stimulus.miil.guided.details"
MIIL_GUIDED_POSITION_TAG = "fundamental.stimulus.miil.guided.position"
MIIL_GUIDED_NEXT_TAG = "fundamental.stimulus.miil.guided.next"
MIIL_GUIDED_PROGRESS_TAG = "fundamental.stimulus.miil.guided.progress"
MIIL_GUIDED_ADVANCE_TAG = "fundamental.stimulus.miil.guided.advance"
MIIL_GUIDED_KEY_HINT_TAG = "fundamental.stimulus.miil.guided.key_hint"

PARADIGM_LABEL_BY_ID = dict(STIMULUS_PARADIGMS)
PARADIGM_ID_BY_LABEL = {label: paradigm for paradigm, label in STIMULUS_PARADIGMS}

_miil_editor_row_count = 0
_miil_button_signature: tuple[tuple[int, str], ...] = ()
_miil_history_signature: tuple[tuple[object, ...], ...] | None = None
_guided_palette_signature: tuple[tuple[int, str], ...] = ()
_guided_editor_codes: list[int] = []
_guided_editor_signature: tuple[int, ...] | None = None
_timed_schedule_signature: tuple[tuple[object, ...], ...] | None = None
_timed_log_signature: tuple[tuple[object, ...], ...] | None = None
_pending_guided_focus_restore = False
_last_guided_runtime_state: GuidedSequenceState | None = None


_GUIDED_ENTER_STATES = {
    GuidedSequenceState.WAITING_FIRST,
    GuidedSequenceState.ACTIVE,
    GuidedSequenceState.BUFFER,
    GuidedSequenceState.RETRY_PENDING,
}

_STATE_COLORS = {
    GuidedSequenceState.UNCONFIGURED: (155, 165, 180),
    GuidedSequenceState.READY: (100, 180, 230),
    GuidedSequenceState.WAITING_FIRST: (100, 180, 230),
    GuidedSequenceState.ACTIVE: (90, 205, 175),
    GuidedSequenceState.BUFFER: (155, 165, 180),
    GuidedSequenceState.RETRY_PENDING: (255, 105, 95),
    GuidedSequenceState.PAUSED: (255, 190, 75),
    GuidedSequenceState.COMPLETED: (100, 205, 125),
    GuidedSequenceState.STOPPED: (155, 165, 180),
    GuidedSequenceState.ABORTED: (255, 105, 95),
}


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
    app.register_context_enter_handler(
        lambda frame_app: _handle_guided_enter(frame_app, session)
    )


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
    global _guided_editor_codes
    global _guided_editor_signature
    global _guided_palette_signature
    global _miil_button_signature
    global _miil_history_signature
    global _last_guided_runtime_state
    global _pending_guided_focus_restore
    global _timed_log_signature
    global _timed_schedule_signature
    _guided_editor_codes = []
    _guided_editor_signature = None
    _guided_palette_signature = ()
    _miil_button_signature = ()
    _miil_history_signature = None
    _last_guided_runtime_state = None
    _pending_guided_focus_restore = False
    _timed_log_signature = None
    _timed_schedule_signature = None
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
            with dpg.group(tag=MIIL_SETUP_GROUP_TAG):
                with dpg.collapsing_header(
                    label="MIIL Action Configuration",
                    tag=MIIL_SETUP_HEADER_TAG,
                    default_open=True,
                ):
                    dpg.add_text(
                        "Configure each Action and its positive, unique Code before Start. "
                        "Codes 0 and -1 are reserved.",
                        color=(170, 180, 195),
                    )
                    with dpg.child_window(
                        tag=MIIL_ACTION_EDITOR_TAG,
                        width=-1,
                        height=145,
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

                dpg.add_checkbox(
                    label="Enable Guided Sequence (Enter-to-Advance)",
                    tag=MIIL_GUIDED_ENABLE_TAG,
                    default_value=session.guided_sequence_enabled,
                    callback=lambda *_: _on_guided_enabled_changed(app, session),
                )
                with dpg.group(tag=MIIL_GUIDED_CONFIG_GROUP_TAG, show=False):
                    dpg.add_text(
                        "Operator-paced order only: Enter advances instructions; "
                        "action duration remains manual.",
                        color=(170, 180, 195),
                    )
                    dpg.add_text("Click an applied action to append it")
                    with dpg.child_window(
                        tag=MIIL_GUIDED_PALETTE_TAG,
                        width=-1,
                        height=58,
                        horizontal_scrollbar=True,
                    ):
                        pass
                    dpg.add_text("Action order")
                    with dpg.child_window(
                        tag=MIIL_GUIDED_SEQUENCE_LIST_TAG,
                        width=-1,
                        height=125,
                        horizontal_scrollbar=True,
                    ):
                        pass
                    with dpg.group(horizontal=True):
                        dpg.add_input_int(
                            label="Repeat Groups",
                            tag=MIIL_GUIDED_REPEAT_TAG,
                            default_value=1,
                            min_value=1,
                            min_clamped=True,
                            width=155,
                            callback=lambda *_: _mark_guided_dirty(session),
                        )
                        dpg.add_button(
                            label="Apply Sequence",
                            tag=MIIL_GUIDED_APPLY_TAG,
                            width=135,
                            callback=lambda *_: _run_action(
                                app, lambda: _apply_guided_sequence(app, session)
                            ),
                        )
                        dpg.add_button(
                            label="Clear",
                            tag=MIIL_GUIDED_CLEAR_TAG,
                            width=85,
                            callback=lambda *_: _run_action(
                                app, lambda: _clear_guided_sequence(session)
                            ),
                        )
                    dpg.add_text("", tag=MIIL_GUIDED_PREVIEW_TAG)
                    dpg.add_text(
                        "Sequence not applied.",
                        tag=MIIL_GUIDED_CONFIG_STATUS_TAG,
                        color=(255, 190, 75),
                    )

            dpg.add_separator()
            dpg.add_text("MIIL Operator Console")
            with dpg.child_window(
                tag=MIIL_CURRENT_PANEL_TAG,
                width=-1,
                height=178,
                border=True,
            ):
                with dpg.group(horizontal=True):
                    dpg.add_text("MODE: MANUAL", tag=MIIL_OPERATOR_MODE_TAG)
                    dpg.add_text("STATE: IDLE", tag=MIIL_OPERATOR_STATE_TAG)
                with dpg.group(horizontal=True):
                    dpg.add_text(
                        "NO ACTIVE INSTRUCTION",
                        tag=MIIL_OPERATOR_ACTION_TAG,
                        color=(255, 214, 64),
                    )
                    dpg.add_text(
                        "CODE 0",
                        tag=MIIL_OPERATOR_CODE_TAG,
                        color=(155, 165, 180),
                    )
                dpg.add_text("Elapsed: 0.000 s", tag=MIIL_OPERATOR_ELAPSED_TAG)
                dpg.add_text(
                    "Current: No Stimulus | code 0 | elapsed 0.000s",
                    tag=MIIL_CURRENT_TEXT_TAG,
                    color=(170, 180, 195),
                )
                with dpg.group(tag=MIIL_GUIDED_DETAILS_GROUP_TAG, show=False):
                    dpg.add_text("Plan ready.", tag=MIIL_GUIDED_POSITION_TAG)
                    dpg.add_text("Next: -", tag=MIIL_GUIDED_NEXT_TAG)
                    dpg.add_progress_bar(
                        tag=MIIL_GUIDED_PROGRESS_TAG,
                        default_value=0.0,
                        overlay="0 / 0 completed",
                        width=-1,
                    )
                    with dpg.group(horizontal=True):
                        dpg.add_button(
                            label="Advance  [Enter]",
                            tag=MIIL_GUIDED_ADVANCE_TAG,
                            width=190,
                            height=32,
                            repeat=False,
                            callback=lambda *_: _run_guided_advance(app, session),
                        )
                        dpg.add_text(
                            "Start acquisition; first Enter begins step 1.",
                            tag=MIIL_GUIDED_KEY_HINT_TAG,
                            color=(170, 180, 195),
                        )

            with dpg.group(tag=MIIL_MANUAL_CONTROLS_GROUP_TAG):
                dpg.add_text("Manual Instruction Controls")
                with dpg.child_window(
                    tag=MIIL_ACTION_BUTTONS_TAG,
                    width=-1,
                    height=58,
                    horizontal_scrollbar=True,
                ):
                    pass

            with dpg.group(tag=MIIL_EXCEPTION_CONTROLS_GROUP_TAG):
                dpg.add_text("Exception Controls")
                with dpg.group(horizontal=True):
                    dpg.add_button(
                        label="No Stimulus (0)",
                        tag=MIIL_NO_STIMULUS_BUTTON_TAG,
                        width=285,
                        callback=lambda *_: _run_miil_no_stimulus(app, session),
                    )
                    dpg.add_button(
                        label="Drop Current Interval (-1)",
                        tag=MIIL_DROP_BUTTON_TAG,
                        width=245,
                        callback=lambda *_: _run_miil_drop(app, session),
                    )

            dpg.add_text("Interval History (automatically follows the latest interval)")
            with dpg.child_window(
                tag=MIIL_HISTORY_TAG,
                width=-1,
                height=150,
                horizontal_scrollbar=True,
            ):
                pass

    _sync_inputs_from_selected(stimulus)
    _set_miil_editor_actions(list(session.miil.actions), session)
    _rebuild_miil_action_buttons(session, app=app, force=True)
    _rebuild_guided_palette(session, app=app, force=True)
    plan = session.guided_sequence.plan
    _set_guided_editor_steps([] if plan is None else list(plan.pattern), session)
    if plan is not None:
        dpg.set_value(MIIL_GUIDED_REPEAT_TAG, plan.repeat_count)
    _refresh_window(session)


def _on_frame(
    _app: FundamentalApp,
    session: RecordingSession,
) -> None:
    global _last_guided_runtime_state
    global _pending_guided_focus_restore
    runner = session.capture_guided_sequence
    runtime_state = None if runner is None else runner.state
    if (
        runtime_state != _last_guided_runtime_state
        and runtime_state in _GUIDED_ENTER_STATES
        and session.acquisition.state == AcquisitionState.RUNNING
    ):
        _pending_guided_focus_restore = True
    _last_guided_runtime_state = runtime_state
    if dpg.does_item_exist(STIMULUS_WINDOW_TAG):
        _refresh_window(session)
    if _pending_guided_focus_restore and _app.window_manager.is_shown(
        STIMULUS_WINDOW_TAG
    ):
        try:
            dpg.focus_item(STIMULUS_WINDOW_TAG)
        except SystemError:
            pass
        _pending_guided_focus_restore = False


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
    global _pending_guided_focus_restore
    messages = session.start_stimulus()
    if session.guided_sequence_enabled:
        _pending_guided_focus_restore = True
    _sync_save_path(session.acquisition, force=True)
    _refresh_window(session)
    return messages


def _pause(session: RecordingSession) -> list[str]:
    messages = session.pause()
    _refresh_window(session)
    return messages


def _resume(session: RecordingSession) -> list[str]:
    global _pending_guided_focus_restore
    messages = session.resume()
    if session.capture_guided_sequence is not None:
        _pending_guided_focus_restore = True
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
    _rebuild_guided_palette(session, app=app, force=True)
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


def _guided_palette_button_tag(code: int) -> str:
    return f"fundamental.stimulus.miil.guided.palette.{code}"


def _guided_step_tag(index: int, field_name: str) -> str:
    return f"fundamental.stimulus.miil.guided.step.{index}.{field_name}"


def _on_guided_enabled_changed(
    app: FundamentalApp,
    session: RecordingSession,
) -> None:
    requested = bool(dpg.get_value(MIIL_GUIDED_ENABLE_TAG))
    message = session.set_guided_sequence_enabled(requested)
    dpg.set_value(MIIL_GUIDED_ENABLE_TAG, session.guided_sequence_enabled)
    if session.guided_sequence_enabled and not _guided_editor_codes:
        plan = session.guided_sequence.plan
        if plan is not None:
            _set_guided_editor_steps(list(plan.pattern), session)
            dpg.set_value(MIIL_GUIDED_REPEAT_TAG, plan.repeat_count)
    if message:
        app.log(message)
    _refresh_window(session)


def _rebuild_guided_palette(
    session: RecordingSession,
    *,
    app: FundamentalApp | None = None,
    force: bool = False,
) -> None:
    global _guided_palette_signature
    if not dpg.does_item_exist(MIIL_GUIDED_PALETTE_TAG):
        return
    signature = tuple((action.code, action.label) for action in session.miil.actions)
    if not force and signature == _guided_palette_signature:
        return
    dpg.delete_item(MIIL_GUIDED_PALETTE_TAG, children_only=True)
    with dpg.group(horizontal=True, parent=MIIL_GUIDED_PALETTE_TAG):
        for action in session.miil.actions:
            dpg.add_button(
                label=f"{action.label}  [CODE {action.code}]",
                tag=_guided_palette_button_tag(action.code),
                width=max(145, min(245, 66 + len(action.label) * 9)),
                user_data=action.code,
                callback=lambda _sender, _app_data, code: _run_optional_action(
                    app,
                    lambda: _add_guided_step(session, int(code)),
                ),
            )
    _guided_palette_signature = signature


def _set_guided_editor_steps(
    codes: list[int],
    session: RecordingSession,
    *,
    force: bool = False,
) -> None:
    global _guided_editor_codes
    global _guided_editor_signature
    normalized = [int(code) for code in codes]
    signature = tuple(normalized)
    _guided_editor_codes = normalized
    if not dpg.does_item_exist(MIIL_GUIDED_SEQUENCE_LIST_TAG):
        _guided_editor_signature = signature
        return
    if not force and signature == _guided_editor_signature:
        _refresh_guided_preview(session)
        return

    dpg.delete_item(MIIL_GUIDED_SEQUENCE_LIST_TAG, children_only=True)
    if not normalized:
        dpg.add_text(
            "No steps yet. Click an action above to build the order.",
            parent=MIIL_GUIDED_SEQUENCE_LIST_TAG,
            color=(170, 180, 195),
        )
    for index, code in enumerate(normalized):
        action = _miil_action_for_code(session, code)
        label = f"Unknown Code {code}" if action is None else action.label
        with dpg.group(horizontal=True, parent=MIIL_GUIDED_SEQUENCE_LIST_TAG):
            dpg.add_text(f"{index + 1:02d}", color=(170, 180, 195))
            dpg.add_text(
                f"[CODE {code}]  {label}",
                tag=_guided_step_tag(index, "label"),
                color=_action_color(code),
            )
            dpg.add_button(
                label="Up",
                tag=_guided_step_tag(index, "up"),
                width=52,
                enabled=index > 0,
                user_data=(index, -1),
                callback=lambda _sender, _app_data, move: _move_guided_step(
                    session, int(move[0]), int(move[1])
                ),
            )
            dpg.add_button(
                label="Down",
                tag=_guided_step_tag(index, "down"),
                width=58,
                enabled=index < len(normalized) - 1,
                user_data=(index, 1),
                callback=lambda _sender, _app_data, move: _move_guided_step(
                    session, int(move[0]), int(move[1])
                ),
            )
            dpg.add_button(
                label="Remove",
                tag=_guided_step_tag(index, "remove"),
                width=72,
                user_data=index,
                callback=lambda _sender, _app_data, row: _remove_guided_step(
                    session, int(row)
                ),
            )
    _guided_editor_signature = signature
    _refresh_guided_preview(session)


def _guided_steps_from_window() -> list[int]:
    return list(_guided_editor_codes)


def _add_guided_step(session: RecordingSession, code: int) -> str:
    action = _miil_action_for_code(session, code)
    if action is None:
        return f"MIIL action code {code} is not configured."
    _set_guided_editor_steps([*_guided_editor_codes, code], session, force=True)
    _mark_guided_dirty(session)
    try:
        dpg.set_y_scroll(
            MIIL_GUIDED_SEQUENCE_LIST_TAG,
            dpg.get_y_scroll_max(MIIL_GUIDED_SEQUENCE_LIST_TAG),
        )
    except SystemError:
        pass
    return f"Added '{action.label}' to the Guided Sequence order."


def _move_guided_step(
    session: RecordingSession,
    index: int,
    direction: int,
) -> None:
    next_index = index + direction
    if index < 0 or index >= len(_guided_editor_codes):
        return
    if next_index < 0 or next_index >= len(_guided_editor_codes):
        return
    codes = list(_guided_editor_codes)
    codes[index], codes[next_index] = codes[next_index], codes[index]
    _set_guided_editor_steps(codes, session, force=True)
    _mark_guided_dirty(session)


def _remove_guided_step(session: RecordingSession, index: int) -> None:
    if index < 0 or index >= len(_guided_editor_codes):
        return
    codes = list(_guided_editor_codes)
    del codes[index]
    _set_guided_editor_steps(codes, session, force=True)
    _mark_guided_dirty(session)


def _mark_guided_dirty(session: RecordingSession) -> None:
    session.mark_guided_sequence_configuration_dirty()
    _refresh_guided_preview(session)
    if dpg.does_item_exist(MIIL_GUIDED_CONFIG_STATUS_TAG):
        dpg.set_value(MIIL_GUIDED_CONFIG_STATUS_TAG, "SEQUENCE NOT APPLIED")
        dpg.configure_item(
            MIIL_GUIDED_CONFIG_STATUS_TAG,
            color=(255, 190, 75),
        )


def _apply_guided_sequence(
    _app: FundamentalApp,
    session: RecordingSession,
) -> str:
    repeat_count = int(dpg.get_value(MIIL_GUIDED_REPEAT_TAG))
    message = session.apply_guided_sequence(
        _guided_steps_from_window(),
        repeat_count,
    )
    plan = session.guided_sequence.plan
    if not session.guided_sequence_configuration_dirty and plan is not None:
        _set_guided_editor_steps(list(plan.pattern), session, force=True)
        dpg.set_value(MIIL_GUIDED_REPEAT_TAG, plan.repeat_count)
    _refresh_window(session)
    return message


def _clear_guided_sequence(session: RecordingSession) -> str:
    message = session.clear_guided_sequence()
    if session.guided_sequence.plan is None:
        _set_guided_editor_steps([], session, force=True)
        dpg.set_value(MIIL_GUIDED_REPEAT_TAG, 1)
    _refresh_window(session)
    return message


def _refresh_guided_preview(session: RecordingSession) -> None:
    if not dpg.does_item_exist(MIIL_GUIDED_PREVIEW_TAG):
        return
    repeat_count = max(1, int(dpg.get_value(MIIL_GUIDED_REPEAT_TAG)))
    labels = []
    for code in _guided_editor_codes:
        action = _miil_action_for_code(session, code)
        label = "Unknown" if action is None else action.label
        labels.append(f"{label} [{code}]")
    order = " -> ".join(labels) if labels else "(empty order)"
    total_steps = len(_guided_editor_codes) * repeat_count
    dpg.set_value(
        MIIL_GUIDED_PREVIEW_TAG,
        f"{order}   x {repeat_count} group(s) = {total_steps} planned step(s)",
    )


def _run_guided_advance(app: FundamentalApp, session: RecordingSession) -> None:
    global _pending_guided_focus_restore
    result = session.advance_guided_sequence()
    if result:
        app.log(result)
    _pending_guided_focus_restore = True
    _refresh_window(session)


def _run_miil_no_stimulus(app: FundamentalApp, session: RecordingSession) -> None:
    global _pending_guided_focus_restore
    result = session.select_no_stimulus()
    if result:
        app.log(result)
    if session.capture_guided_sequence is not None:
        _pending_guided_focus_restore = True
    _refresh_window(session)


def _run_miil_drop(app: FundamentalApp, session: RecordingSession) -> None:
    global _pending_guided_focus_restore
    result = session.drop_miil_current()
    if result:
        app.log(result)
    if session.capture_guided_sequence is not None:
        _pending_guided_focus_restore = True
    _refresh_window(session)


def _handle_guided_enter(app: FundamentalApp, session: RecordingSession) -> bool:
    runner = session.capture_guided_sequence
    if runner is None or session.selected_paradigm != MIIL_PARADIGM_ID:
        return False
    if (
        session.acquisition.state != AcquisitionState.RUNNING
        or runner.state not in _GUIDED_ENTER_STATES
    ):
        app.log(f"Guided Sequence Enter ignored while {runner.state.value}.")
        return True
    if not app.window_manager.is_shown(STIMULUS_WINDOW_TAG):
        app.log("Guided Sequence Enter ignored: open the Stimulus window first.")
        return True
    _run_guided_advance(app, session)
    return True


def _miil_action_for_code(session: RecordingSession, code: int) -> MIILAction | None:
    return next((action for action in session.miil.actions if action.code == code), None)


def _action_color(code: int) -> tuple[int, int, int]:
    palette = (
        (95, 180, 245),
        (170, 125, 245),
        (80, 205, 165),
        (245, 165, 80),
        (230, 105, 155),
    )
    return palette[(max(1, int(code)) - 1) % len(palette)]


def _guided_ui_mode(session: RecordingSession) -> bool:
    """Use the frozen capture mode only while a capture is in progress."""

    if session.acquisition.state != AcquisitionState.STOPPED:
        return session.capture_guided_sequence is not None
    return session.guided_sequence_enabled


def _refresh_window(session: RecordingSession) -> None:
    if not dpg.does_item_exist(STIMULUS_WINDOW_TAG):
        return

    acquisition = session.acquisition
    stimulus = session.stimulus
    selected = session.selected_paradigm
    selected_state = session.selected_stimulus_state
    guided_mode = _guided_ui_mode(session)
    guided_dirty = (
        selected == MIIL_PARADIGM_ID
        and session.guided_sequence_enabled
        and session.guided_sequence_configuration_dirty
    )
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
        )
        + (" | GUIDED SEQUENCE NOT APPLIED" if guided_dirty else ""),
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
    _rebuild_guided_palette(session)
    _refresh_guided_preview(session)
    _refresh_miil(session)
    _refresh_operator_console(session)

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
            and not guided_dirty
        ),
    )
    _configure_if_exists(PAUSE_BUTTON_TAG, enabled=running and acquisition_running)
    _configure_if_exists(
        RESUME_BUTTON_TAG,
        enabled=paused and not session.guided_sequence_completed,
    )
    _configure_if_exists(
        STOP_BUTTON_TAG,
        enabled=running or paused or acquisition_running or acquisition_starting,
    )
    pause_and_save = session.can_pause_and_save_guided_sequence
    _configure_if_exists(
        SAVE_BUTTON_TAG,
        enabled=(
            acquisition.buffer.row_count > 0
            and (
                acquisition.state in (
                    AcquisitionState.PAUSED,
                    AcquisitionState.STOPPED,
                )
                or pause_and_save
            )
        ),
        label="Pause & Save" if pause_and_save else "Save",
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
    _configure_if_exists(
        MIIL_SETUP_GROUP_TAG,
        show=selected == MIIL_PARADIGM_ID and acquisition_stopped,
    )
    _configure_if_exists(
        MIIL_GUIDED_ENABLE_TAG,
        enabled=(
            acquisition_stopped
            and selected == MIIL_PARADIGM_ID
            and not session.miil_configuration_dirty
        ),
    )
    if dpg.does_item_exist(MIIL_GUIDED_ENABLE_TAG):
        dpg.set_value(MIIL_GUIDED_ENABLE_TAG, session.guided_sequence_enabled)
    _configure_if_exists(
        MIIL_GUIDED_CONFIG_GROUP_TAG,
        show=(
            selected == MIIL_PARADIGM_ID
            and acquisition_stopped
            and session.guided_sequence_enabled
        ),
    )
    _configure_if_exists(
        MIIL_MANUAL_CONTROLS_GROUP_TAG,
        show=selected == MIIL_PARADIGM_ID and not guided_mode,
    )
    _configure_if_exists(
        MIIL_GUIDED_DETAILS_GROUP_TAG,
        show=selected == MIIL_PARADIGM_ID and guided_mode,
    )
    _configure_guided_editor(
        acquisition_stopped
        and selected == MIIL_PARADIGM_ID
        and session.guided_sequence_enabled
    )
    if dpg.does_item_exist(MIIL_GUIDED_CONFIG_STATUS_TAG):
        if not session.guided_sequence_enabled:
            guided_status = "GUIDED SEQUENCE DISABLED"
            guided_status_color = (155, 165, 180)
        elif session.guided_sequence_configuration_dirty:
            guided_status = "SEQUENCE NOT APPLIED"
            guided_status_color = (255, 190, 75)
        else:
            guided_status = "SEQUENCE APPLIED - READY"
            guided_status_color = (100, 205, 125)
        dpg.set_value(MIIL_GUIDED_CONFIG_STATUS_TAG, guided_status)
        dpg.configure_item(
            MIIL_GUIDED_CONFIG_STATUS_TAG,
            color=guided_status_color,
        )
    miil_runtime_enabled = (
        selected == MIIL_PARADIGM_ID
        and acquisition_running
        and session.miil.state == StimulusState.RUNNING
    )
    for action in session.miil.actions:
        _configure_if_exists(
            _miil_runtime_button_tag(action.code),
            enabled=miil_runtime_enabled and not guided_mode,
        )
    runner = session.capture_guided_sequence
    guided_state = None if runner is None else runner.state
    if guided_mode:
        no_enabled = miil_runtime_enabled and guided_state in (
            GuidedSequenceState.ACTIVE,
            GuidedSequenceState.RETRY_PENDING,
        )
        drop_enabled = (
            miil_runtime_enabled and guided_state == GuidedSequenceState.ACTIVE
        )
        no_label = "End Step & Insert No-Stimulus Buffer (0)"
        drop_label = "Drop Current Attempt (-1)"
    else:
        no_enabled = miil_runtime_enabled and session.miil.current_code != 0
        drop_enabled = miil_runtime_enabled and session.miil.current_code > 0
        no_label = "No Stimulus (0)"
        drop_label = "Drop Current Interval (-1)"
    _configure_if_exists(
        MIIL_NO_STIMULUS_BUTTON_TAG,
        enabled=no_enabled,
        label=no_label,
    )
    _configure_if_exists(
        MIIL_DROP_BUTTON_TAG,
        enabled=drop_enabled,
        label=drop_label,
    )
    _configure_if_exists(
        MIIL_GUIDED_ADVANCE_TAG,
        enabled=(
            miil_runtime_enabled
            and runner is not None
            and runner.state in _GUIDED_ENTER_STATES
        ),
    )


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


def _refresh_operator_console(session: RecordingSession) -> None:
    if not dpg.does_item_exist(MIIL_OPERATOR_STATE_TAG):
        return

    guided_mode = _guided_ui_mode(session)
    runner = session.guided_sequence_runtime
    if guided_mode:
        state_text = runner.state.value.upper().replace("_", " ")
        state_color = _STATE_COLORS.get(runner.state, (170, 180, 195))
        mode_text = "MODE: GUIDED SEQUENCE (ENTER-PACED)"
    else:
        state_text = session.miil.state.value.upper()
        if session.miil.current_code == -1:
            state_color = (255, 105, 95)
        elif session.miil.state == StimulusState.PAUSED:
            state_color = (255, 190, 75)
        else:
            state_color = (100, 180, 230)
        mode_text = "MODE: MANUAL INSTRUCTION"

    dpg.set_value(MIIL_OPERATOR_MODE_TAG, mode_text)
    dpg.set_value(MIIL_OPERATOR_STATE_TAG, f"STATE: {state_text}")
    dpg.configure_item(MIIL_OPERATOR_STATE_TAG, color=state_color)

    if session.miil.state in (StimulusState.IDLE, StimulusState.STOPPED):
        current_label = "NO ACTIVE INSTRUCTION"
        current_code = 0
        elapsed_s = 0.0
    else:
        current_label = session.miil.current_label.upper()
        current_code = session.miil.current_code
        elapsed_s = session.miil.current_elapsed_s(session.timeline_time_s)
    if current_code == -1:
        current_color = (255, 105, 95)
    elif current_code == 0:
        current_color = (155, 165, 180)
    else:
        current_color = _action_color(current_code)
    dpg.set_value(MIIL_OPERATOR_ACTION_TAG, f"CURRENT: {current_label}")
    dpg.configure_item(MIIL_OPERATOR_ACTION_TAG, color=current_color)
    dpg.set_value(MIIL_OPERATOR_CODE_TAG, f"CODE {current_code}")
    dpg.configure_item(MIIL_OPERATOR_CODE_TAG, color=current_color)
    timer_note = " (FROZEN)" if session.miil.state == StimulusState.PAUSED else ""
    dpg.set_value(
        MIIL_OPERATOR_ELAPSED_TAG,
        f"Elapsed: {elapsed_s:.3f} s{timer_note}",
    )
    _configure_if_exists(
        MIIL_CURRENT_PANEL_TAG,
        height=188 if guided_mode else 100,
    )

    if not guided_mode:
        return
    plan = runner.plan
    if plan is None:
        dpg.set_value(MIIL_GUIDED_POSITION_TAG, "No Guided Sequence plan applied.")
        dpg.set_value(MIIL_GUIDED_NEXT_TAG, "Next: -")
        total_steps = 0
    else:
        total_steps = plan.total_steps
        active = runner.active_step
        if active is None:
            position = _guided_non_active_position(runner.state, plan.repeat_count)
        else:
            attempt_number = _guided_attempt_number(runner, active.flat_index)
            position = (
                f"Group {active.group_number}/{plan.repeat_count} | "
                f"Step {active.step_number}/{len(plan.pattern)} | "
                f"Attempt {attempt_number}"
            )
            if runner.retry_pending:
                position += " | RETRY REQUIRED"
        dpg.set_value(MIIL_GUIDED_POSITION_TAG, position)

        next_step = runner.next_step
        if next_step is None:
            next_text = (
                "Next Enter: finish final action and pause"
                if runner.state == GuidedSequenceState.ACTIVE
                else "Next: sequence complete"
            )
        else:
            action = _miil_action_for_code(session, next_step.code)
            label = f"Code {next_step.code}" if action is None else action.label
            verb = "Retry" if runner.retry_pending else "Next"
            next_text = (
                f"{verb}: Group {next_step.group_number}/{plan.repeat_count}, "
                f"Step {next_step.step_number}/{len(plan.pattern)} - "
                f"{label} [CODE {next_step.code}]"
            )
        dpg.set_value(MIIL_GUIDED_NEXT_TAG, next_text)

    dpg.set_value(MIIL_GUIDED_PROGRESS_TAG, runner.progress_fraction)
    dpg.configure_item(
        MIIL_GUIDED_PROGRESS_TAG,
        overlay=f"{runner.completed_step_count} / {total_steps} completed",
    )
    dpg.set_value(
        MIIL_GUIDED_KEY_HINT_TAG,
        _guided_key_hint(runner.state, runner),
    )


def _guided_non_active_position(
    state: GuidedSequenceState,
    repeat_count: int,
) -> str:
    if state == GuidedSequenceState.WAITING_FIRST:
        return f"Ready at no_stimulus | 0/{repeat_count} group(s) started"
    if state == GuidedSequenceState.BUFFER:
        return "NO-STIMULUS BUFFER | planned position preserved"
    if state == GuidedSequenceState.COMPLETED:
        return "COMPLETE | acquisition paused | Save or Stop"
    if state == GuidedSequenceState.PAUSED:
        return "PAUSED | plan position and timer are frozen"
    if state == GuidedSequenceState.READY:
        return f"PLAN READY | {repeat_count} group(s)"
    return state.value.upper().replace("_", " ")


def _guided_attempt_number(runner, flat_step_index: int) -> int:
    attempt_numbers = [
        attempt.step_attempt_number
        for attempt in runner.attempts
        if attempt.flat_step_index == flat_step_index
    ]
    current = max(attempt_numbers, default=0)
    if runner.retry_pending:
        return current + 1
    return max(1, current)


def _guided_key_hint(state: GuidedSequenceState, runner) -> str:
    if runner.retry_pending and state in (
        GuidedSequenceState.BUFFER,
        GuidedSequenceState.RETRY_PENDING,
    ):
        return "ENTER: retry the dropped planned action"
    if state == GuidedSequenceState.WAITING_FIRST:
        return "ENTER: start the first planned action"
    if state == GuidedSequenceState.ACTIVE:
        active = runner.active_step
        if active is not None and active.flat_index == runner.total_steps - 1:
            return "ENTER: finish the final action and pause acquisition"
        return "ENTER: finish this action and start the next action"
    if state == GuidedSequenceState.BUFFER:
        return "ENTER: leave the buffer and start the next planned action"
    if state == GuidedSequenceState.PAUSED:
        return "PAUSED: Resume to continue; Enter is disabled"
    if state == GuidedSequenceState.COMPLETED:
        return "COMPLETE: save or stop this experiment"
    if state == GuidedSequenceState.READY:
        return "Start acquisition; the first Enter begins step 1"
    return "Enter is disabled in the current state"


def _refresh_schedule(stimulus: StimulusController) -> None:
    global _timed_schedule_signature
    if not dpg.does_item_exist(SCHEDULE_LIST_TAG):
        return
    signature = tuple(
        (event.code, event.label, event.duration_s) for event in stimulus.schedule
    )
    if signature == _timed_schedule_signature:
        return
    dpg.delete_item(SCHEDULE_LIST_TAG, children_only=True)
    for index, event in enumerate(stimulus.schedule, start=1):
        dpg.add_text(
            f"{index:02d} | code {event.code:>3} | {event.duration_s:>7.3f}s | {event.label}",
            parent=SCHEDULE_LIST_TAG,
        )
    _timed_schedule_signature = signature


def _refresh_timed_log(stimulus: StimulusController) -> None:
    global _timed_log_signature
    if not dpg.does_item_exist(LOG_LIST_TAG):
        return
    rows = stimulus.event_log_rows()
    signature = tuple(
        (
            row.get("event_index"),
            row.get("stimulus_code"),
            row.get("label"),
            row.get("start_time_s"),
            row.get("end_time_s"),
            row.get("status"),
        )
        for row in rows
    )
    if signature == _timed_log_signature:
        return
    dpg.delete_item(LOG_LIST_TAG, children_only=True)
    for row in rows:
        start_time = float(row["start_time_s"])
        end_time = row["end_time_s"]
        end_text = "-" if end_time is None else f"{float(end_time):.3f}"
        dpg.add_text(
            f"{int(row['event_index']):02d} | code {int(row['stimulus_code']):>3} "
            f"| {start_time:.3f}-{end_text}s | {row['status']} | {row['label']}",
            parent=LOG_LIST_TAG,
        )
    _timed_log_signature = signature


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


def _configure_guided_editor(enabled: bool) -> None:
    for tag in (
        MIIL_GUIDED_REPEAT_TAG,
        MIIL_GUIDED_APPLY_TAG,
        MIIL_GUIDED_CLEAR_TAG,
    ):
        _configure_if_exists(tag, enabled=enabled)
    for code, _label in _guided_palette_signature:
        _configure_if_exists(_guided_palette_button_tag(code), enabled=enabled)
    for index in range(len(_guided_editor_codes)):
        for field_name in ("up", "down", "remove"):
            tag = _guided_step_tag(index, field_name)
            if not dpg.does_item_exist(tag):
                continue
            item_enabled = enabled
            if field_name == "up":
                item_enabled = enabled and index > 0
            elif field_name == "down":
                item_enabled = enabled and index < len(_guided_editor_codes) - 1
            dpg.configure_item(tag, enabled=item_enabled)


def _run_action(app: FundamentalApp, action) -> None:
    result = action()
    if isinstance(result, list):
        for message in result:
            if message:
                app.log(message)
        return
    if result:
        app.log(result)


def _run_optional_action(app: FundamentalApp | None, action) -> None:
    result = action()
    if app is not None and result:
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
