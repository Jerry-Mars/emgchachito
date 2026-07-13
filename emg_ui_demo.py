from __future__ import annotations

import math
import time
from dataclasses import dataclass

import dearpygui.dearpygui as dpg
import numpy as np

# since Chinese can not show nomally in DearPyGUI, we need to replace them with English

# ============================================================
# 配置
# ============================================================

CHANNEL_COUNT = 6
SAMPLE_RATE = 1000
BUFFER_SECONDS = 20
MAX_BUFFER_POINTS = SAMPLE_RATE * BUFFER_SECONDS

WINDOW_WIDTH = 1380
WINDOW_HEIGHT = 920

SOURCE_OPTIONS = ["Raw EMG", "Full-Wave Rectified", "RMS", "Envelope"]
SCALE_OPTIONS = ["Robust Scaling", "Full Range", "Fixed Range"]


# ============================================================
# 简单数据缓存
# ============================================================

class SignalBuffer:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.t = np.empty(0, dtype=np.float64)
        self.y = np.empty(0, dtype=np.float64)

    def append(self, t: np.ndarray, y: np.ndarray) -> None:
        self.t = np.concatenate((self.t, t))
        self.y = np.concatenate((self.y, y))

        if self.t.size > self.capacity:
            self.t = self.t[-self.capacity :]
            self.y = self.y[-self.capacity :]

    def recent(self, seconds: float) -> tuple[np.ndarray, np.ndarray]:
        if self.t.size == 0:
            return self.t, self.y

        start_time = self.t[-1] - seconds
        index = np.searchsorted(self.t, start_time, side="left")
        return self.t[index:], self.y[index:]


# ============================================================
# 信号处理，仅用于 UI 演示
# ============================================================

def moving_average(y: np.ndarray, window: int) -> np.ndarray:
    if y.size == 0:
        return y
    window = max(1, min(window, y.size))
    kernel = np.ones(window, dtype=np.float64) / window
    return np.convolve(y, kernel, mode="same")


def moving_rms(y: np.ndarray, window: int = 50) -> np.ndarray:
    return np.sqrt(moving_average(y * y, window))


def process_signal(y: np.ndarray, source_type: str) -> tuple[np.ndarray, bool, str]:
    if source_type == "Raw EMG":
        return y, True, "mV"

    if source_type == "Full-Wave Rectified":
        return np.abs(y), False, "mV"

    if source_type == "RMS":
        return moving_rms(y, 50), False, "mV RMS"

    if source_type == "Envelope":
        rectified = np.abs(y)
        return moving_average(rectified, 100), False, "mV"

    return y, True, "mV"


def minmax_downsample(
    x: np.ndarray,
    y: np.ndarray,
    max_points: int = 1600,
) -> tuple[np.ndarray, np.ndarray]:
    if x.size <= max_points:
        return x, y

    bucket_count = max_points // 2
    edges = np.linspace(0, x.size, bucket_count + 1, dtype=np.int64)
    selected: list[int] = []

    for start, end in zip(edges[:-1], edges[1:]):
        if end <= start:
            continue

        segment = y[start:end]
        i_min = start + int(np.argmin(segment))
        i_max = start + int(np.argmax(segment))
        selected.extend(sorted((i_min, i_max)))

    idx = np.asarray(selected, dtype=np.int64)
    return x[idx], y[idx]


# ============================================================
# 坐标轴缩放
# ============================================================

