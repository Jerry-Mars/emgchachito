"""Executable BWT901BLE worker / ingest tester."""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from typing import Literal

import tyro

from assembly.acquisition.BLE.bwt901_ingest import (
    BWT901RecordIngestor,
    bwt901_stream_id,
    make_bwt901_stream_schema,
)
from assembly.acquisition.BLE.bwt901_worker import (
    BWT901BLEConfig,
    BWT901BLEWorker,
    BWT901Record,
)
from assembly.acquisition.runtime.queue_pump import QueuePump
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore

TesterMode = Literal["raw", "ingest"]


@dataclass
class Config:
    """Hardware and observation settings for one physical BWT901BLE."""

    device_id: str = "imu_test"
    address: str = ""
    name_filter: str = "WT"
    tester_mode: TesterMode = "raw"
    capture_seconds: float = 10.0
    scan_timeout_s: float = 10.0
    startup_timeout_s: float = 15.0
    shutdown_timeout_s: float = 5.0
    queue_size: int = 2048
    print_first: int = 5
    windows_address_type: Literal["public", "random"] | None = None


def _print_worker_state(worker: BWT901BLEWorker) -> None:
    print("\n[BWT901 worker]")
    print("device_id          :", worker.config.device_id)
    print("resolved_name      :", worker.resolved_name)
    print("resolved_address   :", worker.resolved_address)
    print("alive              :", worker.is_alive())
    print("startup_event      :", worker.startup_event.is_set())
    print("stopped_event      :", worker.stopped_event.is_set())
    print("notifications      :", worker.notification_count)
    print("decoded_frames     :", worker.decoded_frame_count)
    print("error              :", repr(worker.error))


def _print_store_summary(store: RealtimeStreamStore, stream_id: str) -> None:
    snapshot = store.tail_samples(stream_id, 5)
    print("\n[Normalized stream]")
    print("stream_id          :", stream_id)
    print("schema             :", snapshot.schema)
    print("total rows         :", store.row_count)
    print("latest rows        :", snapshot.rows)


def run(config: Config) -> None:
    if config.capture_seconds <= 0:
        raise ValueError("capture_seconds must be positive.")
    if config.startup_timeout_s <= 0 or config.shutdown_timeout_s <= 0:
        raise ValueError("startup/shutdown timeouts must be positive.")
    if config.queue_size <= 0:
        raise ValueError("queue_size must be positive.")

    device = BWT901BLEConfig(
        device_id=config.device_id,
        address=config.address,
        name_filter=config.name_filter,
        scan_timeout_s=config.scan_timeout_s,
        windows_address_type=config.windows_address_type,
    )
    records: queue.Queue[BWT901Record] = queue.Queue(maxsize=config.queue_size)
    worker = BWT901BLEWorker(device, records)

    store: RealtimeStreamStore | None = None
    pump: QueuePump[BWT901Record] | None = None
    stream_id = bwt901_stream_id(config.device_id)
    if config.tester_mode == "ingest":
        schema = make_bwt901_stream_schema(config.device_id)
        store = RealtimeStreamStore((schema,), retention_seconds=max(30.0, config.capture_seconds + 5.0))
        ingestor = BWT901RecordIngestor(store, config.device_id)
        pump = QueuePump(records, ingestor.ingest)

    consumed = 0
    first_record: BWT901Record | None = None
    latest_record: BWT901Record | None = None

    try:
        worker.start()
        if not worker.startup_event.wait(config.startup_timeout_s):
            raise TimeoutError("BWT901 worker startup did not resolve before timeout.")
        if worker.error is not None:
            raise RuntimeError("BWT901 worker failed during startup.") from worker.error
        if not worker.is_alive():
            raise RuntimeError("BWT901 worker stopped before capture began.")

        _print_worker_state(worker)
        print(f"\nCapturing for {config.capture_seconds:g}s in {config.tester_mode!r} mode...")
        deadline = time.monotonic() + config.capture_seconds

        while time.monotonic() < deadline:
            if config.tester_mode == "ingest":
                assert pump is not None
                consumed += pump.drain(max_items=4096)
                if worker.stopped_event.is_set():
                    break
                time.sleep(0.01)
                continue

            try:
                record = records.get(timeout=0.25)
            except queue.Empty:
                if worker.stopped_event.is_set():
                    break
                continue

            consumed += 1
            if first_record is None:
                first_record = record
            latest_record = record
            if consumed <= config.print_first:
                print("RAW", record)

        if config.tester_mode == "ingest":
            assert pump is not None
            consumed += pump.drain()

        print("\n[Capture summary]")
        print("records consumed    :", consumed)
        print("queue remains       :", records.qsize())
        if config.tester_mode == "raw":
            print("first record        :", first_record)
            print("latest record       :", latest_record)
        else:
            assert store is not None
            _print_store_summary(store, stream_id)

        _print_worker_state(worker)
        if worker.error is not None:
            raise RuntimeError("BWT901 worker failed while capturing.") from worker.error
    finally:
        try:
            worker.close(config.shutdown_timeout_s)
        finally:
            _print_worker_state(worker)


def main() -> None:
    run(tyro.cli(Config))


if __name__ == "__main__":
    main()
