"""Optional recorder tap around an existing RealtimeStreamStore.

This is a composition helper, not a replacement data store.  Existing code may
continue to pass a plain RealtimeStreamStore directly to device ingestors.
"""

from __future__ import annotations

from collections.abc import Iterable

from assembly.acquisition.runtime.stream_store import (
    RealtimeStreamStore,
    StreamRow,
    StreamSample,
)
from assembly.save.recorder import H5StreamRecorder


class StreamStoreTap:
    """Delegate commits to a store, optionally mirror committed rows to a recorder."""

    def __init__(self, store: RealtimeStreamStore, recorder: H5StreamRecorder) -> None:
        self.store = store
        self.recorder = recorder

    def schema(self, stream_id: str):
        return self.store.schema(stream_id)

    def append(
        self,
        stream_id: str,
        *,
        host_monotonic_ns: int,
        host_unix_ns: int,
        values: tuple[float, ...],
    ) -> StreamRow:
        row = self.store.append(
            stream_id,
            host_monotonic_ns=host_monotonic_ns,
            host_unix_ns=host_unix_ns,
            values=values,
        )
        if self.recorder.is_recording:
            self.recorder.append(stream_id, row)
        return row

    def append_batch(
        self,
        stream_id: str,
        samples: Iterable[StreamSample],
    ) -> tuple[StreamRow, ...]:
        rows = self.store.append_batch(stream_id, samples)
        if rows and self.recorder.is_recording:
            self.recorder.append_batch(stream_id, rows)
        return rows
