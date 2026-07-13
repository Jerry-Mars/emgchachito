"""Acquisition control window and commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import dearpygui.dearpygui as dpg

from fundamental.app_shell import FundamentalApp
from fundamental.commands import CommandSpec
from fundamental.messages import AcquisitionState
from fundamental.recording_session import RecordingSession
from fundamental.window_manager import ManagedWindow

if TYPE_CHECKING:
    from fundamental.acquisition import AcquisitionController


ACQUISITION_WINDOW_TAG = "fundamental.acquisition.window"
STATUS_TEXT_TAG = "fundamental.acquisition.status"
CONFIG_TEXT_TAG = "fundamental.acquisition.config"
SAVE_PATH_INPUT_TAG = "fundamental.acquisition.save_path"
START_BUTTON_TAG = "fundamental.acquisition.start"
PAUSE_BUTTON_TAG = "fundamental.acquisition.pause"
STOP_BUTTON_TAG = "fundamental.acquisition.stop"
SAVE_BUTTON_TAG = "fundamental.acquisition.save"


def register(app: FundamentalApp, session: RecordingSession) -> None:
    controller = session.acquisition
    app.window_manager.register(
        ManagedWindow(
            tag=ACQUISITION_WINDOW_TAG,
            title="Acquisition",
            build=lambda: _build_window(app, session),
        )
    )
    app.register_command(
        CommandSpec(
            name="acquisition",
            description="Open acquisition controls.",
            handler=lambda context: _open_window(context.app, session),
            aliases=("record",),
        )
    )
    app.register_frame_callback(lambda frame_app: _on_frame(frame_app, session))
    app.register_shutdown_callback(lambda _frame_app: controller.shutdown())


def _open_window(app: FundamentalApp, session: RecordingSession) -> str | None:
    controller = session.acquisition
    app.open_window(ACQUISITION_WINDOW_TAG)
    _sync_save_path(controller, force=True)
    _refresh_status(controller)
    return None


def _build_window(app: FundamentalApp, session: RecordingSession) -> None:
    controller = session.acquisition
    with dpg.window(
        label="Acquisition",
        tag=ACQUISITION_WINDOW_TAG,
        show=False,
        width=620,
        height=220,
        pos=(80, 80),
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

        dpg.add_spacer(height=8)
        dpg.add_input_text(
            tag=SAVE_PATH_INPUT_TAG,
            label="Save Path",
            default_value=controller.last_save_path,
            width=520,
        )
        dpg.add_spacer(height=8)
        dpg.add_text("", tag=STATUS_TEXT_TAG)
        dpg.add_text("", tag=CONFIG_TEXT_TAG)

    _refresh_status(controller)


def _on_frame(_app: FundamentalApp, session: RecordingSession) -> None:
    controller = session.acquisition
    if dpg.does_item_exist(ACQUISITION_WINDOW_TAG):
        _refresh_status(controller)


def _start(session: RecordingSession) -> str:
    result = session.start_acquisition()
    _sync_save_path(session.acquisition, force=True)
    _refresh_status(session.acquisition)
    return result


def _pause(session: RecordingSession) -> list[str]:
    result = session.pause()
    _refresh_status(session.acquisition)
    return result


def _stop(session: RecordingSession) -> list[str]:
    result = session.stop()
    _refresh_status(session.acquisition)
    return result


def _save(session: RecordingSession) -> str:
    controller = session.acquisition
    path = _save_path_from_window(controller)
    result = session.save(path)
    _sync_save_path(controller, force=True)
    _refresh_status(controller)
    return result


def _run_action(app: FundamentalApp, action) -> None:
    result = action()
    if isinstance(result, list):
        for message in result:
            if message:
                app.log(message)
        return
    if result:
        app.log(result)


def _refresh_status(controller: AcquisitionController) -> None:
    if not dpg.does_item_exist(ACQUISITION_WINDOW_TAG):
        return

    state = controller.state.value.upper()
    dpg.set_value(
        STATUS_TEXT_TAG,
        f"State: {state} | Samples: {controller.buffer.frame_count}",
    )
    dpg.set_value(CONFIG_TEXT_TAG, f"Source: {controller.source_display_text()}")
    _sync_save_path(controller)

    running = controller.state == AcquisitionState.RUNNING
    _configure_if_exists(START_BUTTON_TAG, enabled=not running)
    _configure_if_exists(PAUSE_BUTTON_TAG, enabled=running)
    _configure_if_exists(STOP_BUTTON_TAG, enabled=controller.state != AcquisitionState.STOPPED)
    _configure_if_exists(SAVE_BUTTON_TAG, enabled=not running and controller.buffer.frame_count > 0)


def _save_path_from_window(controller: AcquisitionController) -> str:
    if dpg.does_item_exist(SAVE_PATH_INPUT_TAG):
        value = str(dpg.get_value(SAVE_PATH_INPUT_TAG)).strip()
        if value:
            return value
    return controller.last_save_path


def _sync_save_path(controller: AcquisitionController, force: bool = False) -> None:
    if not dpg.does_item_exist(SAVE_PATH_INPUT_TAG):
        return
    current_value = str(dpg.get_value(SAVE_PATH_INPUT_TAG)).strip()
    if current_value and not force:
        return
    dpg.set_value(SAVE_PATH_INPUT_TAG, controller.last_save_path)


def _configure_if_exists(tag: str, **kwargs) -> None:
    if dpg.does_item_exist(tag):
        dpg.configure_item(tag, **kwargs)
