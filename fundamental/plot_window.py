"""Live signal plot window."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import dearpygui.dearpygui as dpg

from fundamental.app_shell import FundamentalApp
from fundamental.capture_store import CaptureStore
from fundamental.commands import CommandSpec
from fundamental.messages import DEFAULT_PLOT_WINDOW_SECONDS
from fundamental.plot_processing import (
    SCALE_MODE_OPTIONS,
    AxisScaler,
    minmax_downsample,
    process_signal,
    signal_stats,
)
from fundamental.window_manager import ManagedWindow


PLOT_WINDOW_TAG = "fundamental.plot.window"
STATUS_TEXT_TAG = "fundamental.plot.status"
WINDOW_SECONDS_TAG = "fundamental.plot.window_seconds"
DENSITY_TAG = "fundamental.plot.density"
SLOT_CONTROLS_VISIBLE_TAG = "fundamental.plot.slot_controls_visible"
PLOT_COUNT_TAG = "fundamental.plot.count"
PLOT_SETTINGS_TAG = "fundamental.plot.settings"
SLOTS_PARENT_TAG = "fundamental.plot.slots"

PLOT_THEME_TAG = "fundamental.plot.theme"
PLOT_PANEL_THEME_TAG = "fundamental.plot.theme.panel"
PLOT_SLOT_THEME_TAG = "fundamental.plot.theme.slot"

COMPACT_SLOT_HEIGHT = 172
COMFORTABLE_SLOT_HEIGHT = 205
MAX_DISPLAY_POINTS = 1600
MAX_SLOT_COUNT = 16
PLOT_REFRESH_HZ = 25

MUTED_TEXT_COLOR = (145, 158, 176)

DEFAULT_SIGNAL_VIEWS = ["Raw", "Raw", "Raw", "Rectified", "RMS", "Envelope", "Raw", "Raw"]

CHANNEL_COLORS = [
    (230, 159, 0),
    (86, 180, 233),
    (0, 158, 115),
    (240, 228, 66),
    (0, 114, 178),
    (213, 94, 0),
    (204, 121, 167),
    (90, 90, 90),
]


@dataclass
class PlotSlot:
    slot_id: int
    series_id: str
    signal_view: str
    scale_mode: str

    def __post_init__(self) -> None:
        prefix = f"fundamental.plot.slot.{self.slot_id}"
        self.card_tag = f"{prefix}.card"
        self.title_tag = f"{prefix}.title"
        self.summary_tag = f"{prefix}.summary"
        self.channel_tag = f"{prefix}.channel"
        self.view_tag = f"{prefix}.view"
        self.scale_tag = f"{prefix}.scale"
        self.status_tag = f"{prefix}.status"
        self.plot_tag = f"{prefix}.plot"
        self.x_axis_tag = f"{prefix}.x"
        self.y_axis_tag = f"{prefix}.y"
        self.series_tag = f"{prefix}.series"
        self.line_theme_tag = f"{prefix}.line_theme"
        self.scaler = AxisScaler()

    def build(
        self,
        provider: CaptureStore,
        parent: int | str,
        display_index: int,
        show_x_labels: bool,
        controls_visible: bool,
        can_remove: bool,
        slot_height: int,
        plot_height: int,
        on_remove: Callable[[int], None],
    ) -> None:
        with dpg.child_window(parent=parent, tag=self.card_tag, width=-1, height=slot_height, border=True):
            with dpg.group(horizontal=True):
                dpg.add_text(f"Plot {display_index + 1}", tag=self.title_tag, bullet=True)
                dpg.add_spacer(width=8)
                dpg.add_text(_slot_summary(provider, self), tag=self.summary_tag, color=MUTED_TEXT_COLOR)
                if can_remove:
                    dpg.add_spacer(width=8)
                    dpg.add_button(
                        label="Delete",
                        width=64,
                        callback=lambda *_args, slot_id=self.slot_id: on_remove(slot_id),
                    )
                dpg.add_spacer(width=8)
                dpg.add_text("", tag=self.status_tag, color=MUTED_TEXT_COLOR)

            if controls_visible:
                with dpg.group(horizontal=True):
                    dpg.add_text("Series")
                    dpg.add_combo(
                        _series_labels(provider),
                        default_value=_series_label(provider, self.series_id),
                        width=150,
                        tag=self.channel_tag,
                        callback=self._on_channel_changed,
                    )
                    dpg.add_text("View")
                    dpg.add_combo(
                        list(self._view_options(provider)),
                        default_value=self.signal_view,
                        width=120,
                        tag=self.view_tag,
                        callback=self._on_view_changed,
                    )
                    dpg.add_text("Scale")
                    dpg.add_combo(
                        list(SCALE_MODE_OPTIONS),
                        default_value=self.scale_mode,
                        width=130,
                        tag=self.scale_tag,
                        callback=self._on_scale_changed,
                    )

            with dpg.plot(tag=self.plot_tag, width=-1, height=plot_height, anti_aliased=True, no_mouse_pos=True):
                dpg.add_plot_axis(
                    dpg.mvXAxis,
                    label="Time (s)" if show_x_labels else "",
                    tag=self.x_axis_tag,
                    no_tick_labels=not show_x_labels,
                )
                series_spec = provider.series_spec(self.series_id)
                dpg.add_plot_axis(
                    dpg.mvYAxis,
                    label=(series_spec.unit if series_spec is not None else "value"),
                    tag=self.y_axis_tag,
                )
                dpg.add_line_series([], [], parent=self.y_axis_tag, tag=self.series_tag)
                dpg.bind_item_theme(
                    self.series_tag,
                    _line_theme(CHANNEL_COLORS[self.slot_id % len(CHANNEL_COLORS)], self.line_theme_tag),
                )

        dpg.bind_item_theme(self.card_tag, PLOT_SLOT_THEME_TAG)
        self._provider = provider

    def update(self, provider: CaptureStore, window_seconds: float) -> None:
        series_window = provider.get_series_window(self.series_id, window_seconds)
        if series_window is None:
            dpg.set_value(self.series_tag, [[], []])
            dpg.set_value(self.status_tag, _empty_status(provider, self.series_id))
            dpg.set_axis_limits(self.x_axis_tag, -float(window_seconds), 0.0)
            dpg.set_axis_limits(self.y_axis_tag, -1.0, 1.0)
            return

        timestamps = series_window.time_s
        processed = process_signal(
            series_window.values,
            self.signal_view,
            series_window.spec.unit or "value",
        )
        relative_time = [timestamp - timestamps[-1] for timestamp in timestamps]
        display_x, display_y = minmax_downsample(relative_time, processed.values, MAX_DISPLAY_POINTS)

        dpg.set_value(self.series_tag, [display_x, display_y])
        dpg.set_axis_limits(self.x_axis_tag, -float(window_seconds), 0.0)

        low, high, outside = self.scaler.get_limits(
            processed.values,
            self.scale_mode,
            processed.bipolar,
            fixed_range=series_window.spec.fixed_range,
        )
        dpg.set_axis_limits(self.y_axis_tag, low, high)
        dpg.set_item_label(self.y_axis_tag, processed.unit)

        stats = signal_stats(processed.values)
        status = f"Peak {stats.peak:.3f} {processed.unit} | RMS {stats.rms:.3f}"
        if outside:
            status = f"{status} | Axis Out {outside}"
        dpg.set_value(self.status_tag, status)

    def set_series_id(self, provider: CaptureStore, series_id: str) -> None:
        if provider.series_spec(series_id) is None:
            return
        self.series_id = series_id
        options = self._view_options(provider)
        if self.signal_view not in options:
            self.signal_view = options[0]
        self.scaler.reset()
        _configure_if_exists(self.view_tag, items=list(options))
        _set_value_if_exists(self.channel_tag, _series_label(provider, self.series_id))
        _set_value_if_exists(self.view_tag, self.signal_view)
        self._refresh_summary(provider)

    def set_signal_view(self, signal_view: str) -> None:
        provider = getattr(self, "_provider", None)
        if provider is not None and signal_view not in self._view_options(provider):
            return
        self.signal_view = signal_view
        self.scaler.reset()
        _set_value_if_exists(self.view_tag, signal_view)
        if provider is not None:
            self._refresh_summary(provider)

    def set_scale_mode(self, scale_mode: str) -> None:
        self.scale_mode = scale_mode
        self.scaler.reset()
        _set_value_if_exists(self.scale_tag, scale_mode)
        provider = getattr(self, "_provider", None)
        if provider is not None:
            self._refresh_summary(provider)

    def resize(self, slot_height: int, plot_height: int) -> None:
        _configure_if_exists(self.card_tag, height=slot_height)
        _configure_if_exists(self.plot_tag, height=plot_height)

    def _refresh_summary(self, provider: CaptureStore) -> None:
        _set_value_if_exists(self.summary_tag, _slot_summary(provider, self))

    def _on_channel_changed(self, _sender, app_data, _user_data=None) -> None:
        provider = getattr(self, "_provider", None)
        if provider is not None:
            self.set_series_id(provider, _series_id_from_label(provider, str(app_data)))

    def _on_view_changed(self, _sender, app_data, _user_data=None) -> None:
        self.set_signal_view(str(app_data))

    def _on_scale_changed(self, _sender, app_data, _user_data=None) -> None:
        self.set_scale_mode(str(app_data))

    def _view_options(self, provider: CaptureStore) -> tuple[str, ...]:
        series = provider.series_spec(self.series_id)
        return series.view_options if series is not None else ("Raw",)


class PlotWindowState:
    def __init__(self, provider: CaptureStore) -> None:
        self.slots: list[PlotSlot] = []
        self.next_slot_id = 0
        self.last_refresh_time = 0.0
        self.catalog_signature: tuple[str, ...] = ()
        self.reset_defaults(provider)

    def add_slot(self, provider: CaptureStore) -> PlotSlot | None:
        if len(self.slots) >= MAX_SLOT_COUNT:
            return None

        series_specs = provider.series_specs()
        if not series_specs:
            return None
        position = len(self.slots)
        series = series_specs[position % len(series_specs)]
        signal_view = DEFAULT_SIGNAL_VIEWS[position] if position < len(DEFAULT_SIGNAL_VIEWS) else "Raw"
        if signal_view not in series.view_options:
            signal_view = series.view_options[0]
        slot = PlotSlot(
            slot_id=self.next_slot_id,
            series_id=series.series_id,
            signal_view=signal_view,
            scale_mode="Robust Scaling",
        )
        self.next_slot_id += 1
        self.slots.append(slot)
        return slot

    def remove_slot(self, slot_id: int) -> bool:
        if len(self.slots) <= 1:
            return False
        next_slots = [slot for slot in self.slots if slot.slot_id != slot_id]
        if len(next_slots) == len(self.slots):
            return False
        self.slots = next_slots
        return True

    def remove_last_slot(self) -> bool:
        if len(self.slots) <= 1:
            return False
        self.slots.pop()
        return True

    def reset_defaults(self, provider: CaptureStore) -> None:
        self.slots = []
        self.next_slot_id = 0
        series_specs = provider.series_specs()
        defaults = [series for series in series_specs if series.default_plot]
        selected = defaults or list(series_specs[:8])
        for position, series in enumerate(selected[:MAX_SLOT_COUNT]):
            signal_view = DEFAULT_SIGNAL_VIEWS[position] if position < len(DEFAULT_SIGNAL_VIEWS) else "Raw"
            if signal_view not in series.view_options:
                signal_view = series.view_options[0]
            self.slots.append(
                PlotSlot(self.next_slot_id, series.series_id, signal_view, "Robust Scaling")
            )
            self.next_slot_id += 1
        self.catalog_signature = tuple(series.series_id for series in series_specs)
        self.last_refresh_time = 0.0

    def sync_catalog(self, provider: CaptureStore) -> bool:
        signature = tuple(series.series_id for series in provider.series_specs())
        if signature == self.catalog_signature:
            return False
        self.reset_defaults(provider)
        return True

    def refresh(self, provider: CaptureStore, force: bool = False) -> None:
        if not dpg.does_item_exist(PLOT_WINDOW_TAG):
            return

        if self.sync_catalog(provider):
            _rebuild_slot_list(self, provider)
            return

        now = time.monotonic()
        if not force and now - self.last_refresh_time < 1.0 / PLOT_REFRESH_HZ:
            return

        window_seconds = float(dpg.get_value(WINDOW_SECONDS_TAG))
        _refresh_status(provider)
        for slot in self.slots:
            slot.update(provider, window_seconds)
        self.last_refresh_time = now


def register(app: FundamentalApp, buffer: CaptureStore) -> None:
    state = PlotWindowState(buffer)
    app.window_manager.register(
        ManagedWindow(
            tag=PLOT_WINDOW_TAG,
            title="Signal Plot",
            build=lambda: _build_window(app, buffer, state),
        )
    )
    app.register_command(
        CommandSpec(
            name="plot",
            description="Open the live signal plot window.",
            handler=lambda context: _open_window(context.app, buffer, state),
        )
    )
    app.register_frame_callback(lambda frame_app: _on_frame(frame_app, buffer, state))


def _open_window(app: FundamentalApp, buffer: CaptureStore, state: PlotWindowState) -> str | None:
    app.open_window(PLOT_WINDOW_TAG)
    state.refresh(buffer, force=True)
    return None


def _build_window(_app: FundamentalApp, buffer: CaptureStore, state: PlotWindowState) -> None:
    _ensure_plot_themes()

    with dpg.window(
        label="Signal Plot",
        tag=PLOT_WINDOW_TAG,
        show=False,
        width=1120,
        height=780,
        pos=(80, 80),
    ):
        with dpg.group(horizontal=True):
            with dpg.group():
                dpg.add_text("Signal Plot")

            dpg.add_spacer(width=28)
            dpg.add_text("Display Window")
            dpg.add_slider_float(
                tag=WINDOW_SECONDS_TAG,
                min_value=1.0,
                max_value=30.0,
                default_value=DEFAULT_PLOT_WINDOW_SECONDS,
                format="%.1f s",
                width=180,
                callback=lambda *_: state.refresh(buffer, force=True),
            )
            dpg.add_spacer(width=18)
            dpg.add_text("", tag=STATUS_TEXT_TAG, color=MUTED_TEXT_COLOR)

        dpg.add_separator()
        with dpg.group(horizontal=True):
            with dpg.child_window(width=235, height=-1, border=True, tag=PLOT_SETTINGS_TAG):
                dpg.add_text("Display Settings")
                dpg.add_separator()
                dpg.add_text("Layout Density")
                dpg.add_radio_button(
                    ["Compact", "Comfortable"],
                    default_value="Comfortable",
                    tag=DENSITY_TAG,
                    callback=lambda _sender, app_data, _user_data=None: _change_density(
                        state, str(app_data)
                    ),
                )

                dpg.add_spacer(height=8)
                dpg.add_text("Plot Slots")
                dpg.add_text("", tag=PLOT_COUNT_TAG, color=MUTED_TEXT_COLOR)
                with dpg.group(horizontal=True):
                    dpg.add_button(
                        label="Add Slot",
                        width=106,
                        callback=lambda *_: _add_slot(state, buffer),
                    )
                    dpg.add_button(
                        label="Delete Last",
                        width=106,
                        callback=lambda *_: _remove_last_slot(state, buffer),
                    )
                dpg.add_checkbox(
                    label="Show Slot Controls",
                    default_value=True,
                    tag=SLOT_CONTROLS_VISIBLE_TAG,
                    callback=lambda *_: _rebuild_slot_list(state, buffer),
                )

                dpg.add_spacer(height=8)
                dpg.add_text("Global Operations")
                dpg.add_button(
                    label="Set All Robust",
                    width=-1,
                    callback=lambda *_: _set_all_scale(state, "Robust Scaling"),
                )
                dpg.add_button(
                    label="Show All Raw",
                    width=-1,
                    callback=lambda *_: _set_all_view(state, "Raw"),
                )
                dpg.add_button(
                    label="Restore Defaults",
                    width=-1,
                    callback=lambda *_: _restore_defaults(state, buffer),
                )

            with dpg.child_window(width=-1, height=-1, border=False, tag=SLOTS_PARENT_TAG):
                pass

    dpg.bind_item_theme(PLOT_WINDOW_TAG, PLOT_THEME_TAG)
    dpg.bind_item_theme(PLOT_SETTINGS_TAG, PLOT_PANEL_THEME_TAG)
    _rebuild_slot_list(state, buffer)


def _on_frame(_app: FundamentalApp, buffer: CaptureStore, state: PlotWindowState) -> None:
    state.refresh(buffer)


def _refresh_status(buffer: CaptureStore) -> None:
    if not dpg.does_item_exist(STATUS_TEXT_TAG):
        return

    latest_values = buffer.latest_series_values(limit=4)
    latest = "  ".join(f"{series.label}: {value:.3f}" for series, value in latest_values)
    series_count = len(buffer.series_specs())
    dpg.set_value(
        STATUS_TEXT_TAG,
        f"Rows {buffer.row_count} | Streams {buffer.stream_count or '-'} | "
        f"Series {series_count or '-'} | Latest {latest or '-'}",
    )


def _rebuild_slot_list(state: PlotWindowState, buffer: CaptureStore) -> None:
    if not dpg.does_item_exist(SLOTS_PARENT_TAG):
        return

    dpg.delete_item(SLOTS_PARENT_TAG, children_only=True)
    controls_visible = _slot_controls_visible()
    slot_height, plot_height = _slot_dimensions(_density_value(), controls_visible)

    for index, slot in enumerate(state.slots):
        slot.build(
            provider=buffer,
            parent=SLOTS_PARENT_TAG,
            display_index=index,
            show_x_labels=index == len(state.slots) - 1,
            controls_visible=controls_visible,
            can_remove=len(state.slots) > 1,
            slot_height=slot_height,
            plot_height=plot_height,
            on_remove=lambda slot_id: _remove_slot(state, buffer, slot_id),
        )
        dpg.add_spacer(height=6, parent=SLOTS_PARENT_TAG)

    _update_plot_count(state)
    state.refresh(buffer, force=True)


def _add_slot(state: PlotWindowState, buffer: CaptureStore) -> None:
    if state.add_slot(buffer) is None:
        _update_plot_count(state, "Maximum reached")
        return
    _rebuild_slot_list(state, buffer)


def _remove_slot(state: PlotWindowState, buffer: CaptureStore, slot_id: int) -> None:
    if not state.remove_slot(slot_id):
        _update_plot_count(state, "Keep at least one")
        return
    _rebuild_slot_list(state, buffer)


def _remove_last_slot(state: PlotWindowState, buffer: CaptureStore) -> None:
    if not state.remove_last_slot():
        _update_plot_count(state, "Keep at least one")
        return
    _rebuild_slot_list(state, buffer)


def _update_plot_count(state: PlotWindowState, note: str = "") -> None:
    if not dpg.does_item_exist(PLOT_COUNT_TAG):
        return
    suffix = f" | {note}" if note else ""
    dpg.set_value(PLOT_COUNT_TAG, f"{len(state.slots)} / {MAX_SLOT_COUNT} slots{suffix}")


def _change_density(state: PlotWindowState, density: str) -> None:
    slot_height, plot_height = _slot_dimensions(density, _slot_controls_visible())
    for slot in state.slots:
        slot.resize(slot_height, plot_height)


def _set_all_scale(state: PlotWindowState, scale_mode: str) -> None:
    for slot in state.slots:
        slot.set_scale_mode(scale_mode)


def _set_all_view(state: PlotWindowState, signal_view: str) -> None:
    for slot in state.slots:
        slot.set_signal_view(signal_view)


def _restore_defaults(state: PlotWindowState, buffer: CaptureStore) -> None:
    state.reset_defaults(buffer)
    _set_value_if_exists(WINDOW_SECONDS_TAG, DEFAULT_PLOT_WINDOW_SECONDS)
    _set_value_if_exists(DENSITY_TAG, "Comfortable")
    _set_value_if_exists(SLOT_CONTROLS_VISIBLE_TAG, True)
    _rebuild_slot_list(state, buffer)


def _empty_status(buffer: CaptureStore, series_id: str) -> str:
    if buffer.row_count == 0:
        return "Waiting for data"
    if buffer.series_spec(series_id) is None:
        return "Series unavailable"
    if buffer.get_series_window(series_id, DEFAULT_PLOT_WINDOW_SECONDS) is None:
        return "Series inactive"
    return "Waiting for window"


def _slot_summary(provider: CaptureStore, slot: PlotSlot) -> str:
    return f"{_series_label(provider, slot.series_id)} | {slot.signal_view} | {slot.scale_mode}"


def _slot_controls_visible() -> bool:
    if not dpg.does_item_exist(SLOT_CONTROLS_VISIBLE_TAG):
        return True
    return bool(dpg.get_value(SLOT_CONTROLS_VISIBLE_TAG))


def _density_value() -> str:
    if not dpg.does_item_exist(DENSITY_TAG):
        return "Comfortable"
    return str(dpg.get_value(DENSITY_TAG))


def _slot_dimensions(density: str, controls_visible: bool) -> tuple[int, int]:
    slot_height = COMPACT_SLOT_HEIGHT if density == "Compact" else COMFORTABLE_SLOT_HEIGHT
    reserved_height = 60 if controls_visible else 34
    return slot_height, max(110, slot_height - reserved_height)


def _series_label_map(provider: CaptureStore) -> dict[str, str]:
    specs = provider.series_specs()
    label_counts: dict[str, int] = {}
    for spec in specs:
        label_counts[spec.label] = label_counts.get(spec.label, 0) + 1
    return {
        spec.series_id: (
            spec.label
            if label_counts[spec.label] == 1
            else f"{spec.label} · {spec.stream_id}"
        )
        for spec in specs
    }


def _series_labels(provider: CaptureStore) -> list[str]:
    return list(_series_label_map(provider).values())


def _series_label(provider: CaptureStore, series_id: str) -> str:
    return _series_label_map(provider).get(series_id, series_id)


def _series_id_from_label(provider: CaptureStore, label: str) -> str:
    labels = _series_label_map(provider)
    return next(
        (series_id for series_id, display_label in labels.items() if display_label == label),
        next(iter(labels), ""),
    )


def _configure_if_exists(tag: str, **kwargs) -> None:
    if dpg.does_item_exist(tag):
        dpg.configure_item(tag, **kwargs)


def _set_value_if_exists(tag: str, value) -> None:
    if dpg.does_item_exist(tag):
        dpg.set_value(tag, value)


def _ensure_plot_themes() -> None:
    if dpg.does_item_exist(PLOT_THEME_TAG):
        return

    with dpg.theme(tag=PLOT_THEME_TAG):
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 12, 12)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 8, 5)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 7)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 5)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 8)
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (18, 21, 28, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (24, 28, 36, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (36, 42, 53, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (48, 57, 71, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Header, (42, 91, 115, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Button, (40, 89, 112, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (48, 107, 133, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (226, 232, 240, 255))

    with dpg.theme(tag=PLOT_PANEL_THEME_TAG):
        with dpg.theme_component(dpg.mvChildWindow):
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 8)
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (24, 28, 36, 255))

    with dpg.theme(tag=PLOT_SLOT_THEME_TAG):
        with dpg.theme_component(dpg.mvChildWindow):
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 7)
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (24, 28, 36, 255))


def _line_theme(color: tuple[int, int, int], tag: str) -> str:
    if dpg.does_item_exist(tag):
        return tag

    with dpg.theme(tag=tag):
        with dpg.theme_component(dpg.mvLineSeries):
            dpg.add_theme_color(dpg.mvPlotCol_Line, color, category=dpg.mvThemeCat_Plots)
    return tag
