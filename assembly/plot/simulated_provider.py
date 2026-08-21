"""Simulated replacement for ``AcquisitionController.buffer``."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from assembly.plot.models import SeriesSpec, SeriesWindow


class SimulatedPlotProvider:
    """Expose generated EMG and IMU series through the real Plot protocol."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started_at = clock()
        self._specs = (
            SeriesSpec(
                series_id="sim.emg/left_uv",
                stream_id="sim.emg",
                field_key="left_uv",
                label="Left EMG",
                unit="uV",
                signal_kind="emg",
                default_plot=True,
                fixed_range=(-1.2, 1.2),
            ),
            SeriesSpec(
                series_id="sim.emg/right_uv",
                stream_id="sim.emg",
                field_key="right_uv",
                label="Right EMG",
                unit="uV",
                signal_kind="emg",
                default_plot=True,
                fixed_range=(-1.2, 1.2),
            ),
            SeriesSpec(
                series_id="sim.imu/accel_x_g",
                stream_id="sim.imu",
                field_key="accel_x_g",
                label="Acceleration X",
                unit="g",
                signal_kind="acceleration",
                default_plot=False,
                fixed_range=(-2.0, 2.0),
            ),
            SeriesSpec(
                series_id="sim.imu/gyro_z_dps",
                stream_id="sim.imu",
                field_key="gyro_z_dps",
                label="Gyroscope Z",
                unit="dps",
                signal_kind="angular_velocity",
                default_plot=False,
                fixed_range=(-250.0, 250.0),
            ),
        )
        self._by_id = {spec.series_id: spec for spec in self._specs}
        self._rates = {
            "sim.emg": 1000.0,
            "sim.imu": 100.0,
        }

    @property
    def row_count(self) -> int:
        elapsed = self._elapsed()
        return sum(int(elapsed * rate) + 1 for rate in self._rates.values())

    @property
    def stream_count(self) -> int:
        return len(self._rates)

    def series_specs(self) -> tuple[SeriesSpec, ...]:
        return self._specs

    def series_spec(self, series_id: str) -> SeriesSpec | None:
        return self._by_id.get(series_id)

    def get_series_window(
        self,
        series_id: str,
        window_seconds: float,
    ) -> SeriesWindow | None:
        spec = self.series_spec(series_id)
        if spec is None:
            return None

        elapsed = self._elapsed()
        rate = self._rates[spec.stream_id]
        first_index = math.ceil(max(0.0, elapsed - float(window_seconds)) * rate)
        last_index = math.floor(elapsed * rate)
        time_s = [index / rate for index in range(first_index, last_index + 1)]
        values = [self._value(spec.field_key, timestamp) for timestamp in time_s]
        return SeriesWindow(spec=spec, time_s=time_s, values=values)

    def latest_series_values(
        self,
        limit: int = 4,
    ) -> tuple[tuple[SeriesSpec, float], ...]:
        elapsed = self._elapsed()
        return tuple(
            (spec, self._value(spec.field_key, elapsed))
            for spec in self._specs[: max(0, int(limit))]
        )

    def _elapsed(self) -> float:
        return max(0.0, self._clock() - self._started_at)

    @staticmethod
    def _value(field_key: str, time_s: float) -> float:
        if field_key == "left_uv":
            envelope = 0.35 + 0.25 * (1.0 + math.sin(2.0 * math.pi * 0.35 * time_s))
            return envelope * (
                math.sin(2.0 * math.pi * 32.0 * time_s)
                + 0.28 * math.sin(2.0 * math.pi * 71.0 * time_s)
            )
        if field_key == "right_uv":
            envelope = 0.30 + 0.20 * (1.0 + math.sin(2.0 * math.pi * 0.28 * time_s + 0.8))
            return envelope * (
                math.sin(2.0 * math.pi * 27.0 * time_s + 0.4)
                + 0.22 * math.sin(2.0 * math.pi * 63.0 * time_s)
            )
        if field_key == "accel_x_g":
            return 0.45 * math.sin(2.0 * math.pi * 0.7 * time_s)
        if field_key == "gyro_z_dps":
            return 110.0 * math.sin(2.0 * math.pi * 0.45 * time_s + 0.5)
        return 0.0
