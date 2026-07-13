"""Common acquisition source contracts."""

from __future__ import annotations

import queue
import threading
from typing import Literal, Protocol

from fundamental.messages import SampleBatch, WorkerEvent

SourceName = Literal["serial_ads1299", "ble_w2"]


class SourceWorker(Protocol):
    """Minimal shape expected by AcquisitionController-like lifecycle code."""

    data_queue: queue.Queue[SampleBatch]
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

    def create_worker(
        self,
        data_queue: queue.Queue[SampleBatch],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        timestamp_offset_s: float = 0.0,
        expected_counter: int | None = None,
    ) -> SourceWorker: ...
