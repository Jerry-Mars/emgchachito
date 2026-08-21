"""Data contracts used by the extracted Plot window."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SignalKind = Literal[
    "emg",
    "eeg",
    "quaternion",
    "acceleration",
    "angular_velocity",
    "generic",
]


@dataclass(frozen=True)
class SeriesSpec:
    """One plottable scalar field exposed by a Plot data provider."""

    series_id: str
    stream_id: str
    field_key: str
    label: str
    unit: str
    signal_kind: SignalKind
    default_plot: bool
    fixed_range: tuple[float, float] | None

    @property
    def view_options(self) -> tuple[str, ...]:
        if self.signal_kind == "emg":
            return ("Raw", "Rectified", "RMS", "Envelope")
        return ("Raw",)


@dataclass(frozen=True)
class SeriesWindow:
    """Recent values for one plot series."""

    spec: SeriesSpec
    time_s: list[float]
    values: list[float]