class AxisScaler:
    def __init__(self) -> None:
        self.current: tuple[float, float] | None = None

    def reset(self) -> None:
        self.current = None

    def get_limits(
        self,
        y: np.ndarray,
        scale_mode: str,
        bipolar: bool,
    ) -> tuple[float, float, int]:
        finite = y[np.isfinite(y)]

        if finite.size == 0:
            return -1.0, 1.0, 0

        if scale_mode == "Fixed Range":
            low, high = (-1.2, 1.2) if bipolar else (0.0, 1.2)
            outside = int(np.count_nonzero((finite < low) | (finite > high)))
            self.current = (low, high)
            return low, high, outside

        if scale_mode == "Full Range":
            raw_low = float(np.min(finite))
            raw_high = float(np.max(finite))
        else:
            if bipolar:
                amplitude = float(np.percentile(np.abs(finite), 99.0))
                amplitude = max(amplitude, 0.05)
                raw_low, raw_high = -amplitude, amplitude
            else:
                raw_low, raw_high = np.percentile(finite, [1.0, 99.0])
                raw_low = float(raw_low)
                raw_high = float(raw_high)

        if bipolar:
            amplitude = max(abs(raw_low), abs(raw_high), 0.05)
            target_low = -amplitude * 1.15
            target_high = amplitude * 1.15
        else:
            span = max(raw_high - raw_low, 0.04)
            target_low = max(0.0, raw_low - span * 0.12)
            target_high = raw_high + span * 0.18

        if self.current is None:
            low, high = target_low, target_high
        else:
            old_low, old_high = self.current
            expanding = target_low < old_low or target_high > old_high
            alpha = 0.72 if expanding else 0.08

            low = old_low + alpha * (target_low - old_low)
            high = old_high + alpha * (target_high - old_high)

        self.current = (low, high)
        outside = int(np.count_nonzero((finite < low) | (finite > high)))
        return low, high, outside


# ============================================================
# 图槽
# ============================================================

@dataclass
class PlotSlot:
    index: int
    channel_index: int
    source_type: str
    scale_mode: str

    def __post_init__(self) -> None:
        prefix = f"slot::{self.index}"

        self.container_tag = f"{prefix}::container"
        self.title_tag = f"{prefix}::title"
        self.channel_combo_tag = f"{prefix}::channel"
        self.source_combo_tag = f"{prefix}::source"
        self.scale_combo_tag = f"{prefix}::scale"
        self.status_tag = f"{prefix}::status"

        self.plot_tag = f"{prefix}::plot"
        self.x_axis_tag = f"{prefix}::x"
        self.y_axis_tag = f"{prefix}::y"
        self.series_tag = f"{prefix}::series"

        self.scaler = AxisScaler()

    def build(self, parent: int | str, show_x_labels: bool) -> None:
        with dpg.group(parent=parent, tag=self.container_tag):
            with dpg.group(horizontal=True):
                dpg.add_text(
                    f"Plot Slot {self.index + 1}",
                    tag=self.title_tag,
                    bullet=True,
                )

                dpg.add_spacer(width=8)

                dpg.add_combo(
                    [f"CH {i + 1}" for i in range(CHANNEL_COUNT)],
                    default_value=f"CH {self.channel_index + 1}",
                    width=90,
                    tag=self.channel_combo_tag,
                    callback=self._on_channel_changed,
                )

                dpg.add_combo(
                    SOURCE_OPTIONS,
                    default_value=self.source_type,
                    width=120,
                    tag=self.source_combo_tag,
                    callback=self._on_source_changed,
                )

                dpg.add_combo(
                    SCALE_OPTIONS,
                    default_value=self.scale_mode,
                    width=120,
                    tag=self.scale_combo_tag,
                    callback=self._on_scale_changed,
                )

                dpg.add_spacer(width=10)
                dpg.add_text("", tag=self.status_tag)

            with dpg.plot(
                tag=self.plot_tag,
                width=-1,
                height=154,
                anti_aliased=True,
                no_mouse_pos=True,
            ):
                dpg.add_plot_axis(
                    dpg.mvXAxis,
                    label="时间 / s" if show_x_labels else "",
                    tag=self.x_axis_tag,
                    no_tick_labels=not show_x_labels,
                )

                dpg.add_plot_axis(
                    dpg.mvYAxis,
                    label="mV",
                    tag=self.y_axis_tag,
                )

                dpg.add_line_series(
                    [],
                    [],
                    parent=self.y_axis_tag,
                    tag=self.series_tag,
                )

        dpg.bind_item_theme(self.container_tag, "theme::plot_card")

    def _on_channel_changed(self, sender, app_data, user_data=None) -> None:
        del sender, user_data
        self.channel_index = int(str(app_data).split()[-1]) - 1
        self.scaler.reset()

    def _on_source_changed(self, sender, app_data, user_data=None) -> None:
        del sender, user_data
        self.source_type = str(app_data)
        self.scaler.reset()

    def _on_scale_changed(self, sender, app_data, user_data=None) -> None:
        del sender, user_data
        self.scale_mode = str(app_data)
        self.scaler.reset()

    def update(
        self,
        buffers: list[SignalBuffer],
        window_seconds: float,
    ) -> None:
        t, raw = buffers[self.channel_index].recent(window_seconds)

        if t.size == 0:
            dpg.set_value(self.series_tag, [[], []])
            dpg.set_value(self.status_tag, "Waiting for data")
            return

        y, bipolar, unit = process_signal(raw, self.source_type)
        x = t - t[-1]

        x_display, y_display = minmax_downsample(x, y)

        dpg.set_value(
            self.series_tag,
            [x_display.tolist(), y_display.tolist()],
        )

        dpg.set_axis_limits(
            self.x_axis_tag,
            -float(window_seconds),
            0.0,
        )

        low, high, outside = self.scaler.get_limits(
            y,
            self.scale_mode,
            bipolar,
        )
        dpg.set_axis_limits(self.y_axis_tag, low, high)
        dpg.set_item_label(self.y_axis_tag, unit)

        peak = float(np.max(np.abs(y)))
        rms = float(np.sqrt(np.mean(y * y)))

        if outside:
            status = (
                f"Peak {peak:.3f} {unit}   "
                f"RMS {rms:.3f}   "
                f"Axis Out {outside} Points"
            )
        else:
            status = (
                f"Peak {peak:.3f} {unit}   "
                f"RMS {rms:.3f}"
            )

        dpg.set_value(self.status_tag, status)


