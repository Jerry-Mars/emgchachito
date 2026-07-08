"""Live raw serial plot window."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from fundamental.acquisition import AcquisitionController
from fundamental.app_shell import FundamentalApp
from fundamental.commands import CommandSpec
from fundamental.messages import (
    CHANNEL_COUNT,
    DEFAULT_PLOT_WINDOW_SECONDS,
)
from fundamental.window_manager import ManagedWindow


PLOT_WINDOW_TAG = "fundamental.plot.window"
STATUS_TEXT_TAG = "fundamental.plot.status"
LATEST_TEXT_TAG = "fundamental.plot.latest"
WINDOW_SECONDS_TAG = "fundamental.plot.window_seconds"
X_AXIS_TAG = "fundamental.plot.x_axis"
Y_AXIS_TAG = "fundamental.plot.y_axis"

CHANNEL_COLORS = [
    (231, 76, 60),
    (52, 152, 219),
    (46, 204, 113),
    (241, 196, 15),
    (155, 89, 182),
    (230, 126, 34),
    (26, 188, 156),
    (149, 165, 166),
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


def _open_window(app: FundamentalApp, controller: AcquisitionController) -> str | None:
    app.open_window(PLOT_WINDOW_TAG)
    _refresh_status(controller)
    _refresh_plot(controller)
    return None


def _build_window(_app: FundamentalApp, controller: AcquisitionController) -> None:
    with dpg.window(
        label="Serial Plot",
        tag=PLOT_WINDOW_TAG,
        show=False,
        width=980,
        height=640,
        pos=(80, 80),
    ):
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


def _on_frame(_app: FundamentalApp, controller: AcquisitionController) -> None:
    if not dpg.does_item_exist(PLOT_WINDOW_TAG):
        return

    _refresh_status(controller)
    _refresh_plot(controller)


def _refresh_status(controller: AcquisitionController) -> None:
    if not dpg.does_item_exist(PLOT_WINDOW_TAG):
        return

    state = controller.state.value.upper()
    active_channel_count = controller.buffer.active_channel_count
    dpg.set_value(
        STATUS_TEXT_TAG,
        f"State: {state} | Samples: {controller.buffer.frame_count} | Channels: {active_channel_count or '-'}",
    )
    latest_values = controller.buffer.latest_values[:active_channel_count]
    latest = "  ".join(
        f"CH{index + 1}: {value:.1f}" for index, value in enumerate(latest_values)
    ) or "-"
    dpg.set_value(LATEST_TEXT_TAG, f"Latest: {latest}")


def _refresh_plot(controller: AcquisitionController) -> None:
    if not dpg.does_item_exist(PLOT_WINDOW_TAG):
        return

    window_seconds = float(dpg.get_value(WINDOW_SECONDS_TAG))
    active_channel_count = controller.buffer.active_channel_count
    window = controller.buffer.get_plot_window(window_seconds)
    if window is None:
        for channel_index in range(CHANNEL_COUNT):
            series_tag = _series_tag(channel_index)
            dpg.set_value(series_tag, [[], []])
            _configure_if_exists(series_tag, show=channel_index < active_channel_count)
        dpg.set_axis_limits(X_AXIS_TAG, 0.0, window_seconds)
        dpg.set_axis_limits(Y_AXIS_TAG, -1.0, 1.0)
        return

    x_min, x_max, window_x, channel_windows, y_min, y_max = window
    for channel_index in range(CHANNEL_COUNT):
        series_tag = _series_tag(channel_index)
        if channel_index < len(channel_windows):
            dpg.set_value(series_tag, [window_x, channel_windows[channel_index]])
            _configure_if_exists(series_tag, show=True)
        else:
            dpg.set_value(series_tag, [[], []])
            _configure_if_exists(series_tag, show=False)

    y_span = y_max - y_min
    padding = max(1.0, y_span * 0.05)
    dpg.set_axis_limits(X_AXIS_TAG, x_min, max(x_max, x_min + 0.1))
    dpg.set_axis_limits(Y_AXIS_TAG, y_min - padding, y_max + padding)


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
