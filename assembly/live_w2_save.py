"""Minimal 2x RunE W2 acquisition + independent SavePanel composition.

Acquisition starts when the app starts and remains independent from recording.
The SavePanel only gates which subsequently committed normalized rows are written
to HDF5.  Existing live_w2_plot.py is intentionally unchanged.
"""

from __future__ import annotations

import queue

import dearpygui.dearpygui as dpg

from assembly.acquisition.runtime.queue_pump import QueuePump
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore
from assembly.acquisition.runtime.worker_group import WorkerGroup
from assembly.acquisition.serial.w2_ingest import W2RecordIngestor, make_w2_stream_schema
from assembly.acquisition.serial.w2_worker import (
    SerialW2Worker,
    W2Record,
    W2SerialConfig,
    resolve_w2_configs,
)
from assembly.save.recorder import H5StreamRecorder
from assembly.save.save_panel import SavePanel
from assembly.save.store_tap import StreamStoreTap


# Edit only this composition-level configuration on the hardware machine.
W2_DEVICES: tuple[W2SerialConfig, ...] = (
    W2SerialConfig("COM9"),
    W2SerialConfig("COM11"),
)

STARTUP_TIMEOUT_S = 10.0
SHUTDOWN_TIMEOUT_S = 5.0
RETENTION_SECONDS = 35.0
RECORD_QUEUE_SIZE = 4096
MAX_RECORDS_PER_FRAME = 4096


def _validate_device_configs(configs: tuple[W2SerialConfig, ...]) -> None:
    if not configs:
        raise ValueError("Configure at least one W2 device.")
    ports = [config.port.casefold() for config in configs]
    if len(set(ports)) != len(ports):
        raise ValueError("Each W2 device must use a different serial port.")


def _print_group_state(group: WorkerGroup, workers: dict[str, SerialW2Worker]) -> None:
    print("\n[W2 workers]")
    for worker_id, worker in workers.items():
        print(
            f"{worker_id:16} "
            f"port={worker.config.port:8} "
            f"alive={worker.is_alive()} "
            f"startup={worker.startup_event.is_set()} "
            f"stopped={worker.stopped_event.is_set()} "
            f"packets={worker.packet_count} "
            f"error={worker.error!r}"
        )
    print("failures:", group.failures())


def main() -> None:
    requested_configs = W2_DEVICES
    _validate_device_configs(requested_configs)
    configs = resolve_w2_configs(requested_configs)
    print("Resolved W2 identities:")
    for config in configs:
        print(f"  {config.device_name} -> {config.device_id} @ {config.port}")

    schemas = tuple(
        make_w2_stream_schema(
            config.device_id,
            nominal_rate_hz=config.nominal_rate_hz,
        )
        for config in configs
    )
    store = RealtimeStreamStore(schemas, retention_seconds=RETENTION_SECONDS)
    recorder = H5StreamRecorder()
    tapped_store = StreamStoreTap(store, recorder)

    workers: dict[str, SerialW2Worker] = {}
    pumps: list[QueuePump[W2Record]] = []
    for config in configs:
        records: queue.Queue[W2Record] = queue.Queue(maxsize=RECORD_QUEUE_SIZE)
        worker_id = f"w2.{config.device_id}"
        worker = SerialW2Worker(config, records)
        # W2RecordIngestor intentionally needs only schema()/append_batch().
        # StreamStoreTap preserves that existing boundary and mirrors committed
        # rows to the recorder only while recording is active.
        ingestor = W2RecordIngestor(tapped_store, config.device_id)  # type: ignore[arg-type]
        workers[worker_id] = worker
        pumps.append(QueuePump(records, ingestor.ingest))

    group = WorkerGroup(workers)

    try:
        group.start()
        group.wait_ready(STARTUP_TIMEOUT_S)
    except BaseException:
        try:
            group.close(SHUTDOWN_TIMEOUT_S)
        finally:
            _print_group_state(group, workers)
        raise

    _print_group_state(group, workers)

    dpg.create_context()
    close_error: BaseException | None = None
    try:
        with dpg.window(label="W2 Save Dashboard", tag="assembly.w2_save.window"):
            dpg.add_text("Acquisition is running continuously; Save Start/Stop only controls recording.")
            dpg.add_separator()
            save_panel = SavePanel(
                recorder,
                schemas,
                default_directory="captures",
                default_filename="w2_capture.h5",
            )
            save_panel.build()

        dpg.create_viewport(
            title="RunE W2 Save Dashboard",
            width=760,
            height=330,
            x_pos=80,
            y_pos=80,
        )
        dpg.setup_dearpygui()
        dpg.show_viewport()

        while dpg.is_dearpygui_running():
            for pump in pumps:
                pump.drain(max_items=MAX_RECORDS_PER_FRAME)

            if group.failures():
                break

            save_panel.refresh()
            dpg.render_dearpygui_frame()
    finally:
        # Stop producers first.  Then drain already-produced records while the
        # recorder is still active, so closing the dashboard does not silently
        # discard queued rows from an active recording.
        try:
            group.close(SHUTDOWN_TIMEOUT_S)
        except BaseException as exc:
            close_error = exc
        finally:
            for pump in pumps:
                pump.drain(max_items=RECORD_QUEUE_SIZE)
            recorder.stop()
            dpg.destroy_context()
            _print_group_state(group, workers)

    failures = group.failures()
    if failures:
        failed_ids = ", ".join(failures)
        first_error = next(iter(failures.values()))
        raise RuntimeError(f"W2 acquisition failed: {failed_ids}.") from first_error
    if close_error is not None:
        raise close_error


if __name__ == "__main__":
    main()
