"""Myo armband BLE source built from the verified pymyo demo flow."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any, ClassVar

from fundamental.messages import DEFAULT_MAX_FRAMES_PER_BATCH, WorkerEvent
from fundamental.sources.base import SourceName, SourceWorker
from fundamental.streams import CaptureResumeState, FieldSpec, StreamBlock, StreamSpec

try:
    from bleak import BleakScanner
    from bleak.exc import BleakBluetoothNotAvailableError
    from pymyo import Myo
    from pymyo.types import ClassifierMode, EmgMode, ImuMode, SleepMode
except ImportError:  # pragma: no cover - depends on local runtime
    BleakScanner = None
    BleakBluetoothNotAvailableError = None
    Myo = None
    ClassifierMode = None
    EmgMode = None
    ImuMode = None
    SleepMode = None


MYO_CONTROL_SERVICE_UUID = "d5060001-a904-deb9-4748-2c7f4a124842"
MYO_EMG_STREAM_ID = "myo.emg"
MYO_IMU_STREAM_ID = "myo.imu"
MYO_EMG_RATE_HZ = 200.0
MYO_IMU_RATE_HZ = 50.0

MYO_EMG_STREAM_SPEC = StreamSpec(
    stream_id=MYO_EMG_STREAM_ID,
    display_name="Myo EMG",
    nominal_rate_hz=MYO_EMG_RATE_HZ,
    fields=(
        FieldSpec(
            "host_rx_time_s",
            "Host Receive Time",
            unit="s",
            role="metadata",
            plottable=False,
            csv_decimals=9,
        ),
        *tuple(
            FieldSpec(
                f"emg_ch{index}_code",
                f"EMG CH {index}",
                unit="code",
                signal_kind="emg",
                default_plot=True,
                fixed_range=(-128.0, 127.0),
            )
            for index in range(1, 9)
        ),
    ),
    time_source="nominal_rate_reconstruction_with_host_receive_audit",
)

MYO_IMU_STREAM_SPEC = StreamSpec(
    stream_id=MYO_IMU_STREAM_ID,
    display_name="Myo IMU",
    nominal_rate_hz=MYO_IMU_RATE_HZ,
    fields=(
        FieldSpec(
            "host_rx_time_s",
            "Host Receive Time",
            unit="s",
            role="metadata",
            plottable=False,
            csv_decimals=9,
        ),
        FieldSpec("quat_w", "Quaternion W", fixed_range=(-1.0, 1.0), signal_kind="quaternion"),
        FieldSpec("quat_x", "Quaternion X", fixed_range=(-1.0, 1.0), signal_kind="quaternion"),
        FieldSpec("quat_y", "Quaternion Y", fixed_range=(-1.0, 1.0), signal_kind="quaternion"),
        FieldSpec("quat_z", "Quaternion Z", fixed_range=(-1.0, 1.0), signal_kind="quaternion"),
        FieldSpec("accel_x_g", "Accel X", unit="g", fixed_range=(-16.0, 16.0), signal_kind="acceleration"),
        FieldSpec("accel_y_g", "Accel Y", unit="g", fixed_range=(-16.0, 16.0), signal_kind="acceleration"),
        FieldSpec("accel_z_g", "Accel Z", unit="g", fixed_range=(-16.0, 16.0), signal_kind="acceleration"),
        FieldSpec(
            "gyro_x_dps",
            "Gyro X",
            unit="deg/s",
            fixed_range=(-2048.0, 2048.0),
            signal_kind="angular_velocity",
        ),
        FieldSpec(
            "gyro_y_dps",
            "Gyro Y",
            unit="deg/s",
            fixed_range=(-2048.0, 2048.0),
            signal_kind="angular_velocity",
        ),
        FieldSpec(
            "gyro_z_dps",
            "Gyro Z",
            unit="deg/s",
            fixed_range=(-2048.0, 2048.0),
            signal_kind="angular_velocity",
        ),
    ),
    time_source="nominal_rate_reconstruction_with_host_receive_audit",
)


@dataclass(frozen=True)
class MyoBLEConfig:
    """Connection and stream settings for a Myo armband."""

    address: str = ""
    device_name_filter: str = "Myo"
    scan_timeout_s: float = 10.0
    connect_timeout_s: float = 20.0
    enable_emg: bool = True
    enable_imu: bool = True

    def normalized(self) -> "MyoBLEConfig":
        return MyoBLEConfig(
            address=self.address.strip(),
            device_name_filter=self.device_name_filter.strip(),
            scan_timeout_s=max(0.1, float(self.scan_timeout_s)),
            connect_timeout_s=max(1.0, float(self.connect_timeout_s)),
            enable_emg=bool(self.enable_emg),
            enable_imu=bool(self.enable_imu),
        )

    def display_text(self) -> str:
        target = self.address or f"service UUID / name contains {self.device_name_filter!r}"
        streams = "+".join(
            name
            for enabled, name in ((self.enable_emg, "EMG"), (self.enable_imu, "IMU"))
            if enabled
        )
        return f"Myo BLE {target}, streams {streams or '-'}"


class MyoWorker(threading.Thread):
    """Own one pymyo connection and publish independent EMG and IMU streams."""

    def __init__(
        self,
        config: MyoBLEConfig,
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        resume_state: CaptureResumeState = CaptureResumeState(),
        max_rows_per_batch: int = DEFAULT_MAX_FRAMES_PER_BATCH,
    ) -> None:
        super().__init__(name="MyoWorker", daemon=True)
        self.config = config.normalized()
        self.data_queue = data_queue
        self.event_queue = event_queue
        self.stop_event = stop_event
        self.max_rows_per_batch = max(1, int(max_rows_per_batch))
        self.myo = None
        self._resolved_device = None
        self._host_origin_ns: int | None = None
        self._host_time_offset_s = resume_state.latest_time_s

        emg_cursor = resume_state.cursor(MYO_EMG_STREAM_ID)
        imu_cursor = resume_state.cursor(MYO_IMU_STREAM_ID)
        self._next_emg_time_s = (
            emg_cursor.last_time_s + 1.0 / MYO_EMG_RATE_HZ
            if emg_cursor is not None
            else None
        )
        self._next_imu_time_s = (
            imu_cursor.last_time_s + 1.0 / MYO_IMU_RATE_HZ
            if imu_cursor is not None
            else None
        )
        self._emg_time_s: list[float] = []
        self._emg_rows: list[tuple[int | float, ...]] = []
        self._imu_time_s: list[float] = []
        self._imu_rows: list[tuple[int | float, ...]] = []

    def run(self) -> None:
        if Myo is None or BleakScanner is None:
            self.event_queue.put(
                WorkerEvent("error", "pymyo/bleak is not installed; Myo acquisition is unavailable.")
            )
            return
        try:
            asyncio.run(self._run_async())
        except Exception as exc:  # pragma: no cover - hardware/event-loop dependent
            if BleakBluetoothNotAvailableError is not None and isinstance(
                exc, BleakBluetoothNotAvailableError
            ):
                message = "Bluetooth is unavailable or powered off. Turn on Bluetooth and retry."
            else:
                message = f"Myo worker failed: {type(exc).__name__}: {exc}"
            self.event_queue.put(WorkerEvent("error", message))
        finally:
            self._flush_all()

    async def _run_async(self) -> None:
        device = await self._resolve_device()
        if device is None:
            if self.stop_event.is_set():
                self.event_queue.put(WorkerEvent("log", "Myo start cancelled during scan."))
            else:
                self.event_queue.put(
                    WorkerEvent("error", "No Myo matched the configured address/service/name.")
                )
            return
        if self.stop_event.is_set():
            self.event_queue.put(WorkerEvent("log", "Myo start cancelled before connection."))
            return

        self.myo = Myo(device, timeout=self.config.connect_timeout_s)
        if self.config.enable_emg:
            self.myo.on_emg(self._on_emg)
        if self.config.enable_imu:
            self.myo.on_imu(self._on_imu)

        connected = False
        try:
            completed, _result = await self._await_or_stop(self.myo.connect())
            if not completed:
                self.event_queue.put(WorkerEvent("log", "Myo start cancelled during connection."))
                return
            connected = True
            self.event_queue.put(
                WorkerEvent("log", f"Connected to Myo {device.name or '-'} at {device.address}.")
            )
            await self._publish_device_metadata()
            await self.myo.set_sleep_mode(SleepMode.NEVER_SLEEP)
            await self.myo.set_mode(
                emg_mode=EmgMode.EMG_RAW if self.config.enable_emg else EmgMode.NONE,
                imu_mode=ImuMode.DATA if self.config.enable_imu else ImuMode.NONE,
                classifier_mode=ClassifierMode.DISABLED,
            )
            self.event_queue.put(WorkerEvent("log", "Started Myo EMG/IMU data streams."))

            while not self.stop_event.is_set():
                self._flush_all()
                await asyncio.sleep(0.05)
        finally:
            if connected or bool(self.myo and self.myo.is_connected):
                await self._cleanup_connection()

    async def _resolve_device(self):
        if self.config.address:
            self.event_queue.put(
                WorkerEvent("log", f"Scanning for configured Myo address {self.config.address}...")
            )
            _completed, device = await self._await_or_stop(
                BleakScanner.find_device_by_address(
                    self.config.address,
                    timeout=self.config.scan_timeout_s,
                )
            )
        else:
            folded_name = self.config.device_name_filter.casefold()
            self.event_queue.put(WorkerEvent("log", "Scanning for Myo service/name..."))
            _completed, device = await self._await_or_stop(
                BleakScanner.find_device_by_filter(
                    lambda candidate, advertisement: (
                        MYO_CONTROL_SERVICE_UUID
                        in {uuid.casefold() for uuid in (advertisement.service_uuids or [])}
                        or (
                            bool(folded_name)
                            and (
                                folded_name in (candidate.name or "").casefold()
                                or folded_name in (advertisement.local_name or "").casefold()
                            )
                        )
                    ),
                    timeout=self.config.scan_timeout_s,
                ),
            )
        self._resolved_device = device
        if device is not None:
            self.event_queue.put(
                WorkerEvent("log", f"Found Myo candidate {device.name or '-'} at {device.address}.")
            )
        return device

    async def _await_or_stop(self, awaitable):
        """Await a BLE operation while allowing the controller to cancel startup."""

        task = asyncio.ensure_future(awaitable)
        while not task.done():
            if self.stop_event.is_set():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                return False, None
            await asyncio.sleep(0.05)
        return True, await task

    def _on_emg(self, samples) -> None:
        host_rx_time_s = self._host_receive_time_s()
        if self._next_emg_time_s is None:
            self._next_emg_time_s = host_rx_time_s
        for sample in samples:
            self._emg_time_s.append(self._next_emg_time_s)
            self._emg_rows.append((host_rx_time_s, *(int(value) for value in sample)))
            self._next_emg_time_s += 1.0 / MYO_EMG_RATE_HZ
        if len(self._emg_rows) >= self.max_rows_per_batch:
            self._flush_emg()

    def _on_imu(self, orientation, accelerometer, gyroscope) -> None:
        host_rx_time_s = self._host_receive_time_s()
        if self._next_imu_time_s is None:
            self._next_imu_time_s = host_rx_time_s
        self._imu_time_s.append(self._next_imu_time_s)
        self._imu_rows.append(
            (
                host_rx_time_s,
                float(orientation.w),
                float(orientation.x),
                float(orientation.y),
                float(orientation.z),
                float(accelerometer[0]),
                float(accelerometer[1]),
                float(accelerometer[2]),
                float(gyroscope[0]),
                float(gyroscope[1]),
                float(gyroscope[2]),
            )
        )
        self._next_imu_time_s += 1.0 / MYO_IMU_RATE_HZ
        if len(self._imu_rows) >= self.max_rows_per_batch:
            self._flush_imu()

    def _host_receive_time_s(self) -> float:
        now_ns = time.perf_counter_ns()
        if self._host_origin_ns is None:
            self._host_origin_ns = now_ns
        return self._host_time_offset_s + (now_ns - self._host_origin_ns) / 1e9

    def _flush_all(self) -> None:
        self._flush_emg()
        self._flush_imu()

    def _flush_emg(self) -> None:
        if not self._emg_rows:
            return
        self.data_queue.put(
            StreamBlock(MYO_EMG_STREAM_SPEC, tuple(self._emg_time_s), tuple(self._emg_rows))
        )
        self._emg_time_s.clear()
        self._emg_rows.clear()

    def _flush_imu(self) -> None:
        if not self._imu_rows:
            return
        self.data_queue.put(
            StreamBlock(MYO_IMU_STREAM_SPEC, tuple(self._imu_time_s), tuple(self._imu_rows))
        )
        self._imu_time_s.clear()
        self._imu_rows.clear()

    async def _publish_device_metadata(self) -> None:
        if self.myo is None:
            return
        try:
            firmware = await self.myo.firmware_version
            info = await self.myo.info
            device = {
                "name": await self.myo.name,
                "battery_percent": await self.myo.battery,
                "firmware": str(firmware),
                "hardware_revision": firmware.hardware_rev.name,
                "serial_number_hex": info.serial_number.hex(":"),
                "ble_identifier": self._resolved_device.address,
            }
            try:
                pymyo_version = version("pymyo")
            except PackageNotFoundError:
                pymyo_version = "unknown"
            self.event_queue.put(
                WorkerEvent("metadata", data={"device": device, "pymyo_version": pymyo_version})
            )
        except Exception as exc:  # pragma: no cover - optional device reads
            self.event_queue.put(WorkerEvent("log", f"Myo metadata read warning: {exc}"))

    async def _cleanup_connection(self) -> None:
        if self.myo is None:
            return
        try:
            await self.myo.set_mode(
                emg_mode=EmgMode.NONE,
                imu_mode=ImuMode.NONE,
                classifier_mode=ClassifierMode.DISABLED,
            )
        except Exception as exc:  # pragma: no cover - disconnect dependent
            self.event_queue.put(WorkerEvent("log", f"Myo stream stop warning: {exc}"))
        try:
            await self.myo.set_sleep_mode(SleepMode.NORMAL)
        except Exception as exc:  # pragma: no cover - disconnect dependent
            self.event_queue.put(WorkerEvent("log", f"Myo sleep restore warning: {exc}"))
        try:
            await self.myo.disconnect()
            self.event_queue.put(WorkerEvent("log", "Disconnected Myo and restored normal sleep mode."))
        except Exception as exc:  # pragma: no cover - disconnect dependent
            self.event_queue.put(WorkerEvent("error", f"Myo disconnect failed: {exc}"))


@dataclass(frozen=True)
class MyoSource:
    config: MyoBLEConfig = field(default_factory=MyoBLEConfig)

    name: ClassVar[SourceName] = "ble_myo"
    display_name: ClassVar[str] = "Myo Armband"

    def __post_init__(self) -> None:
        object.__setattr__(self, "config", self.config.normalized())

    def display_text(self) -> str:
        return f"{self.display_name}: {self.config.display_text()}"

    def inspect_data(self) -> tuple[str, ...]:
        return (
            f"Source handle: {type(self).__name__}.create_worker(...) -> MyoWorker",
            "Transport/protocol: pymyo over bleak",
            "Device discovery: Myo Control Service UUID first, configured name as fallback",
            "Worker outputs: myo.emg and myo.imu StreamBlock objects",
            "EMG schema: reconstructed time, host receive time, 8 raw int8 channels",
            "IMU schema: reconstructed time, host receive time, quaternion, accel(g), gyro(deg/s)",
            f"Current config: {self.config.display_text()}",
        )

    def stream_specs(self) -> tuple[StreamSpec, ...]:
        specs: list[StreamSpec] = []
        if self.config.enable_emg:
            specs.append(MYO_EMG_STREAM_SPEC)
        if self.config.enable_imu:
            specs.append(MYO_IMU_STREAM_SPEC)
        return tuple(specs)

    def capture_metadata(self) -> dict[str, Any]:
        return {
            "transport": "ble",
            "config": asdict(self.config),
            "timestamp_note": (
                "time_s is reconstructed from nominal stream rate; host_rx_time_s is the "
                "capture-relative host callback time; neither is a device timestamp."
            ),
        }

    def with_config(self, config: MyoBLEConfig) -> "MyoSource":
        return MyoSource(config=config)

    def create_worker(
        self,
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        resume_state: CaptureResumeState = CaptureResumeState(),
    ) -> SourceWorker:
        return MyoWorker(
            config=self.config,
            data_queue=data_queue,
            event_queue=event_queue,
            stop_event=stop_event,
            resume_state=resume_state,
        )


__all__ = [
    "MYO_EMG_STREAM_SPEC",
    "MYO_IMU_STREAM_SPEC",
    "MyoBLEConfig",
    "MyoSource",
    "MyoWorker",
]
