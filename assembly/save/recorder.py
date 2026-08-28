"""Minimal HDF5 recorder for committed normalized stream rows.

The recorder deliberately knows nothing about hardware workers, queues, plotting,
or stimulus.  It persists committed :class:`StreamRow` values without signal
processing, alignment, interpolation, or time reconstruction.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Iterable

import h5py
import numpy as np

from assembly.acquisition.runtime.stream_store import StreamRow, StreamSchema


class RecorderState(str, Enum):
    STOPPED = "stopped"
    RECORDING = "recording"


class H5StreamRecorder:
    """Append complete normalized stream rows to one HDF5 file.

    A recording starts with a frozen set of stream schemas.  Rows produced before
    :meth:`start` are intentionally not back-filled, and rows produced after
    :meth:`stop` are ignored by callers because the recorder is no longer active.
    """

    def __init__(self) -> None:
        self._state = RecorderState.STOPPED
        self._path: Path | None = None
        self._file: h5py.File | None = None
        self._schemas: dict[str, StreamSchema] = {}
        self._groups: dict[str, h5py.Group] = {}
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
            raise ValueError("Recorder path must include a file name.")
        if target.suffix.lower() not in {".h5", ".hdf5"}:
            target = target.with_suffix(".h5")

        frozen = tuple(schemas)
        if not frozen:
            raise ValueError("Recorder requires at least one stream schema.")
        by_id = {schema.stream_id: schema for schema in frozen}
        if len(by_id) != len(frozen):
            raise ValueError("Recorder stream IDs must be unique.")

        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(f"Recorder output already exists: {target}")

        handle = h5py.File(target, "x")
        groups: dict[str, h5py.Group] = {}
        try:
            handle.attrs["format"] = "assembly.normalized_streams"
            handle.attrs["format_version"] = 1
            streams_group = handle.create_group("streams")

            for stream_index, schema in enumerate(frozen):
                # HDF5 treats '/' as a path separator.  Use a stable internal key
                # and preserve the real stream_id as metadata instead.
                group = streams_group.create_group(f"stream_{stream_index:04d}")
                group.attrs["stream_id"] = schema.stream_id
                group.attrs["field_keys_json"] = json.dumps(schema.field_keys)
                group.attrs["nominal_rate_known"] = schema.nominal_rate_hz is not None
                if schema.nominal_rate_hz is not None:
                    group.attrs["nominal_rate_hz"] = float(schema.nominal_rate_hz)

                group.create_dataset(
                    "runtime_index",
                    shape=(0,),
                    maxshape=(None,),
                    dtype="i8",
                    chunks=True,
                )
                group.create_dataset(
                    "host_monotonic_ns",
                    shape=(0,),
                    maxshape=(None,),
                    dtype="i8",
                    chunks=True,
                )
                group.create_dataset(
                    "host_unix_ns",
                    shape=(0,),
                    maxshape=(None,),
                    dtype="i8",
                    chunks=True,
                )
                group.create_dataset(
                    "values",
                    shape=(0, len(schema.field_keys)),
                    maxshape=(None, len(schema.field_keys)),
                    dtype="f8",
                    chunks=True,
                )
                groups[schema.stream_id] = group
        except BaseException:
            handle.close()
            target.unlink(missing_ok=True)
            raise

        self._file = handle
        self._path = target
        self._schemas = by_id
        self._groups = groups
        self._rows_written = 0
        self._rows_by_stream = {stream_id: 0 for stream_id in by_id}
        self._state = RecorderState.RECORDING
        return target

    def append(self, stream_id: str, row: StreamRow) -> None:
        self.append_batch(stream_id, (row,))

    def append_batch(self, stream_id: str, rows: Iterable[StreamRow]) -> int:
        if not self.is_recording or self._file is None:
            raise RuntimeError("Recorder is not recording.")

        schema = self._schemas.get(stream_id)
        group = self._groups.get(stream_id)
        if schema is None or group is None:
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

        start = int(group["runtime_index"].shape[0])
        end = start + len(pending)
        for name in ("runtime_index", "host_monotonic_ns", "host_unix_ns"):
            group[name].resize((end,))
        group["values"].resize((end, len(schema.field_keys)))

        group["runtime_index"][start:end] = np.asarray(
            [row.runtime_index for row in pending], dtype=np.int64
        )
        group["host_monotonic_ns"][start:end] = np.asarray(
            [row.host_monotonic_ns for row in pending], dtype=np.int64
        )
        group["host_unix_ns"][start:end] = np.asarray(
            [row.host_unix_ns for row in pending], dtype=np.int64
        )
        group["values"][start:end, :] = np.asarray(
            [row.values for row in pending], dtype=np.float64
        )

        count = len(pending)
        self._rows_written += count
        self._rows_by_stream[stream_id] += count
        return count

    def flush(self) -> None:
        if self._file is not None:
            self._file.flush()

    def stop(self) -> Path | None:
        path = self._path
        handle = self._file
        self._file = None
        self._groups = {}
        self._schemas = {}
        self._state = RecorderState.STOPPED
        if handle is not None:
            try:
                handle.flush()
            finally:
                handle.close()
        return path

    def close(self) -> Path | None:
        return self.stop()

    def __enter__(self) -> "H5StreamRecorder":
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()
