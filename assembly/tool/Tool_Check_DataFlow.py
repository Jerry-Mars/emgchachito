"""Live raw-record arrival raster for acquisition data-flow diagnostics.

This tool intentionally ignores sensor values.  One visible point means one raw
record accepted from an acquisition worker queue:

- RunE W2: one decoded W2 packet record.
- BWT901: one decoded BWT901 frame record.
- Myo EMG: one EMG BLE notification record.
- Myo IMU: one IMU BLE notification record.

The plot therefore answers "when did the PC-side worker deliver records?" rather
than "how many normalized samples exist?".  Gaps are observations at this layer;
they are not automatically classified as device packet loss.
"""

from __future__ import annotations

import asyncio
import queue
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import dearpygui.dearpygui as dpg
from bleak import BleakScanner
from bleak.backends.device import BLEDevice

from assembly.acquisition.BLE.bwt901_worker import (
    BWT901BLEConfig,
    BWT901BLEWorker,
    BWT901Record,
)
from assembly.acquisition.BLE.myo_worker import MyoRecord, MyoWorker
from assembly.acquisition.runtime.queue_pump import QueuePump
from assembly.acquisition.runtime.worker_group import ManagedWorker, WorkerGroup
from assembly.acquisition.serial.w2_worker import (
    SerialW2Worker,
    W2Record,
    W2SerialConfig,
    resolve_w2_configs,
)


# ======================================================================
# HARDWARE CONFIGURATION
# ======================================================================


@dataclass(frozen=True, slots=True)
class MyoDeviceConfig:
    """Minimal composition-level identity for one Myo device."""

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


