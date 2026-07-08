"""CSV persistence for captured raw samples."""

from __future__ import annotations

import csv
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from fundamental.messages import CHANNEL_COUNT, SampleFrame


def default_capture_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("captures") / f"capture_{timestamp}.csv"


StimulusCodeResolver = Callable[[float], int]


def save_frames(
    path: str | Path,
    frames: list[SampleFrame],
    stimulus_code_for_time: StimulusCodeResolver | None = None,
) -> tuple[Path, int]:
    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    include_stimulus = stimulus_code_for_time is not None

    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "time_s",
                "frame_counter",
                "dropped_frames_before",
                "emg_channel_count",
                *(["stimulus_code"] if include_stimulus else []),
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
                    *(
                        [int(stimulus_code_for_time(frame.time_s))]
                        if stimulus_code_for_time is not None
                        else []
                    ),
                    *[int(value) for value in frame.values],
                ]
            )

    return output_path, len(frames)


def stimulus_log_path(capture_path: str | Path) -> Path:
    output_path = Path(capture_path)
    return output_path.with_name(f"{output_path.stem}.stimulus.csv")


def save_stimulus_log(path: str | Path, rows: Sequence[dict[str, Any]]) -> tuple[Path, int]:
    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "event_index",
        "stimulus_code",
        "planned_code",
        "label",
        "start_time_s",
        "end_time_s",
        "status",
    ]

    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "event_index": row.get("event_index", ""),
                    "stimulus_code": row.get("stimulus_code", ""),
                    "planned_code": row.get("planned_code", ""),
                    "label": row.get("label", ""),
                    "start_time_s": _format_optional_time(row.get("start_time_s")),
                    "end_time_s": _format_optional_time(row.get("end_time_s")),
                    "status": row.get("status", ""),
                }
            )

    return output_path, len(rows)


def _format_optional_time(value: object) -> str:
    if value is None:
        return ""
    return f"{float(value):.6f}"
