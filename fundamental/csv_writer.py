"""CSV persistence for captured raw samples."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from fundamental.messages import CHANNEL_COUNT, SampleFrame


def default_capture_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("captures") / f"capture_{timestamp}.csv"


def save_frames(path: str | Path, frames: list[SampleFrame]) -> tuple[Path, int]:
    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "time_s",
                "frame_counter",
                "dropped_frames_before",
                "emg_channel_count",
                *[f"ch{i}_code" for i in range(1, CHANNEL_COUNT + 1)],
            ]
        )
        for frame in frames:
            writer.writerow(
                [
                    f"{frame.time_s:.6f}",
                    frame.counter,
                    frame.dropped_frames_before,
                    frame.emg_channel_count,
                    *[int(value) for value in frame.values],
                ]
            )

    return output_path, len(frames)
