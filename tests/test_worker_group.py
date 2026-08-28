from __future__ import annotations

import threading
import time
import unittest

from assembly.acquisition.runtime.worker_group import WorkerGroup


class FakeManagedWorker(threading.Thread):
    def __init__(
        self,
        *,
        ready_gate: threading.Event | None = None,
        startup_error: BaseException | None = None,
        cleanup_error: BaseException | None = None,
        stop_delay_s: float = 0.0,
    ) -> None:
        super().__init__(daemon=True)
        self.startup_event = threading.Event()
        self.stopped_event = threading.Event()
        self.error: BaseException | None = None
        self.stop_event = threading.Event()
        self.ready_gate = ready_gate
        self.startup_error = startup_error
        self.cleanup_error = cleanup_error
        self.stop_delay_s = stop_delay_s
        self.entered_run = threading.Event()
        self.runtime_failure: BaseException | None = None

    def request_stop(self) -> None:
        self.stop_event.set()

    def fail_runtime(self, exc: BaseException) -> None:
        self.runtime_failure = exc

    def run(self) -> None:
        self.entered_run.set()
        try:
            if self.startup_error is not None:
                raise self.startup_error

            while self.ready_gate is not None and not self.ready_gate.is_set():
                if self.stop_event.wait(0.005):
                    return

            if self.stop_event.is_set():
                return

            self.startup_event.set()

            while not self.stop_event.wait(0.005):
                if self.runtime_failure is not None:
                    raise self.runtime_failure

            if self.stop_delay_s:
                time.sleep(self.stop_delay_s)
            if self.cleanup_error is not None:
                raise self.cleanup_error
        except BaseException as exc:
            self.error = exc
        finally:
            self.startup_event.set()
            self.stopped_event.set()


class WorkerGroupTests(unittest.TestCase):
    def test_rejects_empty_group_and_empty_worker_id(self) -> None:
        with self.assertRaises(ValueError):
            WorkerGroup({})
        with self.assertRaises(ValueError):
            WorkerGroup({" ": FakeManagedWorker()})

    def test_start_is_non_blocking_and_workers_start_concurrently(self) -> None:
        left_gate = threading.Event()
        right_gate = threading.Event()
        left = FakeManagedWorker(ready_gate=left_gate)
        right = FakeManagedWorker(ready_gate=right_gate)
        group = WorkerGroup({"w2.left": left, "w2.right": right})

        group.start()
        self.assertTrue(left.entered_run.wait(0.5))
        self.assertTrue(right.entered_run.wait(0.5))
        self.assertEqual(set(group.pending_worker_ids()), {"w2.left", "w2.right"})

        left_gate.set()
        right_gate.set()
        group.wait_ready(0.5)
        self.assertTrue(group.all_ready())
        group.close(0.5)

    def test_synchronous_start_failure_stops_started_workers_without_waiting_unstarted(self) -> None:
        first = FakeManagedWorker()

        class StartFailureWorker(FakeManagedWorker):
            def start(self) -> None:
                raise OSError("thread start failed")

        second = StartFailureWorker()
        group = WorkerGroup({"first": first, "second": second})

        with self.assertRaisesRegex(OSError, "thread start failed"):
            group.start()

        self.assertTrue(first.stop_event.is_set())
        group.close(0.5)
        self.assertTrue(first.stopped_event.is_set())
        self.assertFalse(second.stopped_event.is_set())

    def test_wait_ready_reports_startup_failure_without_stopping_other_workers(self) -> None:
        survivor = FakeManagedWorker()
        failed = FakeManagedWorker(startup_error=ConnectionError("no device"))
        group = WorkerGroup({"survivor": survivor, "failed": failed})

        group.start()
        with self.assertRaisesRegex(RuntimeError, "failed"):
            group.wait_ready(0.5)

        self.assertIn("failed", group.failures())
        self.assertTrue(survivor.is_alive())
        group.close(0.5)

    def test_wait_ready_times_out_with_pending_worker_id(self) -> None:
        gate = threading.Event()
        worker = FakeManagedWorker(ready_gate=gate)
        group = WorkerGroup({"slow": worker})

        group.start()
        with self.assertRaisesRegex(TimeoutError, "slow"):
            group.wait_ready(0.03)
        group.close(0.5)

    def test_runtime_failure_is_reported_without_automatic_fail_fast(self) -> None:
        left = FakeManagedWorker()
        right = FakeManagedWorker()
        group = WorkerGroup({"left": left, "right": right})
        group.start()
        group.wait_ready(0.5)

        right.fail_runtime(ConnectionError("lost"))
        self.assertTrue(right.stopped_event.wait(0.5))

        self.assertIn("right", group.failures())
        self.assertTrue(left.is_alive())
        group.close(0.5)

    def test_close_broadcasts_stop_then_waits_for_all_workers(self) -> None:
        left = FakeManagedWorker(stop_delay_s=0.03)
        right = FakeManagedWorker(stop_delay_s=0.03)
        group = WorkerGroup({"left": left, "right": right})
        group.start()
        group.wait_ready(0.5)

        started = time.monotonic()
        group.close(0.5)
        elapsed = time.monotonic() - started

        self.assertTrue(left.stop_event.is_set())
        self.assertTrue(right.stop_event.is_set())
        self.assertTrue(left.stopped_event.is_set())
        self.assertTrue(right.stopped_event.is_set())
        self.assertLess(elapsed, 0.055)

    def test_close_uses_one_group_timeout_budget(self) -> None:
        worker = FakeManagedWorker(stop_delay_s=0.15)
        group = WorkerGroup({"slow": worker})
        group.start()
        group.wait_ready(0.5)

        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "slow"):
            group.close(0.03)
        self.assertLess(time.monotonic() - started, 0.08)

        worker.stopped_event.wait(0.5)
        group.close(0.5)

    def test_cleanup_failure_stays_in_failures_but_close_completes(self) -> None:
        worker = FakeManagedWorker(cleanup_error=OSError("close failed"))
        group = WorkerGroup({"worker": worker})
        group.start()
        group.wait_ready(0.5)

        group.close(0.5)

        self.assertIn("worker", group.failures())
        self.assertIsInstance(group.failures()["worker"], OSError)

    def test_close_is_resource_safe_when_repeated(self) -> None:
        worker = FakeManagedWorker()
        group = WorkerGroup({"worker": worker})
        group.start()
        group.wait_ready(0.5)
        group.close(0.5)
        group.close(0.5)
        self.assertFalse(group.is_alive())

    def test_close_before_start_is_safe_and_terminal(self) -> None:
        worker = FakeManagedWorker()
        group = WorkerGroup({"worker": worker})
        group.close(0.5)
        self.assertFalse(group.is_alive())
        with self.assertRaises(RuntimeError):
            group.start()

    def test_start_twice_is_rejected(self) -> None:
        worker = FakeManagedWorker()
        group = WorkerGroup({"worker": worker})
        group.start()
        with self.assertRaises(RuntimeError):
            group.start()
        group.close(0.5)


if __name__ == "__main__":
    unittest.main()
