"""In-memory buffers for live plotting and later CSV saving."""

from __future__ import annotations

from collections import deque

from fundamental.messages import CHANNEL_COUNT, DEFAULT_PLOT_BUFFER_SIZE, SampleBatch, SampleFrame


class SignalBuffer:
    """Keep a rolling plot buffer and the full current capture rows."""

    def __init__(self, plot_buffer_size: int = DEFAULT_PLOT_BUFFER_SIZE) -> None:
        self.plot_buffer_size = plot_buffer_size
        self.timestamps = deque(maxlen=plot_buffer_size)
        self.channels = [deque(maxlen=plot_buffer_size) for _ in range(CHANNEL_COUNT)]
        self.frames: list[SampleFrame] = []
        self.latest_values = [0.0 for _ in range(CHANNEL_COUNT)]
        self.emg_channel_count = 0

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def latest_time_s(self) -> float:
        if not self.frames:
            return 0.0
        return self.frames[-1].time_s

    @property
    def active_channel_count(self) -> int:
        return self.emg_channel_count if self.frames else 0

    def reset(self) -> None:
        self.timestamps = deque(maxlen=self.plot_buffer_size)
        self.channels = [deque(maxlen=self.plot_buffer_size) for _ in range(CHANNEL_COUNT)]
        self.frames = []
        self.latest_values = [0.0 for _ in range(CHANNEL_COUNT)]
        self.emg_channel_count = 0

    def append_batch(self, batch: SampleBatch) -> int:
        count = 0
        for frame in batch.frames:
            if len(frame.values) != CHANNEL_COUNT:
                continue
            if frame.emg_channel_count < 1 or frame.emg_channel_count > CHANNEL_COUNT:
                continue
            self.frames.append(frame)
            self.emg_channel_count = frame.emg_channel_count
            self.timestamps.append(frame.time_s)
            for index, value in enumerate(frame.values):
                numeric_value = float(value)
                self.latest_values[index] = numeric_value
                self.channels[index].append(numeric_value)
            count += 1
        return count

    def snapshot_frames(self) -> list[SampleFrame]:
        return list(self.frames)

    def get_plot_window(
        self, window_seconds: float
    ) -> tuple[float, float, list[float], list[list[float]], float, float] | None:
        timestamps = list(self.timestamps)
        if len(timestamps) < 2:
            return None

        x_max = timestamps[-1]
        x_min = max(0.0, x_max - max(0.1, float(window_seconds)))
        start_index = 0
        for index, timestamp in enumerate(timestamps):
            if timestamp >= x_min:
                start_index = index
                break

        window_x = timestamps[start_index:]
        if len(window_x) < 2:
            return None

        active_channel_count = self.active_channel_count
        if active_channel_count < 1:
            return None

        channel_windows: list[list[float]] = []
        y_min = float("inf")
        y_max = float("-inf")
        for channel in self.channels[:active_channel_count]:
            window_y = list(channel)[start_index:]
            if not window_y:
                return None
            channel_windows.append(window_y)
            y_min = min(y_min, min(window_y))
            y_max = max(y_max, max(window_y))

        return x_min, x_max, window_x, channel_windows, y_min, y_max
