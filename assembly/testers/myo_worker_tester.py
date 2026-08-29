"""Executable Myo worker / ingest tester."""

from __future__ import annotations

import asyncio
import queue
import time
from collections import Counter
from dataclasses import dataclass
from typing import Literal

import tyro
from bleak import BleakScanner

from assembly.acquisition.BLE.myo_ingest import (
    MyoRecordIngestor,
    make_myo_stream_schemas,
)
from assembly.acquisition.BLE.myo_worker import MyoRecord, MyoWorker, start_myo
from assembly.acquisition.runtime.queue_pump import QueuePump
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore

TesterMode = Literal["raw", "ingest"]


@dataclass
class Config:
    """Hardware and observation settings for one physical Myo."""

    address: str
    device_id: str = "myo_test"
    tester_mode: TesterMode = "raw"
    capture_seconds: float = 10.0
    scan_timeout_s: float = 10.0
    connect_timeout_s: float = 20.0
    startup_timeout_s: float = 30.0
    shutdown_timeout_s: float = 5.0
    queue_size: int = 4096
    print_first: int = 5


async def _find_device(address: str, timeout_s: float):
    device = await BleakScanner.find_device_by_address(address, timeout=timeout_s)
    if device is None:
        raise RuntimeError(f"Could not find Myo at BLE address {address!r}.")
    return device


def _print_worker_state(worker: MyoWorker) -> None:
    print("\n[Myo worker]")
    print("alive             :", worker.is_alive())
    print("startup_event     :", worker.startup_event.is_set())
    print("stopped_event     :", worker.stopped_event.is_set())
    print("started_streaming :", worker.started_streaming)
    print("device_info       :", worker.device_info)
    print("error             :", repr(worker.error))


def _print_store_summary(store: RealtimeStreamStore) -> None:
    print("\n[Normalized streams]")
    print("total rows        :", store.row_count)
    for schema in store.schemas():
        snapshot = store.tail_samples(schema.stream_id, 5)
        print(f"{schema.stream_id}:")
        print("  schema          :", schema)
        print("  latest rows     :", snapshot.rows)


def run(config: Config) -> None:
    if not config.address.strip():
        raise ValueError("address must not be empty.")
    if not config.device_id.strip():
        raise ValueError("device_id must not be empty.")
    if config.capture_seconds <= 0:
        raise ValueError("capture_seconds must be positive.")
    if min(
        config.scan_timeout_s,
        config.connect_timeout_s,
        config.startup_timeout_s,
        config.shutdown_timeout_s,
    ) <= 0:
        raise ValueError("scan/connect/startup/shutdown timeouts must be positive.")
    if config.queue_size <= 0:
        raise ValueError("queue_size must be positive.")

    device = asyncio.run(_find_device(config.address.strip(), config.scan_timeout_s))
    print("[BLE device]")
    print("name              :", device.name)
    print("address           :", device.address)

    records: queue.Queue[MyoRecord] = queue.Queue(maxsize=config.queue_size)
    worker: MyoWorker | None = None

    store: RealtimeStreamStore | None = None
    pump: QueuePump[MyoRecord] | None = None
    if config.tester_mode == "ingest":
        schemas = make_myo_stream_schemas(config.device_id)
        store = RealtimeStreamStore(
            schemas,
            retention_seconds=max(30.0, config.capture_seconds + 5.0),
        )
        ingestor = MyoRecordIngestor(store, config.device_id)
        pump = QueuePump(records, ingestor.ingest)

    counts: Counter[str] = Counter()
    first_record: MyoRecord | None = None
    latest_record: MyoRecord | None = None
    consumed = 0

    try:
        worker = start_myo(
            device,
            records=records,
            connect_timeout_s=config.connect_timeout_s,
            startup_timeout_s=config.startup_timeout_s,
        )
        _print_worker_state(worker)
        print(f"\nCapturing for {config.capture_seconds:g}s in {config.tester_mode!r} mode...")
        deadline = time.monotonic() + config.capture_seconds

        while time.monotonic() < deadline:
            if config.tester_mode == "ingest":
                assert pump is not None
                drained = pump.drain(max_items=4096)
                consumed += drained
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
            stream = str(record.get("stream"))
            counts[stream] += 1
            if first_record is None:
                first_record = record
            latest_record = record
            if consumed <= config.print_first:
                print(stream.upper(), record)

        if config.tester_mode == "ingest":
            assert pump is not None
            consumed += pump.drain()

        print("\n[Capture summary]")
        print("records consumed   :", consumed)
        print("queue remains      :", records.qsize())
        if config.tester_mode == "raw":
            print("stream counts      :", dict(counts))
            print("first record       :", first_record)
            print("latest record      :", latest_record)
        else:
            assert store is not None
            _print_store_summary(store)

        _print_worker_state(worker)
        if worker.error is not None:
            raise RuntimeError("Myo worker failed while capturing.") from worker.error
    finally:
        if worker is not None:
            try:
                worker.close(config.shutdown_timeout_s)
            finally:
                _print_worker_state(worker)


def main() -> None:
    run(tyro.cli(Config))


if __name__ == "__main__":
    main()
