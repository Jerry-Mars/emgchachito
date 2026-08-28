"""Executable RunE W2 worker / ingest tester."""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from typing import Literal

import tyro

from assembly.acquisition.runtime.queue_pump import QueuePump
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore
from assembly.acquisition.serial.w2_ingest import W2RecordIngestor, make_w2_stream_schema
from assembly.acquisition.serial.w2_worker import SerialW2Worker, W2Record, W2SerialConfig

TesterMode = Literal["raw", "ingest"]


@dataclass
class Config:
    """Hardware and observation settings for one physical W2."""

    port: str
    device_id: str = "w2_test"
    mode: Literal["emg_raw", "emg_rms", "eeg_raw"] = "emg_raw"
    tester_mode: TesterMode = "raw"
    capture_seconds: float = 10.0
    nominal_rate_hz: float = 1000.0
    baud_rate: int = 256000
    serial_timeout_s: float = 0.05
    startup_timeout_s: float = 5.0
    shutdown_timeout_s: float = 5.0
    queue_size: int = 4096
    print_first: int = 5


def _print_worker_state(worker: SerialW2Worker) -> None:
    print("\n[W2 worker]")
    print("device_id         :", worker.config.device_id)
    print("port              :", worker.config.port)
    print("alive             :", worker.is_alive())
    print("startup_event     :", worker.startup_event.is_set())
    print("stopped_event     :", worker.stopped_event.is_set())
    print("started_collecting:", worker.started_collecting)
    print("read_count        :", worker.read_count)
    print("packet_count      :", worker.packet_count)
    print("error             :", repr(worker.error))


def _print_store_summary(store: RealtimeStreamStore, stream_id: str) -> None:
    snapshot = store.tail_samples(stream_id, 5)
    print("\n[Normalized stream]")
    print("stream_id         :", stream_id)
    print("schema            :", snapshot.schema)
    print("total rows        :", store.row_count)
    print("latest rows       :", snapshot.rows)


def run(config: Config) -> None:
    if config.capture_seconds <= 0:
        raise ValueError("capture_seconds must be positive.")
    if config.startup_timeout_s <= 0 or config.shutdown_timeout_s <= 0:
        raise ValueError("startup/shutdown timeouts must be positive.")
    if config.queue_size <= 0:
        raise ValueError("queue_size must be positive.")

    device = W2SerialConfig(
        device_id=config.device_id,
        port=config.port,
        mode=config.mode,
        nominal_rate_hz=config.nominal_rate_hz,
        baud_rate=config.baud_rate,
        timeout_s=config.serial_timeout_s,
    )
    records: queue.Queue[W2Record] = queue.Queue(maxsize=config.queue_size)
    worker = SerialW2Worker(device, records)

    store: RealtimeStreamStore | None = None
    pump: QueuePump[W2Record] | None = None
    stream_id = make_w2_stream_schema(
        config.device_id,
        nominal_rate_hz=config.nominal_rate_hz,
    ).stream_id

    if config.tester_mode == "ingest":
        schema = make_w2_stream_schema(
            config.device_id,
            nominal_rate_hz=config.nominal_rate_hz,
        )
        store = RealtimeStreamStore((schema,), retention_seconds=max(30.0, config.capture_seconds + 5.0))
        ingestor = W2RecordIngestor(store, config.device_id)
        pump = QueuePump(records, ingestor.ingest)

    seen = 0
    first_record: W2Record | None = None
    latest_record: W2Record | None = None

    try:
        worker.start()
        if not worker.startup_event.wait(config.startup_timeout_s):
            raise TimeoutError("W2 worker startup did not resolve before timeout.")
        if worker.error is not None:
            raise RuntimeError("W2 worker failed during startup.") from worker.error
        if not worker.is_alive() or not worker.started_collecting:
            raise RuntimeError("W2 worker did not reach collecting state.")

        _print_worker_state(worker)
        print(f"\nCapturing for {config.capture_seconds:g}s in {config.tester_mode!r} mode...")
        deadline = time.monotonic() + config.capture_seconds

        while time.monotonic() < deadline:
            if config.tester_mode == "ingest":
                assert pump is not None
                seen += pump.drain(max_items=4096)
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

            seen += 1
            if first_record is None:
                first_record = record
            latest_record = record
            if seen <= config.print_first:
                print("RAW", record)

        if config.tester_mode == "ingest":
            assert pump is not None
            seen += pump.drain()

        print("\n[Capture summary]")
        print("records consumed   :", seen)
        print("queue remains      :", records.qsize())
        if config.tester_mode == "raw":
            print("first record       :", first_record)
            print("latest record      :", latest_record)
        else:
            assert store is not None
            _print_store_summary(store, stream_id)

        _print_worker_state(worker)
        if worker.error is not None:
            raise RuntimeError("W2 worker failed while capturing.") from worker.error
    finally:
        try:
            worker.close(config.shutdown_timeout_s)
        finally:
            _print_worker_state(worker)


def main() -> None:
    run(tyro.cli(Config))


if __name__ == "__main__":
    main()
