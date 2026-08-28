"""Live BWT901BLE -> shared realtime store -> existing Plot."""

from __future__ import annotations

import queue

import dearpygui.dearpygui as dpg

from assembly.acquisition.BLE.bwt901_ingest import (
    BWT901_FIELD_KEYS,
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
from assembly.acquisition.runtime.worker_group import WorkerGroup
from assembly.plot.models import SeriesSpec
from assembly.plot.plot_window import create_plot_window
from assembly.plot.realtime_provider import BufferedPlotProvider


# ======================================================================
# HARDWARE CONFIGURATION
# ======================================================================

# A single device may use address="" and a name filter.  For multiple devices,
# configure an explicit unique BLE address for each row so workers cannot select
# the same physical IMU.
BWT901_DEVICES: tuple[BWT901BLEConfig, ...] = (
    BWT901BLEConfig(
        "imu_1",
        address="E9:34:17:08:9F:4A",
        name_filter="WT901BLE67",
    ),
)

STARTUP_TIMEOUT_S = 15.0
SHUTDOWN_TIMEOUT_S = 5.0


# ======================================================================
# RUNTIME CONFIGURATION
# ======================================================================

RETENTION_SECONDS = 35.0
RECORD_QUEUE_SIZE = 2048
MAX_RECORDS_PER_FRAME = 4096


_FIELD_PLOT_CONFIG = {
    "acc_x_g": ("Accel X", "g", "acceleration", (-16.0, 16.0), True),
    "acc_y_g": ("Accel Y", "g", "acceleration", (-16.0, 16.0), True),
    "acc_z_g": ("Accel Z", "g", "acceleration", (-16.0, 16.0), True),
    "gyro_x_dps": ("Gyro X", "deg/s", "angular_velocity", (-2000.0, 2000.0), False),
    "gyro_y_dps": ("Gyro Y", "deg/s", "angular_velocity", (-2000.0, 2000.0), False),
    "gyro_z_dps": ("Gyro Z", "deg/s", "angular_velocity", (-2000.0, 2000.0), False),
    "angle_x_deg": ("Angle X", "deg", "generic", (-180.0, 180.0), False),
    "angle_y_deg": ("Angle Y", "deg", "generic", (-180.0, 180.0), False),
    "angle_z_deg": ("Angle Z", "deg", "generic", (-180.0, 180.0), False),
}


def _validate_device_configs(configs: tuple[BWT901BLEConfig, ...]) -> None:
    if not configs:
        raise ValueError("Configure at least one BWT901 device.")

    device_ids = [config.device_id.casefold() for config in configs]
    if len(set(device_ids)) != len(device_ids):
        raise ValueError("BWT901 device IDs must be unique.")

    if len(configs) > 1:
        addresses = [config.address.casefold() for config in configs]
        if any(not address for address in addresses):
            raise ValueError(
                "Multiple BWT901 devices require explicit BLE addresses."
            )
        if len(set(addresses)) != len(addresses):
            raise ValueError("BWT901 BLE addresses must be unique.")


def _plot_specs(configs: tuple[BWT901BLEConfig, ...]) -> tuple[SeriesSpec, ...]:
    specs: list[SeriesSpec] = []
    for config in configs:
        stream_id = bwt901_stream_id(config.device_id)
        for field_key in BWT901_FIELD_KEYS:
            label, unit, signal_kind, fixed_range, default_plot = _FIELD_PLOT_CONFIG[field_key]
            specs.append(
                SeriesSpec(
                    series_id=f"{stream_id}/{field_key}",
                    stream_id=stream_id,
                    field_key=field_key,
                    label=f"BWT901 {config.device_id} {label}",
                    unit=unit,
                    signal_kind=signal_kind,  # type: ignore[arg-type]
                    default_plot=default_plot,
                    fixed_range=fixed_range,
                )
            )
    return tuple(specs)


def _print_group_state(
    group: WorkerGroup,
    workers: dict[str, BWT901BLEWorker],
) -> None:
    print("\n[BWT901 workers]")
    for worker_id, worker in workers.items():
        print(
            f"{worker_id:20} "
            f"address={worker.resolved_address or worker.config.address or '-':18} "
            f"alive={worker.is_alive()} "
            f"startup={worker.startup_event.is_set()} "
            f"stopped={worker.stopped_event.is_set()} "
            f"notifications={worker.notification_count} "
            f"frames={worker.decoded_frame_count} "
            f"error={worker.error!r}"
        )
    print("failures:", group.failures())


def main() -> None:
    configs = BWT901_DEVICES
    _validate_device_configs(configs)

    schemas = tuple(make_bwt901_stream_schema(config.device_id) for config in configs)
    store = RealtimeStreamStore(
        schemas,
        retention_seconds=RETENTION_SECONDS,
    )

    workers: dict[str, BWT901BLEWorker] = {}
    pumps: list[QueuePump[BWT901Record]] = []

    for config in configs:
        records: queue.Queue[BWT901Record] = queue.Queue(maxsize=RECORD_QUEUE_SIZE)
        worker_id = f"bwt901.{config.device_id}"
        worker = BWT901BLEWorker(config, records)
        ingestor = BWT901RecordIngestor(store, config.device_id)

        workers[worker_id] = worker
        pumps.append(QueuePump(records, ingestor.ingest))

    group = WorkerGroup(workers)
    provider = BufferedPlotProvider(store, _plot_specs(configs))

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
            title="Live BWT901BLE Plot",
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

            # This demo treats every configured IMU as required.  WorkerGroup
            # only reports the failure; this app chooses the fail-fast policy.
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
        raise RuntimeError(f"BWT901 acquisition failed: {failed_ids}.") from first_error
    if close_error is not None:
        raise close_error


if __name__ == "__main__":
    main()
