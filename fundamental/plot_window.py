"""Live raw serial plot window and acquisition commands."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from fundamental.acquisition import AcquisitionController
from fundamental.app_shell import FundamentalApp
from fundamental.commands import CommandSpec
from fundamental.messages import (
    CHANNEL_COUNT,
    DEFAULT_PLOT_WINDOW_SECONDS,
    AcquisitionState,
)
from fundamental.window_manager import ManagedWindow


PLOT_WINDOW_TAG = "fundamental.plot.window"
STATUS_TEXT_TAG = "fundamental.plot.status"
CONFIG_TEXT_TAG = "fundamental.plot.config"
LATEST_TEXT_TAG = "fundamental.plot.latest"
SAVE_PATH_INPUT_TAG = "fundamental.plot.save_path"
WINDOW_SECONDS_TAG = "fundamental.plot.window_seconds"
X_AXIS_TAG = "fundamental.plot.x_axis"
Y_AXIS_TAG = "fundamental.plot.y_axis"
START_BUTTON_TAG = "fundamental.plot.start"
PAUSE_BUTTON_TAG = "fundamental.plot.pause"
STOP_BUTTON_TAG = "fundamental.plot.stop"
SAVE_BUTTON_TAG = "fundamental.plot.save"

CHANNEL_COLORS = [
    (231, 76, 60),
    (52, 152, 219),
    (46, 204, 113),
    (241, 196, 15),
    (155, 89, 182),
    (230, 126, 34),
]


def register(app: FundamentalApp, controller: AcquisitionController) -> None:
    app.window_manager.register(
        ManagedWindow(
            tag=PLOT_WINDOW_TAG,
            title="Serial Plot",
            build=lambda: _build_window(app, controller),
        )
    )
    app.register_command(
        CommandSpec(
            name="plot",
            description="Open the live serial plot window.",
            handler=lambda context: _open_window(context.app, controller),
        )
    )
    app.register_frame_callback(lambda frame_app: _on_frame(frame_app, controller))
    app.register_shutdown_callback(lambda _frame_app: controller.shutdown())


def _open_window(app: FundamentalApp, controller: AcquisitionController) -> str | None:
    app.open_window(PLOT_WINDOW_TAG)
    _refresh_status(controller)
    _refresh_plot(controller)
    return None


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


def _build_window(app: FundamentalApp, controller: AcquisitionController) -> None:
    with dpg.window(
        label="Serial Plot",
        tag=PLOT_WINDOW_TAG,
        show=False,
        width=980,
        height=640,
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
        dpg.add_slider_float(
            tag=WINDOW_SECONDS_TAG,
            label="Window (s)",
            min_value=1.0,
            max_value=30.0,
            default_value=DEFAULT_PLOT_WINDOW_SECONDS,
            width=260,
            callback=lambda *_: _refresh_plot(controller),
        )
        dpg.add_spacer(height=8)
        dpg.add_text("", tag=STATUS_TEXT_TAG)
        dpg.add_text("", tag=CONFIG_TEXT_TAG)
        dpg.add_text("", tag=LATEST_TEXT_TAG)
        dpg.add_spacer(height=10)

        with dpg.plot(label="Raw Serial Data", height=420, width=-1):
            dpg.add_plot_legend()
            dpg.add_plot_axis(dpg.mvXAxis, label="Time (s)", tag=X_AXIS_TAG)
            dpg.add_plot_axis(dpg.mvYAxis, label="Amplitude", tag=Y_AXIS_TAG)
            for channel_index in range(CHANNEL_COUNT):
                series_tag = _series_tag(channel_index)
                dpg.add_line_series(
                    [],
                    [],
                    label=f"CH{channel_index + 1}",
                    parent=Y_AXIS_TAG,
                    tag=series_tag,
                )
                dpg.bind_item_theme(series_tag, _line_theme(CHANNEL_COLORS[channel_index]))

    _refresh_status(controller)


def _on_frame(app: FundamentalApp, controller: AcquisitionController) -> None:
    appended = controller.drain_queues(app.log)
    if not dpg.does_item_exist(PLOT_WINDOW_TAG):
        return

    _refresh_status(controller)
    if appended:
        _refresh_plot(controller)


def _run_action(app: FundamentalApp, action) -> None:
    result = action()
    if result:
        app.log(result)


def _refresh_status(controller: AcquisitionController) -> None:
    if not dpg.does_item_exist(PLOT_WINDOW_TAG):
        return

    state = controller.state.value.upper()
    dpg.set_value(
        STATUS_TEXT_TAG,
        f"State: {state} | Samples: {controller.buffer.frame_count}",
    )
    dpg.set_value(CONFIG_TEXT_TAG, f"Serial: {controller.config.display_text()}")
    latest = "  ".join(
        f"CH{index + 1}: {value:.1f}" for index, value in enumerate(controller.buffer.latest_values)
    )
    dpg.set_value(LATEST_TEXT_TAG, f"Latest: {latest}")
    _sync_save_path(controller)

    running = controller.state == AcquisitionState.RUNNING
    _configure_if_exists(START_BUTTON_TAG, enabled=not running)
    _configure_if_exists(PAUSE_BUTTON_TAG, enabled=running)
    _configure_if_exists(STOP_BUTTON_TAG, enabled=controller.state != AcquisitionState.STOPPED)
    _configure_if_exists(SAVE_BUTTON_TAG, enabled=not running and controller.buffer.frame_count > 0)


def _refresh_plot(controller: AcquisitionController) -> None:
    if not dpg.does_item_exist(PLOT_WINDOW_TAG):
        return

    window_seconds = float(dpg.get_value(WINDOW_SECONDS_TAG))
    window = controller.buffer.get_plot_window(window_seconds)
    if window is None:
        for channel_index in range(CHANNEL_COUNT):
            dpg.set_value(_series_tag(channel_index), [[], []])
        dpg.set_axis_limits(X_AXIS_TAG, 0.0, window_seconds)
        dpg.set_axis_limits(Y_AXIS_TAG, -1.0, 1.0)
        return

    x_min, x_max, window_x, channel_windows, y_min, y_max = window
    for channel_index, window_y in enumerate(channel_windows):
        dpg.set_value(_series_tag(channel_index), [window_x, window_y])

    y_span = y_max - y_min
    padding = max(1.0, y_span * 0.05)
    dpg.set_axis_limits(X_AXIS_TAG, x_min, max(x_max, x_min + 0.1))
    dpg.set_axis_limits(Y_AXIS_TAG, y_min - padding, y_max + padding)


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


def _series_tag(channel_index: int) -> str:
    return f"fundamental.plot.series.ch{channel_index + 1}"


def _line_theme(color: tuple[int, int, int]) -> int:
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvLineSeries):
            dpg.add_theme_color(dpg.mvPlotCol_Line, color, category=dpg.mvThemeCat_Plots)
    return theme
