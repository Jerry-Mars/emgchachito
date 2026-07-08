"""Acquisition control window and commands."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from fundamental.acquisition import AcquisitionController
from fundamental.app_shell import FundamentalApp
from fundamental.commands import CommandSpec
from fundamental.messages import AcquisitionState
from fundamental.window_manager import ManagedWindow


ACQUISITION_WINDOW_TAG = "fundamental.acquisition.window"
STATUS_TEXT_TAG = "fundamental.acquisition.status"
CONFIG_TEXT_TAG = "fundamental.acquisition.config"
SAVE_PATH_INPUT_TAG = "fundamental.acquisition.save_path"
START_BUTTON_TAG = "fundamental.acquisition.start"
PAUSE_BUTTON_TAG = "fundamental.acquisition.pause"
STOP_BUTTON_TAG = "fundamental.acquisition.stop"
SAVE_BUTTON_TAG = "fundamental.acquisition.save"


def register(app: FundamentalApp, controller: AcquisitionController) -> None:
    app.window_manager.register(
        ManagedWindow(
            tag=ACQUISITION_WINDOW_TAG,
            title="Acquisition",
            build=lambda: _build_window(app, controller),
        )
    )
    app.register_command(
        CommandSpec(
            name="acquisition",
            description="Open acquisition controls.",
            handler=lambda context: _open_window(context.app, controller),
            aliases=("record",),
        )
    )
    app.register_frame_callback(lambda frame_app: _on_frame(frame_app, controller))
    app.register_shutdown_callback(lambda _frame_app: controller.shutdown())


def _open_window(app: FundamentalApp, controller: AcquisitionController) -> str | None:
    app.open_window(ACQUISITION_WINDOW_TAG)
    _sync_save_path(controller, force=True)
    _refresh_status(controller)
    return None


def _build_window(app: FundamentalApp, controller: AcquisitionController) -> None:
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
                callback=lambda *_: _run_action(app, lambda: _start(controller)),
            )
            dpg.add_button(
                label="Pause",
                tag=PAUSE_BUTTON_TAG,
                width=90,
                callback=lambda *_: _run_action(app, lambda: _pause(controller)),
            )
            dpg.add_button(
                label="Stop",
                tag=STOP_BUTTON_TAG,
                width=90,
                callback=lambda *_: _run_action(app, lambda: _stop(controller)),
            )
            dpg.add_button(
                label="Save",
                tag=SAVE_BUTTON_TAG,
                width=90,
                callback=lambda *_: _run_action(app, lambda: _save(controller)),
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


def _on_frame(app: FundamentalApp, controller: AcquisitionController) -> None:
    controller.drain_queues(app.log)
    if dpg.does_item_exist(ACQUISITION_WINDOW_TAG):
        _refresh_status(controller)


def _start(controller: AcquisitionController) -> str:
    result = controller.start()
    _sync_save_path(controller, force=True)
    _refresh_status(controller)
    return result


def _pause(controller: AcquisitionController) -> str:
    result = controller.pause()
    _refresh_status(controller)
    return result


def _stop(controller: AcquisitionController) -> str:
    result = controller.stop()
    _refresh_status(controller)
    return result


def _save(controller: AcquisitionController) -> str:
    path = _save_path_from_window(controller)
    result = controller.save(path)
    _sync_save_path(controller, force=True)
    _refresh_status(controller)
    return result


def _run_action(app: FundamentalApp, action) -> None:
    result = action()
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
    dpg.set_value(CONFIG_TEXT_TAG, f"Serial: {controller.config.display_text()}")
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