# ============================================================
# 模拟 EMG 数据
# ============================================================

class DemoSignalGenerator:
    def __init__(self) -> None:
        self.sample_index = 0
        self.rng = np.random.default_rng(7)

    def generate(
        self,
        block_size: int,
    ) -> tuple[np.ndarray, list[np.ndarray]]:
        indices = np.arange(
            self.sample_index,
            self.sample_index + block_size,
        )
        t = indices / SAMPLE_RATE

        output: list[np.ndarray] = []

        for channel in range(CHANNEL_COUNT):
            phase = channel * 0.55

            activation = np.maximum(
                0.0,
                np.sin(2.0 * np.pi * (0.18 + channel * 0.015) * t + phase),
            ) ** 2.5

            envelope = 0.025 + (0.14 + channel * 0.018) * activation

            carrier = (
                self.rng.normal(0.0, 1.0, block_size)
                + 0.25 * np.sin(2.0 * np.pi * (70 + channel * 8) * t)
            )

            signal = envelope * carrier

            # 偶发尖峰，方便观察稳健缩放效果。
            if self.rng.random() < 0.022:
                spike_index = int(self.rng.integers(0, block_size))
                signal[spike_index] += float(
                    self.rng.choice((-1.0, 1.0))
                ) * self.rng.uniform(1.5, 3.8)

            output.append(signal)

        self.sample_index += block_size
        return t, output


# ============================================================
# UI
# ============================================================

