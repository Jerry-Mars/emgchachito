"""Arbitrary Myo / RunE W2 / BWT901 composition with realtime Plot + HDF5 Save.

This file is intentionally a composition harness rather than a device framework.
Each device keeps its own worker and ingestor semantics while all normalized
streams share the same runtime Store, Plot provider, and Recorder.

Configuration may contain zero, one, or many instances of each device type.  At
least one physical device must be configured overall.
"""

from __future__ import annotations

import asyncio
import queue
from dataclasses import dataclass

import dearpygui.dearpygui as dpg
from bleak import BleakScanner
from bleak.backends.device import BLEDevice

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
from assembly.acquisition.BLE.myo_ingest import (
    MyoRecordIngestor,
    make_myo_stream_schemas,
    myo_emg_stream_id,
    myo_imu_stream_id,
)
from assembly.acquisition.BLE.myo_worker import MyoRecord, MyoWorker
from assembly.acquisition.runtime.queue_pump import QueuePump
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore, StreamSchema
from assembly.acquisition.runtime.worker_group import ManagedWorker, WorkerGroup
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
from assembly.save.selectable_recorder import SelectableStreamRecorder
from assembly.save.save_panel import SavePanel
from assembly.save.store_tap import StreamStoreTap


@dataclass(frozen=True, slots=True)
class MyoDeviceConfig:
    """Composition-level identity and BLE address for one physical Myo."""

    device_id: str
    address: str
    scan_timeout_s: float = 10.0
    connect_timeout_s: float = 20.0

    def __post_init__(self) -> None:
        device_id = self.device_id.strip()
        address = self.address.strip()
        if not device_id:
            raise ValueError("Myo device_id must not be empty.")
        if not address:
            raise ValueError("Myo address must not be empty.")
        if self.scan_timeout_s <= 0 or self.connect_timeout_s <= 0:
            raise ValueError("Myo scan/connect timeouts must be positive.")
        object.__setattr__(self, "device_id", device_id)
        object.__setattr__(self, "address", address)
        object.__setattr__(self, "scan_timeout_s", float(self.scan_timeout_s))
        object.__setattr__(self, "connect_timeout_s", float(self.connect_timeout_s))


# ======================================================================
# HARDWARE CONFIGURATION
# ======================================================================
#
# Each tuple may contain 0, 1, or N devices.  Keep only devices that should
# participate in this run.  The default preserves the already-used 2x W2 setup;
# examples for Myo/BWT901 are left below as comments to avoid placeholder BLE
# addresses making the script fail before the configured devices can start.

MYO_DEVICES: tuple[MyoDeviceConfig, ...] = (
    # MyoDeviceConfig("left_arm", "AA:BB:CC:DD:EE:FF"),
    # MyoDeviceConfig("right_arm", "11:22:33:44:55:66"),
)

W2_DEVICES: tuple[W2SerialConfig, ...] = (
    W2SerialConfig("COM9"),
    W2SerialConfig("COM11"),
)

BWT901_DEVICES: tuple[BWT901BLEConfig, ...] = (
    # BWT901BLEConfig(
    #     "imu_1",
    #     address="E9:34:17:08:9F:4A",
    #     name_filter="WT901BLE67",
    # ),
)


# ======================================================================
# RUNTIME CONFIGURATION
# ======================================================================

RETENTION_SECONDS = 35.0
MYO_QUEUE_SIZE = 4096
W2_QUEUE_SIZE = 4096
BWT901_QUEUE_SIZE = 2048
MAX_RECORDS_PER_PUMP_PER_FRAME = 4096
SHUTDOWN_TIMEOUT_S = 8.0


