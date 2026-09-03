"""Recorder facade that selects one persistence backend before recording starts."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Literal

from assembly.acquisition.runtime.stream_store import StreamRow, StreamSchema
from assembly.save.csv_recorder import CSVStreamRecorder
from assembly.save.recorder import H5StreamRecorder, RecorderState, StreamRecorder

RecorderFormat = Literal["hdf5", "csv"]


class SelectableStreamRecorder:
    """Stable recorder object whose backend may be selected while stopped."""

    def __init__(self, format: RecorderFormat = "hdf5") -> None:
        self._format: RecorderFormat = "hdf5"
        self._backend: StreamRecorder | None = None
        self._last_path: Path | None = None
        self._last_rows_written = 0
        self._last_rows_by_stream: dict[str, int] = {}
        self.set_format(format)

    @property
    def format(self) -> RecorderFormat:
        return self._format

    def set_format(self, format: RecorderFormat) -> None:
        if self.is_recording:
            raise RuntimeError("Cannot change recorder format while recording.")
        if format not in ("hdf5", "csv"):
            raise ValueError(f"Unsupported recorder format: {format!r}.")
        self._format = format

    @property
    def state(self) -> RecorderState:
        return RecorderState.RECORDING if self.is_recording else RecorderState.STOPPED

    @property
    def is_recording(self) -> bool:
        return self._backend is not None and self._backend.is_recording

    @property
    def path(self) -> Path | None:
        return self._backend.path if self._backend is not None else self._last_path

    @property
    def rows_written(self) -> int:
        return self._backend.rows_written if self._backend is not None else self._last_rows_written

    def rows_by_stream(self) -> dict[str, int]:
        if self._backend is not None:
            return self._backend.rows_by_stream()
        return dict(self._last_rows_by_stream)

    def start(self, path: str | Path, schemas: Iterable[StreamSchema]) -> Path:
        if self.is_recording:
            raise RuntimeError("Recorder is already recording.")
        backend: StreamRecorder
        if self._format == "hdf5":
            backend = H5StreamRecorder()
        else:
            backend = CSVStreamRecorder()
        result = backend.start(path, schemas)
        self._backend = backend
        self._last_path = result
        self._last_rows_written = 0
        self._last_rows_by_stream = backend.rows_by_stream()
        return result

    def append(self, stream_id: str, row: StreamRow) -> None:
        backend = self._require_backend()
        backend.append(stream_id, row)

    def append_batch(self, stream_id: str, rows: Iterable[StreamRow]) -> int:
        backend = self._require_backend()
        return backend.append_batch(stream_id, rows)

    def flush(self) -> None:
        if self._backend is not None:
            self._backend.flush()

    def stop(self) -> Path | None:
        backend = self._backend
        if backend is None:
            return self._last_path
        self._last_rows_written = backend.rows_written
        self._last_rows_by_stream = backend.rows_by_stream()
        path = backend.stop()
        self._last_path = path
        self._backend = None
        return path

    def close(self) -> Path | None:
        return self.stop()

    def _require_backend(self) -> StreamRecorder:
        backend = self._backend
        if backend is None or not backend.is_recording:
            raise RuntimeError("Recorder is not recording.")
        return backend


__all__ = ["RecorderFormat", "SelectableStreamRecorder"]
