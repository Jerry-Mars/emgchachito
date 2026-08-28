"""Minimal lifecycle aggregation for independently managed acquisition workers."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Protocol


class ManagedWorker(Protocol):
    """Small lifecycle surface required by :class:`WorkerGroup`.

    ``startup_event`` means the startup attempt has resolved, not necessarily
    succeeded.  Startup is ready only when the event is set, ``error`` is
    ``None``, the worker has not stopped, and it is still alive.

    ``stopped_event`` means the worker execution has ended after completing the
    cleanup owned by that worker.
    """

    startup_event: threading.Event
    stopped_event: threading.Event
    error: BaseException | None

    def start(self) -> None: ...

    def is_alive(self) -> bool: ...

    def request_stop(self) -> None: ...


class WorkerGroup:
    """Coordinate lifecycle operations without participating in the data plane.

    The group deliberately knows nothing about queues, stream schemas, stores,
    plotting, stimulus or device protocols.  Worker names belong to this
    orchestration binding and are not required to be part of the worker itself.
    """

    def __init__(self, workers: Mapping[str, ManagedWorker]) -> None:
        if not workers:
            raise ValueError("WorkerGroup requires at least one worker.")

        normalized: dict[str, ManagedWorker] = {}
        for worker_id, worker in workers.items():
            name = str(worker_id).strip()
            if not name:
                raise ValueError("WorkerGroup worker IDs must not be empty.")
            if name in normalized:
                raise ValueError(f"Duplicate WorkerGroup worker ID: {name!r}")
            normalized[name] = worker

        self._workers = normalized
        self._started_worker_ids: list[str] = []
        self._started = False
        self._closed = False

    def start(self) -> None:
        """Start every worker without waiting for hardware startup to finish."""

        if self._closed:
            raise RuntimeError("WorkerGroup is closed and cannot be started.")
        if self._started:
            raise RuntimeError("WorkerGroup has already been started.")

        self._started = True
        try:
            for worker_id, worker in self._workers.items():
                worker.start()
                self._started_worker_ids.append(worker_id)
        except BaseException as start_error:
            # Only workers whose start() call returned successfully can be
            # awaited later.  Ask those workers to stop, then let the caller
            # decide whether/when to call close().
            rollback_errors: list[BaseException] = []
            for worker_id in self._started_worker_ids:
                try:
                    self._workers[worker_id].request_stop()
                except BaseException as exc:
                    exc.add_note(f"WorkerGroup startup rollback worker: {worker_id}")
                    rollback_errors.append(exc)
            if rollback_errors:
                raise BaseExceptionGroup(
                    "WorkerGroup start and rollback failed.",
                    [start_error, *rollback_errors],
                )
            raise

    def all_ready(self) -> bool:
        """Return whether every worker has successfully completed startup."""

        return all(self._worker_ready(worker) for worker in self._workers.values())

    def pending_worker_ids(self) -> tuple[str, ...]:
        """Workers whose startup attempt has not yet resolved."""

        return tuple(
            worker_id
            for worker_id, worker in self._workers.items()
            if not worker.startup_event.is_set() and not worker.stopped_event.is_set()
        )

    def failures(self) -> dict[str, BaseException]:
        """Return terminal worker failures without applying any fail-fast policy."""

        return {
            worker_id: worker.error
            for worker_id, worker in self._workers.items()
            if worker.error is not None
        }

    def is_alive(self) -> bool:
        return any(worker.is_alive() for worker in self._workers.values())

    def wait_ready(self, timeout_s: float) -> None:
        """Wait for all workers to become ready, fail, stop early, or time out.

        This method has no cleanup side effect.  Callers that want transactional
        startup should call ``close()`` if this method raises.
        """

        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive.")
        if not self._started:
            raise RuntimeError("WorkerGroup must be started before wait_ready().")

        deadline = time.monotonic() + float(timeout_s)
        while True:
            if self.all_ready():
                return

            failures = self.failures()
            if failures:
                failed_ids = ", ".join(failures)
                first_error = next(iter(failures.values()))
                raise RuntimeError(
                    f"Worker startup failed: {failed_ids}."
                ) from first_error

            stopped_before_ready = tuple(
                worker_id
                for worker_id, worker in self._workers.items()
                if worker.stopped_event.is_set() and not self._worker_ready(worker)
            )
            if stopped_before_ready:
                raise RuntimeError(
                    "Workers stopped before startup completed: "
                    + ", ".join(stopped_before_ready)
                    + "."
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                pending = self.pending_worker_ids()
                details = ", ".join(pending) if pending else "unresolved workers"
                raise TimeoutError(f"WorkerGroup startup timed out: {details}.")

            time.sleep(min(0.01, remaining))

    def request_stop(self) -> None:
        """Broadcast a non-blocking stop request to every worker that was started."""

        errors: list[BaseException] = []
        for worker_id in self._started_worker_ids:
            worker = self._workers[worker_id]
            try:
                worker.request_stop()
            except BaseException as exc:
                exc.add_note(f"WorkerGroup request_stop worker: {worker_id}")
                errors.append(exc)

        if errors:
            raise BaseExceptionGroup("WorkerGroup stop request failed.", errors)

    def close(self, timeout_s: float = 5.0) -> None:
        """Broadcast shutdown and wait for all workers using one timeout budget.

        Worker terminal failures remain available through ``failures()``.  The
        close operation itself only fails when a stop request cannot be issued
        or workers cannot finish before the shared timeout budget expires.
        """

        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive.")

        if self._closed and not self.is_alive():
            return
        if not self._started:
            self._closed = True
            return

        stop_error: BaseException | None = None
        try:
            self.request_stop()
        except BaseException as exc:
            stop_error = exc

        deadline = time.monotonic() + float(timeout_s)
        for worker_id in self._started_worker_ids:
            remaining = max(0.0, deadline - time.monotonic())
            self._workers[worker_id].stopped_event.wait(remaining)

        while self.is_alive() and time.monotonic() < deadline:
            time.sleep(min(0.005, max(0.0, deadline - time.monotonic())))

        unfinished = tuple(
            worker_id
            for worker_id in self._started_worker_ids
            if (
                not self._workers[worker_id].stopped_event.is_set()
                or self._workers[worker_id].is_alive()
            )
        )

        self._closed = not unfinished

        timeout_error: BaseException | None = None
        if unfinished:
            timeout_error = TimeoutError(
                "WorkerGroup shutdown timed out: " + ", ".join(unfinished) + "."
            )

        if stop_error is not None and timeout_error is not None:
            raise BaseExceptionGroup(
                "WorkerGroup shutdown failed.", [stop_error, timeout_error]
            )
        if stop_error is not None:
            raise stop_error
        if timeout_error is not None:
            raise timeout_error

    @staticmethod
    def _worker_ready(worker: ManagedWorker) -> bool:
        return bool(
            worker.startup_event.is_set()
            and worker.error is None
            and not worker.stopped_event.is_set()
            and worker.is_alive()
        )