_BWT901_FIELD_PLOT_CONFIG = {
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


def _validate_configs(
    myos: tuple[MyoDeviceConfig, ...],
    w2s: tuple[W2SerialConfig, ...],
    bwts: tuple[BWT901BLEConfig, ...],
) -> None:
    if not myos and not w2s and not bwts:
        raise ValueError("Configure at least one Myo, W2, or BWT901 device.")

    myo_ids = [config.device_id.casefold() for config in myos]
    if len(set(myo_ids)) != len(myo_ids):
        raise ValueError("Myo device IDs must be unique.")
    myo_addresses = [config.address.casefold() for config in myos]
    if len(set(myo_addresses)) != len(myo_addresses):
        raise ValueError("Myo BLE addresses must be unique.")

    w2_ports = [config.port.casefold() for config in w2s]
    if len(set(w2_ports)) != len(w2_ports):
        raise ValueError("Each W2 device must use a different serial port.")

    bwt_ids = [config.device_id.casefold() for config in bwts]
    if len(set(bwt_ids)) != len(bwt_ids):
        raise ValueError("BWT901 device IDs must be unique.")
    if len(bwts) > 1:
        addresses = [config.address.casefold() for config in bwts]
        if any(not address for address in addresses):
            raise ValueError("Multiple BWT901 devices require explicit BLE addresses.")
        if len(set(addresses)) != len(addresses):
            raise ValueError("BWT901 BLE addresses must be unique.")


def _schemas(
    myos: tuple[MyoDeviceConfig, ...],
    w2s: tuple[ResolvedW2SerialConfig, ...],
    bwts: tuple[BWT901BLEConfig, ...],
) -> tuple[StreamSchema, ...]:
    schemas: list[StreamSchema] = []
    for config in myos:
        schemas.extend(make_myo_stream_schemas(config.device_id))
    schemas.extend(
        make_w2_stream_schema(config.device_id, nominal_rate_hz=config.nominal_rate_hz)
        for config in w2s
    )
    schemas.extend(make_bwt901_stream_schema(config.device_id) for config in bwts)
    return tuple(schemas)


def _myo_plot_specs(config: MyoDeviceConfig) -> tuple[SeriesSpec, ...]:
    emg_stream_id = myo_emg_stream_id(config.device_id)
    imu_stream_id = myo_imu_stream_id(config.device_id)
    label_prefix = f"Myo {config.device_id}"
    return (
        *tuple(
            SeriesSpec(
                series_id=f"{emg_stream_id}/ch{channel}",
                stream_id=emg_stream_id,
                field_key=f"emg_ch{channel}_code",
                label=f"{label_prefix} EMG CH {channel}",
                unit="code",
                signal_kind="emg",
                default_plot=True,
                fixed_range=(-128.0, 127.0),
            )
            for channel in range(1, 9)
        ),
        *tuple(
            SeriesSpec(
                series_id=f"{imu_stream_id}/quat_{axis}",
                stream_id=imu_stream_id,
                field_key=f"quat_{axis}",
                label=f"{label_prefix} Quaternion {axis.upper()}",
                unit="",
                signal_kind="quaternion",
                default_plot=False,
                fixed_range=(-1.0, 1.0),
            )
            for axis in "wxyz"
        ),
        *tuple(
            SeriesSpec(
                series_id=f"{imu_stream_id}/accel_{axis}_g",
                stream_id=imu_stream_id,
                field_key=f"accel_{axis}_g",
                label=f"{label_prefix} Acceleration {axis.upper()}",
                unit="g",
                signal_kind="acceleration",
                default_plot=False,
                fixed_range=(-16.0, 16.0),
            )
            for axis in "xyz"
        ),
        *tuple(
            SeriesSpec(
                series_id=f"{imu_stream_id}/gyro_{axis}_dps",
                stream_id=imu_stream_id,
                field_key=f"gyro_{axis}_dps",
                label=f"{label_prefix} Gyroscope {axis.upper()}",
                unit="deg/s",
                signal_kind="angular_velocity",
                default_plot=False,
                fixed_range=(-2048.0, 2048.0),
            )
            for axis in "xyz"
        ),
    )


def _w2_plot_spec(config: ResolvedW2SerialConfig) -> SeriesSpec:
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


def _bwt901_plot_specs(config: BWT901BLEConfig) -> tuple[SeriesSpec, ...]:
    stream_id = bwt901_stream_id(config.device_id)
    specs: list[SeriesSpec] = []
    for field_key in BWT901_FIELD_KEYS:
        label, unit, signal_kind, fixed_range, default_plot = _BWT901_FIELD_PLOT_CONFIG[field_key]
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


def _plot_specs(
    myos: tuple[MyoDeviceConfig, ...],
    w2s: tuple[ResolvedW2SerialConfig, ...],
    bwts: tuple[BWT901BLEConfig, ...],
) -> tuple[SeriesSpec, ...]:
    specs: list[SeriesSpec] = []
    for config in myos:
        specs.extend(_myo_plot_specs(config))
    specs.extend(_w2_plot_spec(config) for config in w2s)
    for config in bwts:
        specs.extend(_bwt901_plot_specs(config))
    return tuple(specs)


async def _find_myo_device(config: MyoDeviceConfig) -> BLEDevice:
    device = await BleakScanner.find_device_by_address(
        config.address,
        timeout=config.scan_timeout_s,
    )
    if device is None:
        raise RuntimeError(
            f"Could not find Myo {config.device_id!r} at BLE address {config.address!r}."
        )
    return device


def _resolve_myo_devices(
    configs: tuple[MyoDeviceConfig, ...],
) -> dict[str, BLEDevice]:
    # Discovery is deliberately sequential.  It is setup work, and avoiding
    # concurrent Windows BLE scans is more predictable than optimizing startup.
    resolved: dict[str, BLEDevice] = {}
    for config in configs:
        resolved[config.device_id] = asyncio.run(_find_myo_device(config))
    return resolved


def _startup_timeout_s(
    myos: tuple[MyoDeviceConfig, ...],
    bwts: tuple[BWT901BLEConfig, ...],
) -> float:
    # BWT901 workers deliberately serialize Windows BLE scan/connect attempts,
    # so their scan budgets accumulate.  Myo workers may connect concurrently.
    myo_budget = max((config.connect_timeout_s + 10.0 for config in myos), default=0.0)
    bwt_budget = sum(config.scan_timeout_s + 10.0 for config in bwts)
    return max(15.0, myo_budget, bwt_budget)


def _print_worker_summary(workers: dict[str, ManagedWorker]) -> None:
    print("\n[Workers]")
    for worker_id, worker in workers.items():
        print(
            f"{worker_id:24} "
            f"alive={worker.is_alive()} "
            f"startup={worker.startup_event.is_set()} "
            f"stopped={worker.stopped_event.is_set()} "
            f"error={worker.error!r}"
        )


def main() -> None:
    myo_configs = MYO_DEVICES
    requested_w2_configs = W2_DEVICES
    bwt_configs = BWT901_DEVICES
    _validate_configs(myo_configs, requested_w2_configs, bwt_configs)
    w2_configs = resolve_w2_configs(requested_w2_configs)
    if w2_configs:
        print("Resolved W2 identities:")
        for config in w2_configs:
            print(f"  {config.device_name} -> {config.device_id} @ {config.port}")

    schemas = _schemas(myo_configs, w2_configs, bwt_configs)
    series_specs = _plot_specs(myo_configs, w2_configs, bwt_configs)
    myo_devices = _resolve_myo_devices(myo_configs)

    store = RealtimeStreamStore(schemas, retention_seconds=RETENTION_SECONDS)
    recorder = SelectableStreamRecorder()
    tapped_store = StreamStoreTap(store, recorder)

    workers: dict[str, ManagedWorker] = {}
    pumps: list[QueuePump] = []
    queue_capacities: list[int] = []

    for config in myo_configs:
        records: queue.Queue[MyoRecord] = queue.Queue(maxsize=MYO_QUEUE_SIZE)
        worker_id = f"myo.{config.device_id}"
        worker = MyoWorker(
            myo_devices[config.device_id],
            records,
            connect_timeout_s=config.connect_timeout_s,
        )
        ingestor = MyoRecordIngestor(tapped_store, config.device_id)  # type: ignore[arg-type]
        workers[worker_id] = worker
        pumps.append(QueuePump(records, ingestor.ingest))
        queue_capacities.append(MYO_QUEUE_SIZE)

    for config in w2_configs:
        records: queue.Queue[W2Record] = queue.Queue(maxsize=W2_QUEUE_SIZE)
        worker_id = f"w2.{config.device_id}"
        worker = SerialW2Worker(config, records)
        ingestor = W2RecordIngestor(tapped_store, config.device_id)  # type: ignore[arg-type]
        workers[worker_id] = worker
        pumps.append(QueuePump(records, ingestor.ingest))
        queue_capacities.append(W2_QUEUE_SIZE)

    for config in bwt_configs:
        records: queue.Queue[BWT901Record] = queue.Queue(maxsize=BWT901_QUEUE_SIZE)
        worker_id = f"bwt901.{config.device_id}"
        worker = BWT901BLEWorker(config, records)
        ingestor = BWT901RecordIngestor(tapped_store, config.device_id)  # type: ignore[arg-type]
        workers[worker_id] = worker
        pumps.append(QueuePump(records, ingestor.ingest))
        queue_capacities.append(BWT901_QUEUE_SIZE)

    group = WorkerGroup(workers)
    provider = BufferedPlotProvider(store, series_specs)

    try:
        group.start()
        group.wait_ready(_startup_timeout_s(myo_configs, bwt_configs))
    except BaseException:
        try:
            group.close(SHUTDOWN_TIMEOUT_S)
        finally:
            _print_worker_summary(workers)
        raise

    _print_worker_summary(workers)

    dpg.create_context()
    close_error: BaseException | None = None
    try:
        plot_state = create_plot_window(provider)

        with dpg.window(
            label="Save",
            tag="assembly.random_device_plot_save.save_window",
            width=540,
            height=300,
            pos=(1220, 80),
        ):
            dpg.add_text(
                "All acquisition and Plot streams run continuously; Save Start/Stop only controls recording."
            )
            dpg.add_separator()
            save_panel = SavePanel(
                recorder,
                schemas,
                tag_prefix="assembly.random_device_plot_save.save",
                default_directory="captures",
                default_filename="multi_device_capture.h5",
            )
            save_panel.build()

        dpg.create_viewport(
            title="Random Device Plot + Save",
            width=1760,
            height=920,
            x_pos=40,
            y_pos=40,
        )
        dpg.setup_dearpygui()
        dpg.show_viewport()

        while dpg.is_dearpygui_running():
            for pump in pumps:
                pump.drain(max_items=MAX_RECORDS_PER_PUMP_PER_FRAME)

            if group.failures():
                break

            plot_state.refresh(provider)
            save_panel.refresh()
            dpg.render_dearpygui_frame()
    finally:
        # Stop every producer, then drain records already emitted while Recorder
        # is still open.  Each queue is drained up to its full configured capacity.
        try:
            group.close(SHUTDOWN_TIMEOUT_S)
        except BaseException as exc:
            close_error = exc
        finally:
            for pump, capacity in zip(pumps, queue_capacities):
                pump.drain(max_items=capacity)
            recorder.stop()
            dpg.destroy_context()
            _print_worker_summary(workers)

    failures = group.failures()
    if failures:
        failed_ids = ", ".join(failures)
        first_error = next(iter(failures.values()))
        raise RuntimeError(f"Acquisition failed: {failed_ids}.") from first_error
    if close_error is not None:
        raise close_error


if __name__ == "__main__":
    main()
