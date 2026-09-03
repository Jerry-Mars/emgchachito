"""CSV recorder backend for committed normalized stream rows.

One normalized stream is written to one CSV file.  A small metadata.json keeps
schema information that would otherwise be lost in a flat CSV header.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable, TextIO

from assembly.acquisition.runtime.stream_store import StreamRow, StreamSchema
from assembly.save.recorder import RecorderState


class CSVStreamRecorder:
    """Append complete normalized stream rows to a directory of CSV files."""

    def __init__(self) -> None:
        self._state = RecorderState.STOPPED
        self._path: Path | None = None
        self._schemas: dict[str, StreamSchema] = {}
        self._files: dict[str, TextIO] = {}
        self._writers: dict[str, csv.writer] = {}
        self._rows_written = 0
        self._rows_by_stream: dict[str, int] = {}

    @property
    def state(self) -> RecorderState:
        return self._state

    @property
    def is_recording(self) -> bool:
        return self._state is RecorderState.RECORDING

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def rows_written(self) -> int:
        return self._rows_written

    def rows_by_stream(self) -> dict[str, int]:
        return dict(self._rows_by_stream)

    def start(self, path: str | Path, schemas: Iterable[StreamSchema]) -> Path:
        if self.is_recording:
            raise RuntimeError("Recorder is already recording.")

        target = Path(path).expanduser()
        if not target.name:
            raise ValueError("Recorder path must include an output name.")
        if target.suffix.lower() in {".csv", ".h5", ".hdf5"}:
            target = target.with_suffix("")

        frozen = tuple(schemas)
        if not frozen:
            raise ValueError("Recorder requires at least one stream schema.")
        by_id = {schema.stream_id: schema for schema in frozen}
        if len(by_id) != len(frozen):
            raise ValueError("Recorder stream IDs must be unique.")

        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(f"Recorder output already exists: {target}")

        streams_dir = target / "streams"
        opened: dict[str, TextIO] = {}
        writers: dict[str, csv.writer] = {}
        try:
            streams_dir.mkdir(parents=True, exist_ok=False)
            metadata = {
                "format": "assembly.normalized_streams.csv",
                "format_version": 1,
                "streams": [],
            }
            for stream_index, schema in enumerate(frozen):
                file_name = f"stream_{stream_index:04d}.csv"
                handle = (streams_dir / file_name).open("x", newline="", encoding="utf-8")
                writer = csv.writer(handle)
                writer.writerow(
                    (
                        "runtime_index",
                        "host_monotonic_ns",
                        "host_unix_ns",
                        *schema.field_keys,
                    )
                )
                opened[schema.stream_id] = handle
                writers[schema.stream_id] = writer
                metadata["streams"].append(
                    {
                        "stream_id": schema.stream_id,
                        "field_keys": list(schema.field_keys),
                        "nominal_rate_hz": schema.nominal_rate_hz,
                        "file": f"streams/{file_name}",
                    }
                )
            (target / "metadata.json").write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except BaseException:
            for handle in opened.values():
                handle.close()
            if target.exists():
                for child in sorted(target.rglob("*"), reverse=True):
                    if child.is_file():
                        child.unlink(missing_ok=True)
                    elif child.is_dir():
                        child.rmdir()
                target.rmdir()
            raise

        self._path = target
        self._schemas = by_id
        self._files = opened
        self._writers = writers
        self._rows_written = 0
        self._rows_by_stream = {stream_id: 0 for stream_id in by_id}
        self._state = RecorderState.RECORDING
        return target

    def append(self, stream_id: str, row: StreamRow) -> None:
        self.append_batch(stream_id, (row,))

    def append_batch(self, stream_id: str, rows: Iterable[StreamRow]) -> int:
        if not self.is_recording:
            raise RuntimeError("Recorder is not recording.")
        schema = self._schemas.get(stream_id)
        writer = self._writers.get(stream_id)
        if schema is None or writer is None:
            raise KeyError(f"Recorder does not know stream_id {stream_id!r}.")

        pending = tuple(rows)
        if not pending:
            return 0
        for row in pending:
            if len(row.values) != len(schema.field_keys):
                raise ValueError(
                    f"{stream_id!r} expects {len(schema.field_keys)} values, "
                    f"got {len(row.values)}."
                )
            writer.writerow(
                (
                    row.runtime_index,
                    row.host_monotonic_ns,
                    row.host_unix_ns,
                    *row.values,
                )
            )

        count = len(pending)
        self._rows_written += count
        self._rows_by_stream[stream_id] += count
        return count

    def flush(self) -> None:
        for handle in self._files.values():
            handle.flush()

    def stop(self) -> Path | None:
        path = self._path
        files = tuple(self._files.values())
        self._files = {}
        self._writers = {}
        self._schemas = {}
        self._state = RecorderState.STOPPED
        for handle in files:
            try:
                handle.flush()
            finally:
                handle.close()
        return path

    def close(self) -> Path | None:
        return self.stop()

    def __enter__(self) -> "CSVStreamRecorder":
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()


__all__ = ["CSVStreamRecorder"]