MYO_DEVICES: tuple[MyoDeviceConfig, ...] = (
    # MyoDeviceConfig("myo_1", "AA:BB:CC:DD:EE:FF"),
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
# TOOL CONFIGURATION
# ======================================================================

VISIBLE_WINDOW_S = 10.0
STARTUP_TIMEOUT_S = 30.0
SHUTDOWN_TIMEOUT_S = 8.0
MAX_RECORDS_PER_SOURCE_PER_FRAME = 4096
W2_QUEUE_SIZE = 4096
BWT901_QUEUE_SIZE = 2048
MYO_QUEUE_SIZE = 4096
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 760


@dataclass(slots=True)
class SourceFlowState:
    """PC-observed arrival history for one independently arriving raw source."""

    source_id: str
    lane: float
    arrivals_ns: deque[int] = field(default_factory=deque)
    total_records: int = 0
    last_arrival_ns: int | None = None
    last_gap_ns: int | None = None
    max_gap_ns: int | None = None


class DataFlowMonitor:
    """Track raw-record arrivals without inspecting sensor payload values."""

    def __init__(self, source_ids: tuple[str, ...], *, retention_seconds: float) -> None:
        if not source_ids:
            raise ValueError("DataFlowMonitor requires at least one source.")
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive.")
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("DataFlowMonitor source IDs must be unique.")

        self.retention_ns = int(retention_seconds * 1_000_000_000)
        self._states = {
            source_id: SourceFlowState(source_id=source_id, lane=float(index))
            for index, source_id in enumerate(source_ids)
        }

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(self._states)

    def state(self, source_id: str) -> SourceFlowState:
        try:
            return self._states[source_id]
        except KeyError as exc:
            raise KeyError(f"Unknown data-flow source {source_id!r}.") from exc

    def observe(self, source_id: str, host_monotonic_ns: int) -> None:
        state = self.state(source_id)
        timestamp = int(host_monotonic_ns)
        previous = state.last_arrival_ns
        if previous is not None and timestamp < previous:
            raise ValueError(
                f"Arrival time moved backwards for {source_id!r}: {timestamp} < {previous}."
            )

        if previous is not None:
            gap = timestamp - previous
            state.last_gap_ns = gap
            if state.max_gap_ns is None or gap > state.max_gap_ns:
                state.max_gap_ns = gap

        state.arrivals_ns.append(timestamp)
        state.total_records += 1
        state.last_arrival_ns = timestamp
        self.prune(timestamp)

    def prune(self, now_monotonic_ns: int) -> None:
        cutoff = int(now_monotonic_ns) - self.retention_ns
        for state in self._states.values():
            while state.arrivals_ns and state.arrivals_ns[0] < cutoff:
                state.arrivals_ns.popleft()

    def relative_points(
        self,
        source_id: str,
        *,
        origin_monotonic_ns: int,
    ) -> tuple[list[float], list[float]]:
        state = self.state(source_id)
        x = [
            (timestamp - origin_monotonic_ns) / 1_000_000_000.0
            for timestamp in state.arrivals_ns
        ]
        y = [state.lane] * len(x)
        return x, y


@dataclass(slots=True)
class SourceBinding:
    source_id: str
    pump: QueuePump[dict[str, object]]


@dataclass(slots=True)
class RuntimeBundle:
    workers: dict[str, ManagedWorker]
    source_bindings: list[SourceBinding]
    source_ids: tuple[str, ...]


async def _find_myo_device(config: MyoDeviceConfig) -> BLEDevice:
    device = await BleakScanner.find_device_by_address(
        config.address,
        timeout=config.scan_timeout_s,
    )
    if device is None:
        raise RuntimeError(f"Could not find Myo at BLE address {config.address!r}.")
    return device


def _validate_configs() -> None:
    if not MYO_DEVICES and not W2_DEVICES and not BWT901_DEVICES:
        raise ValueError("Configure at least one Myo, W2, or BWT901 device.")

    myo_ids = [config.device_id.casefold() for config in MYO_DEVICES]
    if len(set(myo_ids)) != len(myo_ids):
        raise ValueError("Myo device IDs must be unique.")

    w2_ports = [config.port.casefold() for config in W2_DEVICES]
    if len(set(w2_ports)) != len(w2_ports):
        raise ValueError("Each W2 device must use a different serial port.")

    bwt_ids = [config.device_id.casefold() for config in BWT901_DEVICES]
    if len(set(bwt_ids)) != len(bwt_ids):
        raise ValueError("BWT901 device IDs must be unique.")


def _record_observer(
    monitor: DataFlowMonitor,
    source_id_for_record: Callable[[dict[str, object]], str],
) -> Callable[[dict[str, object]], None]:
    def observe(record: dict[str, object]) -> None:
        source_id = source_id_for_record(record)
        monitor.observe(source_id, int(record["host_monotonic_ns"]))

    return observe


def _build_runtime() -> tuple[RuntimeBundle, DataFlowMonitor]:
    _validate_configs()
    resolved_w2 = resolve_w2_configs(W2_DEVICES) if W2_DEVICES else ()
    myo_devices = (
        tuple(asyncio.run(_find_myo_device(config)) for config in MYO_DEVICES)
        if MYO_DEVICES
        else ()
    )

    source_ids: list[str] = []
    for config in resolved_w2:
        source_ids.append(f"w2.{config.device_id}.packet")
    for config in BWT901_DEVICES:
        source_ids.append(f"bwt901.{config.device_id}.frame")
    for config in MYO_DEVICES:
        source_ids.extend(
            (
                f"myo.{config.device_id}.emg_notification",
                f"myo.{config.device_id}.imu_notification",
            )
        )

    monitor = DataFlowMonitor(tuple(source_ids), retention_seconds=VISIBLE_WINDOW_S)
    workers: dict[str, ManagedWorker] = {}
    bindings: list[SourceBinding] = []

    for config in resolved_w2:
        records: queue.Queue[W2Record] = queue.Queue(maxsize=W2_QUEUE_SIZE)
        worker_id = f"w2.{config.device_id}"
        source_id = f"w2.{config.device_id}.packet"
        worker = SerialW2Worker(config, records)
        workers[worker_id] = worker
        bindings.append(
            SourceBinding(
                source_id=source_id,
                pump=QueuePump(records, _record_observer(monitor, lambda _record, sid=source_id: sid)),
            )
        )

    for config in BWT901_DEVICES:
        records: queue.Queue[BWT901Record] = queue.Queue(maxsize=BWT901_QUEUE_SIZE)
        worker_id = f"bwt901.{config.device_id}"
        source_id = f"bwt901.{config.device_id}.frame"
        worker = BWT901BLEWorker(config, records)
        workers[worker_id] = worker
        bindings.append(
            SourceBinding(
                source_id=source_id,
                pump=QueuePump(records, _record_observer(monitor, lambda _record, sid=source_id: sid)),
            )
        )

    for config, device in zip(MYO_DEVICES, myo_devices, strict=True):
        records: queue.Queue[MyoRecord] = queue.Queue(maxsize=MYO_QUEUE_SIZE)
        worker_id = f"myo.{config.device_id}"
        worker = MyoWorker(
            device,
            records,
            connect_timeout_s=config.connect_timeout_s,
        )
        workers[worker_id] = worker

        emg_source = f"myo.{config.device_id}.emg_notification"
        imu_source = f"myo.{config.device_id}.imu_notification"

        def myo_source(record: dict[str, object], *, emg=emg_source, imu=imu_source) -> str:
            stream = str(record.get("stream", ""))
            if stream == "emg":
                return emg
            if stream == "imu":
                return imu
            raise ValueError(f"Unexpected Myo raw stream {stream!r}.")

        bindings.append(
            SourceBinding(
                source_id=f"myo.{config.device_id}",
                pump=QueuePump(records, _record_observer(monitor, myo_source)),
            )
        )

    if not workers:
        raise RuntimeError("No acquisition workers were created.")

    return RuntimeBundle(workers, bindings, tuple(source_ids)), monitor


class DataFlowWindow:
    """Minimal DearPyGui raster plus per-source arrival-gap statistics."""

    def __init__(self, monitor: DataFlowMonitor, *, origin_monotonic_ns: int) -> None:
        self.monitor = monitor
        self.origin_monotonic_ns = int(origin_monotonic_ns)
        self.plot_tag = "tool.data_flow.plot"
        self.x_axis_tag = "tool.data_flow.x_axis"
        self.y_axis_tag = "tool.data_flow.y_axis"
        self.series_tags = {
            source_id: f"tool.data_flow.series.{index}"
            for index, source_id in enumerate(monitor.source_ids)
        }
        self.stats_tags = {
            source_id: f"tool.data_flow.stats.{index}"
            for index, source_id in enumerate(monitor.source_ids)
        }

    def build(self) -> None:
        with dpg.window(label="Data Flow", tag="tool.data_flow.window", width=1220, height=690):
            dpg.add_text("RAW RECORD ARRIVAL RASTER")
            dpg.add_text(f"Visible window: last {VISIBLE_WINDOW_S:g} s")
            with dpg.plot(tag=self.plot_tag, height=470, width=-1, anti_aliased=False):
                dpg.add_plot_legend()
                dpg.add_plot_axis(dpg.mvXAxis, label="PC host monotonic time (s)", tag=self.x_axis_tag)
                dpg.add_plot_axis(dpg.mvYAxis, label="Raw source", tag=self.y_axis_tag)
                for source_id in self.monitor.source_ids:
                    state = self.monitor.state(source_id)
                    dpg.add_scatter_series(
                        [],
                        [],
                        label=source_id,
                        parent=self.y_axis_tag,
                        tag=self.series_tags[source_id],
                    )

            dpg.add_separator()
            dpg.add_text("Source                                  Frames     Last gap     Max gap")
            for source_id in self.monitor.source_ids:
                dpg.add_text("", tag=self.stats_tags[source_id])

    def refresh(self, now_monotonic_ns: int) -> None:
        now_ns = int(now_monotonic_ns)
        self.monitor.prune(now_ns)
        elapsed_s = max(0.0, (now_ns - self.origin_monotonic_ns) / 1_000_000_000.0)
        left_s = max(0.0, elapsed_s - VISIBLE_WINDOW_S)
        right_s = max(VISIBLE_WINDOW_S, elapsed_s)
        if elapsed_s >= VISIBLE_WINDOW_S:
            right_s = elapsed_s
        dpg.set_axis_limits(self.x_axis_tag, left_s, right_s)
        dpg.set_axis_limits(self.y_axis_tag, -0.75, len(self.monitor.source_ids) - 0.25)

        for source_id in self.monitor.source_ids:
            state = self.monitor.state(source_id)
            x, y = self.monitor.relative_points(
                source_id,
                origin_monotonic_ns=self.origin_monotonic_ns,
            )
            dpg.set_value(self.series_tags[source_id], [x, y])
            dpg.set_value(
                self.stats_tags[source_id],
                f"{source_id:<38} {state.total_records:>8}     "
                f"{_format_gap(state.last_gap_ns):>9}     {_format_gap(state.max_gap_ns):>9}",
            )


def _format_gap(gap_ns: int | None) -> str:
    if gap_ns is None:
        return "-"
    gap_ms = gap_ns / 1_000_000.0
    if gap_ms < 1000.0:
        return f"{gap_ms:.1f} ms"
    return f"{gap_ms / 1000.0:.2f} s"


def _print_worker_summary(group: WorkerGroup, workers: dict[str, ManagedWorker]) -> None:
    print("\n[Data-flow workers]")
    for worker_id, worker in workers.items():
        print(
            f"{worker_id:24} alive={worker.is_alive()} "
            f"startup={worker.startup_event.is_set()} "
            f"stopped={worker.stopped_event.is_set()} error={worker.error!r}"
        )
    print("failures:", group.failures())


def main() -> None:
    runtime, monitor = _build_runtime()
    group = WorkerGroup(runtime.workers)

    try:
        group.start()
        group.wait_ready(STARTUP_TIMEOUT_S)
    except BaseException:
        try:
            group.close(SHUTDOWN_TIMEOUT_S)
        finally:
            _print_worker_summary(group, runtime.workers)
        raise

    _print_worker_summary(group, runtime.workers)
    origin_ns = time.perf_counter_ns()

    dpg.create_context()
    close_error: BaseException | None = None
    try:
        window = DataFlowWindow(monitor, origin_monotonic_ns=origin_ns)
        window.build()
        dpg.create_viewport(
            title="Tool - Check Data Flow",
            width=VIEWPORT_WIDTH,
            height=VIEWPORT_HEIGHT,
            x_pos=80,
            y_pos=80,
        )
        dpg.setup_dearpygui()
        dpg.show_viewport()

        while dpg.is_dearpygui_running():
            for binding in runtime.source_bindings:
                binding.pump.drain(max_items=MAX_RECORDS_PER_SOURCE_PER_FRAME)

            if group.failures():
                break

            window.refresh(time.perf_counter_ns())
            dpg.render_dearpygui_frame()
    finally:
        try:
            group.close(SHUTDOWN_TIMEOUT_S)
        except BaseException as exc:
            close_error = exc
        dpg.destroy_context()
        _print_worker_summary(group, runtime.workers)

    failures = group.failures()
    if failures:
        failed_ids = ", ".join(failures)
        first_error = next(iter(failures.values()))
        raise RuntimeError(f"Data-flow acquisition failed: {failed_ids}.") from first_error
    if close_error is not None:
        raise close_error


if __name__ == "__main__":
    main()
