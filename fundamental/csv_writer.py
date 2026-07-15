"""CSV persistence for captured raw samples."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fundamental.streams import FieldSpec, StreamSnapshot


def default_capture_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("captures") / f"capture_{timestamp}.csv"


StimulusCodeResolver = Callable[[float], int]


@dataclass(frozen=True)
class SavedStream:
    stream_id: str
    path: Path
    row_count: int


@dataclass(frozen=True)
class CaptureSaveResult:
    streams: tuple[SavedStream, ...]
    metadata_path: Path
    stimulus_path: Path | None = None
    stimulus_rows: int = 0

    @property
    def total_rows(self) -> int:
        return sum(stream.row_count for stream in self.streams)


def save_capture(
    path: str | Path,
    snapshots: Sequence[StreamSnapshot],
    *,
    stimulus_code_for_time: StimulusCodeResolver | None = None,
    stimulus_log_rows: Sequence[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> CaptureSaveResult:
    """Save each independent stream without resampling or sparse union rows."""

    populated = [snapshot for snapshot in snapshots if snapshot.rows]
    if not populated:
        raise ValueError("No stream rows to save.")

    base_path = Path(path).expanduser()
    base_path.parent.mkdir(parents=True, exist_ok=True)
    multiple = len(populated) > 1
    saved: list[SavedStream] = []
    for snapshot in populated:
        output_path = _stream_output_path(base_path, snapshot.spec.stream_id, multiple)
        row_count = _save_stream(
            output_path,
            snapshot,
            stimulus_code_for_time=stimulus_code_for_time,
        )
        saved.append(SavedStream(snapshot.spec.stream_id, output_path, row_count))

    metadata_path = _metadata_path(base_path)
    capture_metadata = dict(metadata or {})
    capture_metadata.setdefault("saved_at", datetime.now().astimezone().isoformat())
    capture_metadata["streams"] = [
        {
            "stream_id": snapshot.spec.stream_id,
            "display_name": snapshot.spec.display_name,
            "nominal_rate_hz": snapshot.spec.nominal_rate_hz,
            "time_source": snapshot.spec.time_source,
            "row_count": snapshot.row_count,
            "file": str(saved_stream.path),
            "fields": [
                {
                    "key": field.key,
                    "label": field.label,
                    "unit": field.unit,
                    "role": field.role,
                    "signal_kind": field.signal_kind,
                }
                for field in snapshot.spec.fields
            ],
        }
        for snapshot, saved_stream in zip(populated, saved, strict=True)
    ]
    metadata_path.write_text(
        json.dumps(capture_metadata, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    stimulus_path: Path | None = None
    stimulus_rows = 0
    if stimulus_log_rows is not None:
        stimulus_path, stimulus_rows = save_stimulus_log(
            stimulus_log_path(base_path),
            stimulus_log_rows,
        )

    return CaptureSaveResult(tuple(saved), metadata_path, stimulus_path, stimulus_rows)


def _save_stream(
    output_path: Path,
    snapshot: StreamSnapshot,
    *,
    stimulus_code_for_time: StimulusCodeResolver | None,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    include_stimulus = stimulus_code_for_time is not None
    metadata_fields = [field for field in snapshot.spec.fields if field.role == "metadata"]
    signal_fields = [field for field in snapshot.spec.fields if field.role == "signal"]
    field_indexes = snapshot.spec.field_index

    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "time_s",
                *[field.key for field in metadata_fields],
                *(["stimulus_code"] if include_stimulus else []),
                *[field.key for field in signal_fields],
            ]
        )
        for timestamp, row in zip(snapshot.time_s, snapshot.rows, strict=True):
            writer.writerow(
                [
                    f"{float(timestamp):.6f}",
                    *[
                        _format_field_value(field, row[field_indexes[field.key]])
                        for field in metadata_fields
                    ],
                    *(
                        [int(stimulus_code_for_time(float(timestamp)))]
                        if stimulus_code_for_time is not None
                        else []
                    ),
                    *[
                        _format_field_value(field, row[field_indexes[field.key]])
                        for field in signal_fields
                    ],
                ]
            )
    return snapshot.row_count


def _format_field_value(field: FieldSpec, value: int | float) -> int | float | str:
    if field.csv_decimals is not None:
        return f"{float(value):.{field.csv_decimals}f}"
    return value


def _stream_output_path(base_path: Path, stream_id: str, multiple: bool) -> Path:
    if not multiple:
        return base_path
    safe_id = "".join(character if character.isalnum() else "_" for character in stream_id)
    safe_id = safe_id.strip("_") or "stream"
    return base_path.with_name(f"{base_path.stem}.{safe_id}.csv")


def _metadata_path(capture_path: str | Path) -> Path:
    output_path = Path(capture_path)
    return output_path.with_name(f"{output_path.stem}.metadata.json")


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
