"""Common acquisition source contracts."""

from __future__ import annotations

import queue
import threading
from typing import Any, Protocol, TypeAlias

from fundamental.messages import WorkerEvent
from fundamental.streams import CaptureResumeState, StreamBlock, StreamSpec

SourceName: TypeAlias = str


class SourceWorker(Protocol):
    """Minimal shape expected by AcquisitionController-like lifecycle code."""

    data_queue: queue.Queue[StreamBlock]
    event_queue: queue.Queue[WorkerEvent]
    stop_event: threading.Event

    def start(self) -> None: ...

    def is_alive(self) -> bool: ...

    def join(self, timeout: float | None = None) -> None: ...


class AcquisitionSource(Protocol):
    """Configurable source that can create one acquisition worker."""

    name: SourceName
    display_name: str

    def display_text(self) -> str: ...

    def inspect_data(self) -> tuple[str, ...]: ...

    def stream_specs(self) -> tuple[StreamSpec, ...]: ...

    def capture_metadata(self) -> dict[str, Any]: ...

    def create_worker(
        self,
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        resume_state: CaptureResumeState = CaptureResumeState(),
    ) -> SourceWorker: ...