class EMGUIDemo:
    def __init__(self) -> None:
        self.buffers = [
            SignalBuffer(MAX_BUFFER_POINTS)
            for _ in range(CHANNEL_COUNT)
        ]

        self.generator = DemoSignalGenerator()
        self.last_data_time = time.monotonic()
        self.last_ui_time = time.monotonic()

        default_sources = [
            "Raw EMG",
            "Raw EMG",
            "Raw EMG",
            "Full-Wave Rectified",
            "RMS",
            "Envelope",
        ]

        self.slots = [
            PlotSlot(
                index=i,
                channel_index=i,
                source_type=default_sources[i],
                scale_mode="Robust Scaling",
            )
            for i in range(CHANNEL_COUNT)
        ]

    def create_themes(self) -> None:
        with dpg.theme(tag="theme::global"):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_style(
                    dpg.mvStyleVar_WindowPadding,
                    12,
                    12,
                )
                dpg.add_theme_style(
                    dpg.mvStyleVar_FramePadding,
                    8,
                    5,
                )
                dpg.add_theme_style(
                    dpg.mvStyleVar_ItemSpacing,
                    8,
                    7,
                )
                dpg.add_theme_style(
                    dpg.mvStyleVar_FrameRounding,
                    5,
                )
                dpg.add_theme_style(
                    dpg.mvStyleVar_ChildRounding,
                    8,
                )
                dpg.add_theme_style(
                    dpg.mvStyleVar_WindowRounding,
                    8,
                )

                dpg.add_theme_color(
                    dpg.mvThemeCol_WindowBg,
                    (18, 21, 28, 255),
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_ChildBg,
                    (24, 28, 36, 255),
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_FrameBg,
                    (36, 42, 53, 255),
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_FrameBgHovered,
                    (48, 57, 71, 255),
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_Header,
                    (42, 91, 115, 255),
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button,
                    (40, 89, 112, 255),
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_Text,
                    (226, 232, 240, 255),
                )

        with dpg.theme(tag="theme::plot_card"):
            with dpg.theme_component(dpg.mvChildWindow):
                dpg.add_theme_style(
                    dpg.mvStyleVar_ChildRounding,
                    7,
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_ChildBg,
                    (24, 28, 36, 255),
                )

        with dpg.theme(tag="theme::start_button"):
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(
                    dpg.mvThemeCol_Button,
                    (31, 122, 88, 255),
                )
                dpg.add_theme_color(
                    dpg.mvThemeCol_ButtonHovered,
                    (39, 148, 107, 255),
                )

    def build(self) -> None:
        dpg.create_context()
        self.create_themes()
        dpg.bind_theme("theme::global")

        with dpg.window(
            tag="main_window",
            label="EMG Monitor",
            no_title_bar=True,
        ):
            with dpg.group(horizontal=True):
                with dpg.group():
                    dpg.add_text("Multi-Channel EMG Monitor")
                    dpg.add_text(
                        "Modular Plotting · Dynamic Data Types · Robust Scaling",
                        color=(145, 158, 176),
                    )

                dpg.add_spacer(width=40)

                dpg.add_button(
                    label="Pause",
                    width=90,
                    tag="control::pause_button",
                    callback=self.toggle_pause,
                )
                dpg.bind_item_theme(
                    "control::pause_button",
                    "theme::start_button",
                )

                dpg.add_text("Display Window")
                dpg.add_slider_float(
                    min_value=1.0,
                    max_value=15.0,
                    default_value=5.0,
                    format="%.1f s",
                    width=170,
                    tag="control::window",
                )

                dpg.add_text("Refresh Rate")
                dpg.add_slider_int(
                    min_value=5,
                    max_value=60,
                    default_value=25,
                    format="%d Hz",
                    width=150,
                    tag="control::refresh_hz",
                )

                dpg.add_spacer(width=20)

                dpg.add_text(
                    "● Receiving Simulated Data",
                    color=(74, 222, 128),
                    tag="control::status",
                )

            dpg.add_separator()

            with dpg.group(horizontal=True):
                with dpg.child_window(
                    width=225,
                    height=-1,
                    border=True,
                ):
                    dpg.add_text("Display Settings")
                    dpg.add_separator()

                    dpg.add_text("Layout Density")
                    dpg.add_radio_button(
                        ["Compact", "Comfortable"],
                        default_value="Comfortable",
                        tag="control::density",
                        callback=self.change_density,
                    )

                    dpg.add_spacer(height=8)
                    dpg.add_text("Global Operations")

                    dpg.add_button(
                        label="Set All to Robust Scaling",
                        width=-1,
                        callback=self.set_all_robust,
                    )
                    dpg.add_button(
                        label="Show All Raw EMG",
                        width=-1,
                        callback=self.set_all_raw,
                    )
                    dpg.add_button(
                        label="Restore Default Layout",
                        width=-1,
                        callback=self.restore_defaults,
                    )

                    dpg.add_spacer(height=12)
                    dpg.add_separator()
                    dpg.add_text("Instructions")
                    dpg.add_text(
                        "Each plot slot can be configured independently:\n"
                        "- Channel\n"
                        "- Signal Type\n"
                        "- Scale Mode\n\n"
                        "Robust Scaling will reduce the impact of sporadic spikes\n"
                        "on the vertical axis range, but will not\n"
                        "modify the original data.",
                        wrap=190,
                        color=(155, 167, 184),
                    )

                with dpg.child_window(
                    width=-1,
                    height=-1,
                    border=False,
                    tag="plots::scroll",
                ):
                    for i, slot in enumerate(self.slots):
                        with dpg.child_window(
                            width=-1,
                            height=205,
                            border=True,
                            tag=f"card::{i}",
                        ):
                            slot.build(
                                parent=dpg.last_item(),
                                show_x_labels=(i == len(self.slots) - 1),
                            )

                        dpg.add_spacer(height=6)

        dpg.create_viewport(
            title="EMG UI Demo",
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            min_width=1040,
            min_height=700,
        )
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("main_window", True)

    def toggle_pause(self) -> None:
        paused = bool(dpg.get_item_user_data("control::pause_button"))

        if paused:
            dpg.set_item_user_data("control::pause_button", False)
            dpg.set_item_label("control::pause_button", "Pause")
            dpg.set_value(
                "control::status",
                "- Receiving Simulated Data",
            )
            dpg.configure_item(
                "control::status",
                color=(74, 222, 128),
            )
        else:
            dpg.set_item_user_data("control::pause_button", True)
            dpg.set_item_label("control::pause_button", "Resume")
            dpg.set_value(
                "control::status",
                "- Display Paused",
            )
            dpg.configure_item(
                "control::status",
                color=(251, 191, 36),
            )

    def change_density(self, sender, app_data, user_data=None) -> None:
        del sender, user_data
        height = 172 if app_data == "Compact" else 205

        for i in range(len(self.slots)):
            dpg.configure_item(f"card::{i}", height=height)

    def set_all_robust(self) -> None:
        for slot in self.slots:
            slot.scale_mode = "Robust Scaling"
            slot.scaler.reset()
            dpg.set_value(slot.scale_combo_tag, "Robust Scaling")

    def set_all_raw(self) -> None:
        for slot in self.slots:
            slot.source_type = "Raw EMG"
            slot.scaler.reset()
            dpg.set_value(slot.source_combo_tag, "Raw EMG")

    def restore_defaults(self) -> None:
        default_sources = [
            "Raw EMG",
            "Raw EMG",
            "Raw EMG",
            "Rectified",
            "RMS",
            "Envelope",
        ]

        for i, slot in enumerate(self.slots):
            slot.channel_index = i
            slot.source_type = default_sources[i]
            slot.scale_mode = "Robust Scaling"
            slot.scaler.reset()

            dpg.set_value(slot.channel_combo_tag, f"CH {i + 1}")
            dpg.set_value(slot.source_combo_tag, default_sources[i])
            dpg.set_value(slot.scale_combo_tag, "Robust Scaling")

        dpg.set_value("control::window", 5.0)
        dpg.set_value("control::refresh_hz", 25)
        dpg.set_value("control::density", "Compact")
        self.change_density(None, "Compact")

    def update_data(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_data_time

        block_size = int(elapsed * SAMPLE_RATE)
        if block_size < 10:
            return

        block_size = min(block_size, 100)
        t, channels = self.generator.generate(block_size)

        for buffer, y in zip(self.buffers, channels):
            buffer.append(t, y)

        self.last_data_time = now

    def update_plots(self) -> None:
        refresh_hz = max(
            1,
            int(dpg.get_value("control::refresh_hz")),
        )
        period = 1.0 / refresh_hz
        now = time.monotonic()

        if now - self.last_ui_time < period:
            return

        paused = bool(
            dpg.get_item_user_data("control::pause_button")
        )
        if paused:
            return

        window_seconds = float(
            dpg.get_value("control::window")
        )

        for slot in self.slots:
            slot.update(self.buffers, window_seconds)

        self.last_ui_time = now

    def run(self) -> None:
        self.build()
        dpg.set_item_user_data("control::pause_button", False)

        try:
            while dpg.is_dearpygui_running():
                self.update_data()
                self.update_plots()
                dpg.render_dearpygui_frame()
        finally:
            dpg.destroy_context()


if __name__ == "__main__":
    EMGUIDemo().run()
