"""Live multi-device RunE W2 serial -> shared realtime store -> existing Plot."""

from __future__ import annotations

import queue

import dearpygui.dearpygui as dpg

from assembly.acquisition.runtime.queue_pump import QueuePump
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore
from assembly.acquisition.runtime.worker_group import WorkerGroup
from assembly.acquisition.serial.w2_ingest import (
    W2RecordIngestor,
    make_w2_stream_schema,
    w2_stream_id,
)
from assembly.acquisition.serial.w2_worker import (
    ResolvedW2SerialConfig,
    SerialW2Worker,
    W2Record,
    W2SerialConfig,
    resolve_w2_configs,
)
from assembly.plot.models import SeriesSpec
from assembly.plot.plot_window import create_plot_window
from assembly.plot.realtime_provider import BufferedPlotProvider


# ======================================================================
# HARDWARE CONFIGURATION
# ======================================================================

# Edit only these rows for the physical W2 devices on the hardware machine.
# The defaults mirror the first two serial W2 rows used by fundamental.
W2_DEVICES: tuple[W2SerialConfig, ...] = (
    W2SerialConfig("COM9"),
    W2SerialConfig("COM11"),
)

STARTUP_TIMEOUT_S = 10.0
SHUTDOWN_TIMEOUT_S = 5.0


# ======================================================================
# RUNTIME CONFIGURATION
# ======================================================================

RETENTION_SECONDS = 35.0
RECORD_QUEUE_SIZE = 4096
MAX_RECORDS_PER_FRAME = 4096


def _validate_device_configs(configs: tuple[W2SerialConfig, ...]) -> None:
    if not configs:
        raise ValueError("Configure at least one W2 device.")
    ports = [config.port.casefold() for config in configs]
    if len(set(ports)) != len(ports):
        raise ValueError("Each W2 device must use a different serial port.")


def _plot_spec(config: ResolvedW2SerialConfig) -> SeriesSpec:
    stream_id = w2_stream_id(config.device_id)
    signal_kind = {
        "emg_raw": "emg",
        "emg_rms": "generic",
        "eeg_raw": "eeg",
    }[config.mode]
    return SeriesSpec(
        series_id=f"{stream_id}/value",
        stream_id=stream_id,
        field_key="value",
        label=f"W2 {config.device_id}",
        unit="code",
        signal_kind=signal_kind,  # type: ignore[arg-type]
        default_plot=True,
        fixed_range=None,
    )


def _print_group_state(
    group: WorkerGroup,
    workers: dict[str, SerialW2Worker],
) -> None:
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
    store = RealtimeStreamStore(
        schemas,
        retention_seconds=RETENTION_SECONDS,
    )

    workers: dict[str, SerialW2Worker] = {}
    pumps: list[QueuePump[W2Record]] = []

    for config in configs:
        records: queue.Queue[W2Record] = queue.Queue(maxsize=RECORD_QUEUE_SIZE)
        worker_id = f"w2.{config.device_id}"
        worker = SerialW2Worker(config, records)
        ingestor = W2RecordIngestor(store, config.device_id)

        workers[worker_id] = worker
        pumps.append(QueuePump(records, ingestor.ingest))

    group = WorkerGroup(workers)
    provider = BufferedPlotProvider(
        store,
        tuple(_plot_spec(config) for config in configs),
    )

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
        plot_state = create_plot_window(provider)
        dpg.create_viewport(
            title="Live RunE W2 Multi-Device Plot",
            width=1280,
            height=900,
            x_pos=80,
            y_pos=80,
        )
        dpg.setup_dearpygui()
        dpg.show_viewport()

        while dpg.is_dearpygui_running():
            for pump in pumps:
                pump.drain(max_items=MAX_RECORDS_PER_FRAME)

            # WorkerGroup reports the fact; this app chooses fail-fast because
            # every configured W2 is a required acquisition channel here.
            if group.failures():
                break

            plot_state.refresh(provider)
            dpg.render_dearpygui_frame()
    finally:
        try:
            group.close(SHUTDOWN_TIMEOUT_S)
        except BaseException as exc:
            close_error = exc
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
