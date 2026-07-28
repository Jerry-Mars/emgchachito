"""Common acquisition source contracts."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeAlias

from fundamental.messages import WorkerEvent
from fundamental.streams import CaptureResumeState, StreamBlock, StreamSpec

SourceName: TypeAlias = str


class CaptureClock:
    """Thread-safe active-capture clock whose time does not advance while paused."""

    def __init__(self, offset_s: float = 0.0) -> None:
        self._elapsed_s = max(0.0, float(offset_s))
        self._running_since: float | None = None
        self._lock = threading.Lock()

    def resume(self) -> None:
        with self._lock:
            if self._running_since is None:
                self._running_since = time.monotonic()

    def pause(self) -> None:
        with self._lock:
            if self._running_since is None:
                return
            self._elapsed_s += time.monotonic() - self._running_since
            self._running_since = None

    def now(self) -> float:
        with self._lock:
            elapsed = self._elapsed_s
            if self._running_since is not None:
                elapsed += time.monotonic() - self._running_since
            return elapsed


@dataclass
class WorkerControl:
    """Shared stop/run gates and logical clock for coordinated device workers."""

    stop_event: threading.Event
    capture_event: threading.Event = field(default_factory=threading.Event)
    clock: CaptureClock = field(default_factory=CaptureClock)

    @classmethod
    def running(
        cls,
        stop_event: threading.Event,
        *,
        offset_s: float = 0.0,
    ) -> "WorkerControl":
        control = cls(stop_event=stop_event, clock=CaptureClock(offset_s))
        control.clock.resume()
        control.capture_event.set()
        return control


class SourceWorker(Protocol):
    """Minimal shape expected by AcquisitionController-like lifecycle code."""

    data_queue: queue.Queue[StreamBlock]
    event_queue: queue.Queue[WorkerEvent]
    stop_event: threading.Event

    def start(self) -> None: ...

    def is_alive(self) -> bool: ...

    def join(self, timeout: float | None = None) -> None: ...


class ManagedSourceWorker(SourceWorker, Protocol):
    """Worker that connects first and waits for a shared capture gate."""

    ready_event: threading.Event


class SourceWorkerGroup:
    """Expose several independently connected workers through one worker contract."""

    def __init__(
        self,
        workers: tuple[SourceWorker, ...],
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
    ) -> None:
        self.workers = workers
        self.data_queue = data_queue
        self.event_queue = event_queue
        self.stop_event = stop_event
        self.ready_event = threading.Event()
        self._ready_monitor: threading.Thread | None = None

    def start(self) -> None:
        started: list[SourceWorker] = []
        try:
            for worker in self.workers:
                worker.start()
                started.append(worker)
        except Exception:
            self.stop_event.set()
            for worker in started:
                worker.join(timeout=1.0)
            raise

        self._ready_monitor = threading.Thread(
            target=self._monitor_ready,
            name="SourceWorkerGroup-ready",
            daemon=True,
        )
        self._ready_monitor.start()

    def _monitor_ready(self) -> None:
        while not self.stop_event.wait(0.01):
            if all(_worker_ready(worker) for worker in self.workers):
                self.ready_event.set()
                return
            if any(not worker.is_alive() for worker in self.workers):
                return

    def is_alive(self) -> bool:
        return any(worker.is_alive() for worker in self.workers)

    def join(self, timeout: float | None = None) -> None:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        for worker in self.workers:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            worker.join(timeout=remaining)


def _worker_ready(worker: SourceWorker) -> bool:
    ready_event = getattr(worker, "ready_event", None)
    return bool(ready_event is None or ready_event.is_set())


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
