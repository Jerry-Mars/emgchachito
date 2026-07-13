"""Signal display transforms for the live plot window."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


SignalView = Literal["Raw", "Rectified", "RMS", "Envelope"]
ScaleMode = Literal["Robust Scaling", "Full Range", "Fixed Range"]

SIGNAL_VIEW_OPTIONS: tuple[SignalView, ...] = ("Raw", "Rectified", "RMS", "Envelope")
SCALE_MODE_OPTIONS: tuple[ScaleMode, ...] = ("Robust Scaling", "Full Range", "Fixed Range")

FIXED_BIPOLAR_LIMIT = 1.2
FIXED_UNIPOLAR_LIMIT = 1.2


@dataclass(frozen=True)
class ProcessedSignal:
    values: list[float]
    bipolar: bool
    unit: str


@dataclass(frozen=True)
class SignalStats:
    peak: float
    rms: float


def moving_average(values: list[float], window: int) -> list[float]:
    if not values:
        return []

    window = max(1, min(int(window), len(values)))
    half_window = window // 2
    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)

    averaged: list[float] = []
    for index in range(len(values)):
        start = max(0, index - half_window)
        end = min(len(values), start + window)
        start = max(0, end - window)
        averaged.append((prefix[end] - prefix[start]) / (end - start))
    return averaged


def moving_rms(values: list[float], window: int = 50) -> list[float]:
    return [math.sqrt(value) for value in moving_average([value * value for value in values], window)]


def process_signal(values: list[float], signal_view: str) -> ProcessedSignal:
    raw = [float(value) for value in values]
    if signal_view == "Rectified":
        return ProcessedSignal([abs(value) for value in raw], False, "abs(code)")
    if signal_view == "RMS":
        return ProcessedSignal(moving_rms(raw, 50), False, "code RMS")
    if signal_view == "Envelope":
        return ProcessedSignal(moving_average([abs(value) for value in raw], 100), False, "code")
    return ProcessedSignal(raw, True, "code")


def minmax_downsample(
    x_values: list[float],
    y_values: list[float],
    max_points: int = 1600,
) -> tuple[list[float], list[float]]:
    if len(x_values) != len(y_values):
        raise ValueError("x_values and y_values must have the same length.")
    if len(x_values) <= max_points:
        return list(x_values), list(y_values)

    bucket_count = max(1, int(max_points) // 2)
    selected: list[int] = []
    total_count = len(x_values)
    for bucket_index in range(bucket_count):
        start = bucket_index * total_count // bucket_count
        end = (bucket_index + 1) * total_count // bucket_count
        if end <= start:
            continue

        segment = y_values[start:end]
        min_index = start + min(range(len(segment)), key=segment.__getitem__)
        max_index = start + max(range(len(segment)), key=segment.__getitem__)
        selected.extend(sorted((min_index, max_index)))

    deduped = sorted(set(selected))
    return [x_values[index] for index in deduped], [y_values[index] for index in deduped]


def signal_stats(values: list[float]) -> SignalStats:
    if not values:
        return SignalStats(0.0, 0.0)
    peak = max(abs(value) for value in values)
    rms = math.sqrt(sum(value * value for value in values) / len(values))
    return SignalStats(peak, rms)


class AxisScaler:
    """Smooth y-axis limits for one plot slot."""

    def __init__(self) -> None:
        self.current: tuple[float, float] | None = None

    def reset(self) -> None:
        self.current = None

    def get_limits(self, values: list[float], scale_mode: str, bipolar: bool) -> tuple[float, float, int]:
        finite = [value for value in values if math.isfinite(value)]
        if not finite:
            return -1.0, 1.0, 0

        if scale_mode == "Fixed Range":
            low, high = (
                (-FIXED_BIPOLAR_LIMIT, FIXED_BIPOLAR_LIMIT)
                if bipolar
                else (0.0, FIXED_UNIPOLAR_LIMIT)
            )
            outside = _outside_count(finite, low, high)
            self.current = (low, high)
            return low, high, outside

        if scale_mode == "Full Range":
            raw_low = min(finite)
            raw_high = max(finite)
        else:
            if bipolar:
                amplitude = max(_percentile([abs(value) for value in finite], 99.0), 0.05)
                raw_low, raw_high = -amplitude, amplitude
            else:
                raw_low = _percentile(finite, 1.0)
                raw_high = _percentile(finite, 99.0)

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
        return low, high, _outside_count(finite, low, high)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    rank = (len(ordered) - 1) * max(0.0, min(100.0, float(percentile))) / 100.0
    lower_index = int(math.floor(rank))
    upper_index = int(math.ceil(rank))
    if lower_index == upper_index:
        return ordered[lower_index]
    fraction = rank - lower_index
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction


def _outside_count(values: list[float], low: float, high: float) -> int:
    return sum(1 for value in values if value < low or value > high)
