"""BWT901BLE IMU source using a pure decoder over the shared BLE transport."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar

from DeviceInterface.bwt901_protocol import BWT901Packet, BWT901StreamDecoder
from fundamental.messages import DEFAULT_MAX_FRAMES_PER_BATCH, WorkerEvent
from fundamental.sources.base import (
    SourceName,
    SourceWorker,
    SourceWorkerGroup,
    WorkerControl,
)
from fundamental.streams import CaptureResumeState, FieldSpec, StreamBlock, StreamSpec
from fundamental.transports import BleGattConfig, BleGattTransport


DEFAULT_BWT901_ADDRESS = "CF:B6:E0:FC:2F:98"
DEFAULT_BWT901_NAME_FILTER = "WT901BLE67"
DEFAULT_BWT901_SERVICE_UUID = "0000ffe5-0000-1000-8000-00805f9a34fb"
DEFAULT_BWT901_WRITE_UUID = "0000ffe9-0000-1000-8000-00805f9a34fb"
DEFAULT_BWT901_NOTIFY_UUID = "0000ffe4-0000-1000-8000-00805f9a34fb"
MAX_BWT901_DEVICES = 2


@dataclass(frozen=True)
class BWT901DeviceConfig:
    device_id: str = "imu_1"
    address: str = DEFAULT_BWT901_ADDRESS
    name_filter: str = DEFAULT_BWT901_NAME_FILTER
    windows_address_type: str | None = None

    def normalized(self) -> "BWT901DeviceConfig":
        address_type = self.windows_address_type
        if address_type not in (None, "public", "random"):
            address_type = None
        return BWT901DeviceConfig(
            device_id=self.device_id.strip(),
            address=self.address.strip(),
            name_filter=self.name_filter.strip(),
            windows_address_type=address_type,
        )

    def display_text(self) -> str:
        target = self.address or f"name contains {self.name_filter!r}"
        return f"{self.device_id or '-'}={target}"


@dataclass(frozen=True)
class BWT901BLEConfig:
    devices: tuple[BWT901DeviceConfig, ...] = field(
        default_factory=lambda: (BWT901DeviceConfig(),)
    )
    service_uuid: str = DEFAULT_BWT901_SERVICE_UUID
    notify_uuid: str = DEFAULT_BWT901_NOTIFY_UUID
    write_uuid: str = DEFAULT_BWT901_WRITE_UUID
    scan_timeout_s: float = 10.0

    def normalized(self) -> "BWT901BLEConfig":
        return BWT901BLEConfig(
            devices=tuple(device.normalized() for device in self.devices),
            service_uuid=self.service_uuid.strip(),
            notify_uuid=self.notify_uuid.strip(),
            write_uuid=self.write_uuid.strip(),
            scan_timeout_s=max(0.1, float(self.scan_timeout_s)),
        )

    def display_text(self) -> str:
        devices = "; ".join(device.display_text() for device in self.devices) or "none"
        return f"BWT901BLE [{devices}]"


def bwt901_stream_id(device_id: str) -> str:
    return f"bwt901.{device_id.strip()}.imu"


def bwt901_stream_spec(device: BWT901DeviceConfig) -> StreamSpec:
    label = device.device_id.strip() or "IMU"
    return StreamSpec(
        stream_id=bwt901_stream_id(device.device_id),
        display_name=f"BWT901 {label} IMU",
        nominal_rate_hz=None,
        fields=(
            FieldSpec("sequence", "Frame Sequence", role="metadata", plottable=False),
            FieldSpec(
                "acc_x_g", f"{label} Accel X", unit="g", signal_kind="acceleration",
                fixed_range=(-16.0, 16.0),
            ),
            FieldSpec(
                "acc_y_g", f"{label} Accel Y", unit="g", signal_kind="acceleration",
                fixed_range=(-16.0, 16.0),
            ),
            FieldSpec(
                "acc_z_g", f"{label} Accel Z", unit="g", signal_kind="acceleration",
                fixed_range=(-16.0, 16.0),
            ),
            FieldSpec(
                "gyro_x_dps", f"{label} Gyro X", unit="deg/s",
                signal_kind="angular_velocity", fixed_range=(-2000.0, 2000.0),
            ),
            FieldSpec(
                "gyro_y_dps", f"{label} Gyro Y", unit="deg/s",
                signal_kind="angular_velocity", fixed_range=(-2000.0, 2000.0),
            ),
            FieldSpec(
                "gyro_z_dps", f"{label} Gyro Z", unit="deg/s",
                signal_kind="angular_velocity", fixed_range=(-2000.0, 2000.0),
            ),
            FieldSpec(
                "angle_x_deg", f"{label} Angle X", unit="deg", signal_kind="generic",
                fixed_range=(-180.0, 180.0),
            ),
            FieldSpec(
                "angle_y_deg", f"{label} Angle Y", unit="deg", signal_kind="generic",
                fixed_range=(-180.0, 180.0),
            ),
            FieldSpec(
                "angle_z_deg", f"{label} Angle Z", unit="deg", signal_kind="generic",
                fixed_range=(-180.0, 180.0),
            ),
        ),
        time_source="shared_host_monotonic_receive_time_excluding_pauses",
    )


class BWT901BLEWorker(threading.Thread):
    """Own one BWT901 BLE connection and publish its decoded IMU stream."""

    def __init__(
        self,
        config: BWT901BLEConfig,
        device: BWT901DeviceConfig,
        spec: StreamSpec,
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        resume_state: CaptureResumeState = CaptureResumeState(),
        max_frames_per_batch: int = DEFAULT_MAX_FRAMES_PER_BATCH,
        control: WorkerControl | None = None,
        transport_factory=BleGattTransport,
    ) -> None:
        normalized = config.normalized()
        self.device = device.normalized()
        super().__init__(name=f"BWT901BLEWorker-{self.device.device_id}", daemon=True)
        self.config = normalized
        self.spec = spec
        self.data_queue = data_queue
        self.event_queue = event_queue
        self.stop_event = stop_event
        self.control = control or WorkerControl.running(
            stop_event,
            offset_s=resume_state.latest_time_s,
        )
        self.ready_event = threading.Event()
        self.max_frames_per_batch = max(1, int(max_frames_per_batch))
        self.decoder = BWT901StreamDecoder()
        self.transport_factory = transport_factory
        self.transport = None
        self.notification_count = 0
        self.decoded_frame_count = 0
        self._started_monotonic = 0.0
        self._last_frame_monotonic: float | None = None
        self._times: list[float] = []
        self._rows: list[tuple[int | float, ...]] = []

    @property
    def source_id(self) -> str:
        return self.device.device_id

    def run(self) -> None:
        try:
            asyncio.run(self._run_async())
        except Exception as exc:  # pragma: no cover - hardware/event-loop dependent
            self._emit("error", f"BWT901 {self.source_id} failed: {type(exc).__name__}: {exc}")
        finally:
            self._flush()

    async def _run_async(self) -> None:
        transport = self.transport_factory(
            BleGattConfig(
                address=self.device.address,
                name_filter=self.device.name_filter,
                scan_timeout_s=self.config.scan_timeout_s,
                windows_address_type=self.device.windows_address_type,
            ),
            log=lambda message: self._emit("log", message),
        )
        self.transport = transport
        try:
            await transport.connect()
            if self.stop_event.is_set():
                return
            await transport.start_notify(self.config.notify_uuid, self._on_notification)
            self._started_monotonic = time.monotonic()
            self.ready_event.set()
            self._emit(
                "ready",
                f"BWT901 {self.source_id} ready at {transport.resolved_address}.",
                data={"address": transport.resolved_address, "transport": "ble"},
            )

            health_deadline = time.monotonic() + 1.0
            while not self.stop_event.is_set():
                if transport.disconnected_event.is_set() or not transport.is_connected:
                    raise ConnectionError("BLE connection was lost.")
                self._flush()
                now = time.monotonic()
                if now >= health_deadline:
                    self._emit_health(now)
                    health_deadline = now + 1.0
                await asyncio.sleep(0.05)
        finally:
            try:
                await transport.disconnect()
            finally:
                self._emit("log", f"Disconnected BWT901 {self.source_id}.")

    def _on_notification(self, _sender, data: bytearray) -> None:
        self.notification_count += 1
        packets = self.decoder.feed(data)
        self.decoded_frame_count += len(packets)
        if packets:
            self._last_frame_monotonic = time.monotonic()
        if not self.control.capture_event.is_set():
            return
        for packet in packets:
            self._append_packet(packet)

    def _append_packet(self, packet: BWT901Packet) -> None:
        self._times.append(self.control.clock.now())
        self._rows.append((packet.sequence, *packet.scaled_values))
        if len(self._rows) >= self.max_frames_per_batch:
            self._flush()

    def _flush(self) -> None:
        if not self._rows:
            return
        self.data_queue.put(StreamBlock(self.spec, tuple(self._times), tuple(self._rows)))
        self._times.clear()
        self._rows.clear()

    def _emit_health(self, now: float) -> None:
        elapsed = max(0.001, now - self._started_monotonic)
        age = None if self._last_frame_monotonic is None else now - self._last_frame_monotonic
        self._emit(
            "health",
            data={
                "status": "receiving" if self.control.capture_event.is_set() else "paused",
                "frames": self.decoded_frame_count,
                "parser_errors": 0,
                "skipped_bytes": self.decoder.skipped_bytes,
                "observed_rate_hz": self.decoded_frame_count / elapsed,
                "last_frame_age_s": age,
            },
        )

    def _emit(
        self,
        kind: str,
        message: str = "",
        *,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.event_queue.put(
            WorkerEvent(kind, message, data=data, source_id=f"bwt901.{self.source_id}")  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class BWT901Source:
    config: BWT901BLEConfig = field(default_factory=BWT901BLEConfig)

    name: ClassVar[SourceName] = "bwt901_ble"
    display_name: ClassVar[str] = "BWT901BLE IMU"
    supports_managed_lifecycle: ClassVar[bool] = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "config", self.config.normalized())

    def display_text(self) -> str:
        return self.config.display_text()

    def inspect_data(self) -> tuple[str, ...]:
        return (
            "Transport: reusable BLE GATT byte transport",
            "Protocol: DeviceInterface.bwt901_protocol.BWT901StreamDecoder",
            "Frame: 55 61 + nine little-endian signed Int16 values (20 bytes)",
            "Signals: acceleration(g), angular velocity(deg/s), angle(deg)",
            "Timing: shared host monotonic receive time; output rate is not assumed",
            f"Current config: {self.config.display_text()}",
        )

    def stream_specs(self) -> tuple[StreamSpec, ...]:
        return tuple(bwt901_stream_spec(device) for device in self.config.devices)

    def capture_metadata(self) -> dict[str, Any]:
        return {
            "transport": "ble",
            "config": asdict(self.config),
            "timestamp_note": (
                "time_s is the shared capture-relative host receive time; the BWT901 demo "
                "does not declare a device output rate or device timestamp."
            ),
        }

    def with_config(self, config: BWT901BLEConfig) -> "BWT901Source":
        return BWT901Source(config=config)

    def create_worker(
        self,
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        resume_state: CaptureResumeState = CaptureResumeState(),
        *,
        control: WorkerControl | None = None,
    ) -> SourceWorker:
        specs = self.stream_specs()
        workers = tuple(
            BWT901BLEWorker(
                config=self.config,
                device=device,
                spec=spec,
                data_queue=data_queue,
                event_queue=event_queue,
                stop_event=stop_event,
                resume_state=resume_state,
                control=control,
            )
            for device, spec in zip(self.config.devices, specs, strict=True)
        )
        return SourceWorkerGroup(workers, data_queue, event_queue, stop_event)


__all__ = [
    "BWT901BLEConfig",
    "BWT901BLEWorker",
    "BWT901DeviceConfig",
    "BWT901Source",
    "DEFAULT_BWT901_ADDRESS",
    "MAX_BWT901_DEVICES",
    "bwt901_stream_id",
    "bwt901_stream_spec",
]
