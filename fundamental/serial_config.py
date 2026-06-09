"""Serial configuration window and commands."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from fundamental.acquisition import AcquisitionController
from fundamental.app_shell import FundamentalApp
from fundamental.commands import CommandContext, CommandSpec
from fundamental.window_manager import ManagedWindow


SERIAL_CONFIG_WINDOW_TAG = "fundamental.serial_config.window"
PORT_INPUT_TAG = "fundamental.serial_config.port"
BAUD_INPUT_TAG = "fundamental.serial_config.baud"
TIMEOUT_INPUT_TAG = "fundamental.serial_config.timeout"
SUMMARY_TEXT_TAG = "fundamental.serial_config.summary"


def register(app: FundamentalApp, controller: AcquisitionController) -> None:
    app.window_manager.register(
        ManagedWindow(
            tag=SERIAL_CONFIG_WINDOW_TAG,
            title="Serial Config",
            build=lambda: _build_window(app, controller),
        )
    )
    app.register_command(
        CommandSpec(
            name="serial",
            description="Open the serial configuration window.",
            handler=lambda context: _open_window(context, controller),
        )
    )


def _open_window(context: CommandContext, controller: AcquisitionController) -> str | None:
    context.open_window(SERIAL_CONFIG_WINDOW_TAG)
    _sync_window(controller)
    return None


def _build_window(app: FundamentalApp, controller: AcquisitionController) -> None:
    with dpg.window(
        label="Serial Config",
        tag=SERIAL_CONFIG_WINDOW_TAG,
        show=False,
        width=420,
        height=210,
        pos=(120, 120),
    ):
        dpg.add_input_text(tag=PORT_INPUT_TAG, label="Port", width=220)
        dpg.add_input_int(tag=BAUD_INPUT_TAG, label="Baud", width=220, min_value=1, min_clamped=True)
        dpg.add_input_float(tag=TIMEOUT_INPUT_TAG, label="Timeout (s)", width=220, step=0.01)
        dpg.add_spacer(height=8)
        dpg.add_button(
            label="Apply",
            width=120,
            callback=lambda *_: _apply_from_window(app, controller),
        )
        dpg.add_spacer(height=8)
        dpg.add_text("", tag=SUMMARY_TEXT_TAG)

    _sync_window(controller)


def _apply_from_window(app: FundamentalApp, controller: AcquisitionController) -> None:
    port = str(dpg.get_value(PORT_INPUT_TAG)).strip()
    baud_rate = int(dpg.get_value(BAUD_INPUT_TAG))
    timeout_s = float(dpg.get_value(TIMEOUT_INPUT_TAG))
    error = controller.update_config(port=port, baud_rate=baud_rate, timeout_s=timeout_s)
    if error:
        app.log(error)
    else:
        app.log(f"Serial config updated: {controller.config.display_text()}.")
    _sync_window(controller)


def _sync_window(controller: AcquisitionController) -> None:
    if not dpg.does_item_exist(SERIAL_CONFIG_WINDOW_TAG):
        return
    dpg.set_value(PORT_INPUT_TAG, controller.config.port)
    dpg.set_value(BAUD_INPUT_TAG, controller.config.baud_rate)
    dpg.set_value(TIMEOUT_INPUT_TAG, controller.config.timeout_s)
    dpg.set_value(SUMMARY_TEXT_TAG, f"Current: {controller.config.display_text()}")
