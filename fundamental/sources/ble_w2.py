"""RunE W2 sources over reusable serial or BLE byte transports."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar, Literal, cast

from DeviceInterface.w2_protocol import W2CommandBuilder, W2Packet, W2RmsPacket, W2StreamParser
from fundamental.messages import (
    DEFAULT_MAX_FRAMES_PER_BATCH,
    DEFAULT_SERIAL_PORT,
    DEFAULT_SERIAL_TIMEOUT,
    WorkerEvent,
)
from fundamental.sources.base import (
    SourceName,
    SourceWorker,
    SourceWorkerGroup,
    WorkerControl,
)
from fundamental.streams import CaptureResumeState, FieldSpec, StreamBlock, StreamSpec
from fundamental.transports import (
    BleGattConfig,
    BleGattTransport,
    SerialByteConfig,
    SerialByteTransport,
)

try:
    import serial
except ImportError:  # pragma: no cover - depends on local runtime
    serial = None

try:
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakBluetoothNotAvailableError, BleakDeviceNotFoundError
except ImportError:  # pragma: no cover - depends on local runtime
    BleakClient = None
    BleakScanner = None
    BleakBluetoothNotAvailableError = None
    BleakDeviceNotFoundError = None


DEFAULT_W2_DEVICE_NAME = "RunE W2"
DEFAULT_W2_ADDRESS = ""
DEFAULT_W2_NOTIFY_UUID = "0000FFF4-0000-1000-8000-00805F9B34FB"
DEFAULT_W2_WRITE_UUID = "0000FFF3-0000-1000-8000-00805F9B34FB"
DEFAULT_W2_SAMPLE_RATE_HZ = 1000.0
DEFAULT_W2_SERIAL_BAUD_RATE = 256000
MAX_W2_DEVICES = 5
W2_MODE_NAMES = ("emg_raw", "emg_rms", "eeg_raw")
W2_TRANSPORT_NAMES = ("ble", "serial")
W2_STREAM_ID = "ble_w2.w2_1.signal"
W2TransportName = Literal["ble", "serial"]
W2ModeName = Literal["emg_raw", "emg_rms", "eeg_raw"]


@dataclass(frozen=True)
class W2SerialDeviceConfig:
    """Compatibility input for the previous serial-only multi-device editor."""

    channel_id: str = "ch1"
    port: str = DEFAULT_SERIAL_PORT

    def normalized(self) -> "W2SerialDeviceConfig":
        return W2SerialDeviceConfig(self.channel_id.strip(), self.port.strip())

    def display_text(self) -> str:
        return f"{self.channel_id or '-'}={self.port or '-'}"


@dataclass(frozen=True)
class W2DeviceConfig:
    """Connection choice for one physical W2 device."""

    device_id: str = "w2_1"
    transport: W2TransportName = "serial"
    port: str = DEFAULT_SERIAL_PORT
    address: str = DEFAULT_W2_ADDRESS
    device_name_filter: str = DEFAULT_W2_DEVICE_NAME

    def normalized(self) -> "W2DeviceConfig":
        transport = self.transport if self.transport in W2_TRANSPORT_NAMES else "serial"
        return W2DeviceConfig(
            device_id=self.device_id.strip(),
            transport=cast(W2TransportName, transport),
            port=self.port.strip(),
            address=self.address.strip(),
            device_name_filter=self.device_name_filter.strip(),
        )

    def display_text(self) -> str:
        if self.transport == "serial":
            target = self.port or "-"
        else:
            target = self.address or f"name contains {self.device_name_filter!r}"
        return f"{self.device_id or '-'}={self.transport}:{target}"


# Edit this tuple to change the W2 rows initially shown in Source Config.
DEFAULT_W2_DEVICES = (
    W2DeviceConfig("w2_1", "serial", port="COM9", device_name_filter="RunE W21"),
    W2DeviceConfig("w2_2", "serial", port="COM11", device_name_filter="RunE W22"),
    W2DeviceConfig("w2_3", "serial", port="COM12", device_name_filter="RunE W23"),
    W2DeviceConfig("w2_4", "serial", port="COM13", device_name_filter="RunE W24"),
)


@dataclass(frozen=True)
class W2Config:
    """Shared W2 protocol settings plus zero or more explicit device rows."""

    address: str = DEFAULT_W2_ADDRESS
    device_name_filter: str = DEFAULT_W2_DEVICE_NAME
    notify_uuid: str = DEFAULT_W2_NOTIFY_UUID
    write_uuid: str = DEFAULT_W2_WRITE_UUID
    mode: W2ModeName = "emg_raw"
    sample_rate_hz: float = DEFAULT_W2_SAMPLE_RATE_HZ
    scan_timeout_s: float = 5.0
    transport: W2TransportName = "ble"
    serial_baud_rate: int = DEFAULT_W2_SERIAL_BAUD_RATE
    serial_timeout_s: float = DEFAULT_SERIAL_TIMEOUT
    serial_devices: tuple[W2SerialDeviceConfig, ...] = field(
        default_factory=lambda: (W2SerialDeviceConfig(),)
    )
    devices: tuple[W2DeviceConfig, ...] = ()

    def normalized(self) -> "W2Config":
        mode = self.mode if self.mode in W2_MODE_NAMES else "emg_raw"
        transport = self.transport if self.transport in W2_TRANSPORT_NAMES else "ble"
        return W2Config(
            address=self.address.strip(),
            device_name_filter=self.device_name_filter.strip(),
            notify_uuid=self.notify_uuid.strip(),
            write_uuid=self.write_uuid.strip(),
            mode=cast(W2ModeName, mode),
            sample_rate_hz=max(0.001, float(self.sample_rate_hz)),
            scan_timeout_s=max(0.1, float(self.scan_timeout_s)),
            transport=cast(W2TransportName, transport),
            serial_baud_rate=max(1, int(self.serial_baud_rate)),
            serial_timeout_s=max(0.001, float(self.serial_timeout_s)),
            serial_devices=tuple(item.normalized() for item in self.serial_devices),
            devices=tuple(item.normalized() for item in self.devices),
        )

    def effective_devices(self) -> tuple[W2DeviceConfig, ...]:
        if self.devices:
            return self.devices
        if self.transport == "serial":
            return tuple(
                W2DeviceConfig(device.channel_id, "serial", port=device.port)
                for device in self.serial_devices
            )
        return (
            W2DeviceConfig(
                "w2_1",
                "ble",
                address=self.address,
                device_name_filter=self.device_name_filter,
            ),
        )

    def display_text(self) -> str:
        devices = "; ".join(device.display_text() for device in self.effective_devices()) or "none"
        return f"W2 [{devices}], mode {self.mode}, {self.sample_rate_hz:g} Hz"


# Compatibility alias for callers written before Serial and BLE became
# per-device interface choices.
W2BLEConfig = W2Config


def default_w2_config() -> W2Config:
    """Current experiment defaults shown in the W2 source editor."""

    return W2Config(devices=DEFAULT_W2_DEVICES)


def w2_stream_id(device_id: str) -> str:
    return f"ble_w2.{device_id.strip()}.signal"


def w2_serial_stream_id(channel_id: str) -> str:
    """Compatibility helper; stream identity no longer depends on transport."""

    return w2_stream_id(channel_id)


def w2_stream_spec(
    config: W2Config,
    *,
    stream_id: str = W2_STREAM_ID,
    channel_label: str | None = None,
) -> StreamSpec:
    kind = {"emg_raw": "emg", "emg_rms": "generic", "eeg_raw": "eeg"}[config.mode]
    label = {"emg_raw": "EMG Raw", "emg_rms": "EMG RMS", "eeg_raw": "EEG Raw"}[config.mode]
    display_label = f"{channel_label} {label}" if channel_label else label
    return StreamSpec(
        stream_id=stream_id,
        display_name=f"W2 {display_label}",
        nominal_rate_hz=config.sample_rate_hz,
        fields=(
            FieldSpec(
                "value",
                display_label,
                unit="code",
                signal_kind=kind,
                default_plot=True,
            ),
        ),
        time_source="configured_rate_anchored_to_shared_host_monotonic_time",
    )


class W2StreamAdapter:
    """Convert unchanged W2 protocol packets to a device-specific stream."""

    def __init__(
        self,
        spec: StreamSpec,
        sample_rate_hz: float = DEFAULT_W2_SAMPLE_RATE_HZ,
        resume_state: CaptureResumeState = CaptureResumeState(),
    ) -> None:
        self.spec = spec
        self.sample_rate_hz = max(0.001, float(sample_rate_hz))
        cursor = resume_state.cursor(spec.stream_id)
        self._next_time_s = (
            cursor.last_time_s + 1.0 / self.sample_rate_hz if cursor is not None else 0.0
        )
        self._anchored = cursor is not None

    def packet_to_block(
        self,
        packet: W2Packet,
        *,
        receive_time_s: float | None = None,
    ) -> StreamBlock:
        values = (
            (float(packet.rms),)
            if isinstance(packet, W2RmsPacket)
            else tuple(float(value) for value in packet.values)
        )
        if not self._anchored and receive_time_s is not None:
            self._next_time_s = max(
                0.0,
                float(receive_time_s) - max(0, len(values) - 1) / self.sample_rate_hz,
            )
            self._anchored = True
        times = tuple(
            self._next_time_s + index / self.sample_rate_hz for index in range(len(values))
        )
        self._next_time_s += len(values) / self.sample_rate_hz
        return StreamBlock(self.spec, times, tuple((value,) for value in values))


class _W2StreamWorker(threading.Thread):
    def __init__(
        self,
        *,
        thread_name: str,
        config: W2Config,
        device: W2DeviceConfig,
        spec: StreamSpec,
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        resume_state: CaptureResumeState,
        max_frames_per_batch: int,
        control: WorkerControl | None,
    ) -> None:
        super().__init__(name=thread_name, daemon=True)
        self.config = config.normalized()
        self.device = device.normalized()
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
        self.parser = W2StreamParser()
        self.adapter = W2StreamAdapter(spec, self.config.sample_rate_hz, resume_state)
        self._times: list[float] = []
        self._rows: list[tuple[int | float, ...]] = []
        self._last_logged_parser_counters = (0, 0, 0)
        self.rx_byte_count = 0
        self.decoded_packet_count = 0
        self._active_started_monotonic: float | None = None
        self._completed_active_s = 0.0
        self._last_packet_monotonic: float | None = None

    @property
    def source_id(self) -> str:
        return self.device.device_id

    def _consume_data(self, received_data: bytes | bytearray) -> None:
        self.rx_byte_count += len(received_data)
        packets = self.parser.feed(bytes(received_data))
        self.decoded_packet_count += len(packets)
        if packets:
            self._last_packet_monotonic = time.monotonic()
        if self.control.capture_event.is_set():
            receive_time = self.control.clock.now()
            for packet in packets:
                block = self.adapter.packet_to_block(packet, receive_time_s=receive_time)
                self._times.extend(block.time_s)
                self._rows.extend(block.rows)
                if len(self._rows) >= self.max_frames_per_batch:
                    self._flush()
        self._log_parser_counters_if_changed()

    def _log_parser_counters_if_changed(self) -> None:
        counters = (
            self.parser.bad_checksum_count,
            self.parser.bad_tail_count,
            self.parser.bad_payload_count,
        )
        if counters == self._last_logged_parser_counters:
            return
        self._last_logged_parser_counters = counters
        if any(counters):
            self._emit(
                "log",
                f"W2 parser counters: bad_checksum={counters[0]}, "
                f"bad_tail={counters[1]}, bad_payload={counters[2]}.",
            )

    def _flush(self) -> None:
        if not self._rows:
            return
        self.data_queue.put(StreamBlock(self.spec, tuple(self._times), tuple(self._rows)))
        self._times.clear()
        self._rows.clear()

    def _reset_partial_frame(self) -> None:
        self.parser.buffer.clear()

    def _begin_active_interval(self, now: float | None = None) -> None:
        if self._active_started_monotonic is None:
            self._active_started_monotonic = time.monotonic() if now is None else float(now)

    def _end_active_interval(self, now: float | None = None) -> None:
        if self._active_started_monotonic is None:
            return
        stopped = time.monotonic() if now is None else float(now)
        self._completed_active_s += max(0.0, stopped - self._active_started_monotonic)
        self._active_started_monotonic = None

    def _active_elapsed(self, now: float | None = None) -> float:
        elapsed = self._completed_active_s
        if self._active_started_monotonic is not None:
            current = time.monotonic() if now is None else float(now)
            elapsed += max(0.0, current - self._active_started_monotonic)
        return elapsed

    def _emit_health(self, now: float | None = None) -> None:
        current = time.monotonic() if now is None else float(now)
        elapsed = self._active_elapsed(current)
        age = (
            None
            if self._last_packet_monotonic is None
            else current - self._last_packet_monotonic
        )
        bad_frames = (
            self.parser.bad_checksum_count
            + self.parser.bad_tail_count
            + self.parser.bad_payload_count
        )
        self._emit(
            "health",
            data={
                "status": "receiving" if self.control.capture_event.is_set() else "paused",
                "frames": self.decoded_packet_count,
                "parser_errors": bad_frames,
                "skipped_bytes": self.parser.skipped_bytes,
                "observed_rate_hz": self.decoded_packet_count / elapsed if elapsed else 0.0,
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
            WorkerEvent(kind, message, data=data, source_id=f"ble_w2.{self.source_id}")  # type: ignore[arg-type]
        )


class SerialW2Worker(_W2StreamWorker):
    """Keep one W2 serial port open across pause/resume."""

    def __init__(
        self,
        config: W2Config,
        serial_config: W2SerialDeviceConfig | None,
        spec: StreamSpec,
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        resume_state: CaptureResumeState = CaptureResumeState(),
        max_frames_per_batch: int = DEFAULT_MAX_FRAMES_PER_BATCH,
        *,
        device: W2DeviceConfig | None = None,
        control: WorkerControl | None = None,
        transport_factory=SerialByteTransport,
    ) -> None:
        if device is None:
            legacy = (serial_config or W2SerialDeviceConfig()).normalized()
            device = W2DeviceConfig(legacy.channel_id, "serial", port=legacy.port)
        self.serial_config = W2SerialDeviceConfig(device.device_id, device.port)
        self.transport_factory = transport_factory
        self.transport = None
        self.read_count = 0
        self.first_rx_preview = ""
        super().__init__(
            thread_name=f"SerialW2Worker-{device.device_id}",
            config=config,
            device=device,
            spec=spec,
            data_queue=data_queue,
            event_queue=event_queue,
            stop_event=stop_event,
            resume_state=resume_state,
            max_frames_per_batch=max_frames_per_batch,
            control=control,
        )

    def run(self) -> None:
        transport = self.transport_factory(
            SerialByteConfig(
                self.device.port,
                self.config.serial_baud_rate,
                self.config.serial_timeout_s,
            ),
            backend=serial,
        )
        self.transport = transport
        active = False
        try:
            transport.open()
            transport.reset_input_buffer()
            self.ready_event.set()
            self._emit(
                "ready",
                f"W2 {self.source_id} ready on {self.device.port} @ "
                f"{self.config.serial_baud_rate}.",
                data={"transport": "serial", "port": self.device.port},
            )
            health_deadline = time.monotonic() + 1.0
            while not self.stop_event.is_set():
                requested = self.control.capture_event.is_set()
                if requested and not active:
                    transport.reset_input_buffer()
                    self._reset_partial_frame()
                    transport.write(W2CommandBuilder.start_for_mode(self.config.mode))
                    active = True
                    self._begin_active_interval()
                    self._emit("log", f"Started W2 {self.source_id} mode {self.config.mode}.")
                elif not requested and active:
                    self._end_active_interval()
                    transport.write(W2CommandBuilder.stop_collect())
                    active = False
                    self._flush()
                    self._emit("log", f"Paused W2 {self.source_id}; serial port remains open.")

                if not active:
                    self.stop_event.wait(0.02)
                else:
                    chunk = transport.read(512)
                    if chunk:
                        self.read_count += 1
                        if not self.first_rx_preview:
                            self.first_rx_preview = chunk[:32].hex(" ")
                            self._emit(
                                "log",
                                f"Received first W2 serial chunk ({len(chunk)} bytes): "
                                f"{self.first_rx_preview}",
                            )
                        self._consume_data(chunk)
                    else:
                        self._flush()

                now = time.monotonic()
                if now >= health_deadline:
                    self._emit_health()
                    health_deadline = now + 1.0
        except Exception as exc:  # pragma: no cover - hardware dependent
            self._emit("error", f"W2 serial {self.source_id} failed: {type(exc).__name__}: {exc}")
        finally:
            if active:
                self._end_active_interval()
                try:
                    transport.write(W2CommandBuilder.stop_collect())
                except Exception as exc:  # pragma: no cover - disconnect dependent
                    self._emit("log", f"W2 {self.source_id} stop warning: {exc}")
            self._flush()
            try:
                transport.close()
                self._emit("log", f"Closed W2 serial port for {self.source_id}.")
            except Exception as exc:  # pragma: no cover - hardware dependent
                self._emit("log", f"W2 {self.source_id} close warning: {exc}")

class BLEW2Worker(_W2StreamWorker):
    """Keep one W2 BLE connection and notification subscription across pause/resume."""

    def __init__(
        self,
        config: W2Config,
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        resume_state: CaptureResumeState = CaptureResumeState(),
        max_frames_per_batch: int = DEFAULT_MAX_FRAMES_PER_BATCH,
        *,
        device: W2DeviceConfig | None = None,
        spec: StreamSpec | None = None,
        control: WorkerControl | None = None,
        transport_factory=BleGattTransport,
    ) -> None:
        if device is None:
            device = next(
                (item for item in config.effective_devices() if item.transport == "ble"),
                W2DeviceConfig(
                    "w2_1",
                    "ble",
                    address=config.address,
                    device_name_filter=config.device_name_filter,
                ),
            )
        self.transport_factory = transport_factory
        self.transport = None
        self.notification_count = 0
        self._resolved_device = None
        super().__init__(
            thread_name=f"BLEW2Worker-{device.device_id}",
            config=config,
            device=device,
            spec=spec or w2_stream_spec(config, stream_id=w2_stream_id(device.device_id)),
            data_queue=data_queue,
            event_queue=event_queue,
            stop_event=stop_event,
            resume_state=resume_state,
            max_frames_per_batch=max_frames_per_batch,
            control=control,
        )

    def run(self) -> None:
        try:
            asyncio.run(self._run_async())
        except Exception as exc:  # pragma: no cover - hardware/event-loop dependent
            if BleakBluetoothNotAvailableError is not None and isinstance(
                exc, BleakBluetoothNotAvailableError
            ):
                message = "Bluetooth is unavailable or powered off."
            else:
                message = f"W2 BLE {self.source_id} failed: {type(exc).__name__}: {exc}"
            self._emit("error", message)
        finally:
            self._flush()

    async def _run_async(self) -> None:
        transport = self.transport_factory(
            BleGattConfig(
                address=self.device.address,
                name_filter=self.device.device_name_filter,
                scan_timeout_s=self.config.scan_timeout_s,
            ),
            log=lambda message: self._emit("log", message),
            client_factory=BleakClient,
            scanner=BleakScanner,
            not_found_error=BleakDeviceNotFoundError,
        )
        self.transport = transport
        active = False
        try:
            await transport.connect()
            self._resolved_device = transport.device
            if self.stop_event.is_set():
                return
            await transport.start_notify(self.config.notify_uuid, self._handle_notification)
            self.ready_event.set()
            self._emit(
                "ready",
                f"W2 {self.source_id} ready at {transport.resolved_address}.",
                data={"transport": "ble", "address": transport.resolved_address},
            )
            health_deadline = time.monotonic() + 1.0
            while not self.stop_event.is_set():
                if transport.disconnected_event.is_set() or not transport.is_connected:
                    raise ConnectionError("BLE connection was lost.")
                requested = self.control.capture_event.is_set()
                if requested and not active:
                    self._reset_partial_frame()
                    await transport.write(
                        self.config.write_uuid,
                        W2CommandBuilder.start_for_mode(self.config.mode),
                    )
                    active = True
                    self._begin_active_interval()
                    self._emit("log", f"Started W2 {self.source_id} mode {self.config.mode}.")
                elif not requested and active:
                    self._end_active_interval()
                    await transport.write(self.config.write_uuid, W2CommandBuilder.stop_collect())
                    active = False
                    self._flush()
                    self._emit("log", f"Paused W2 {self.source_id}; BLE remains connected.")

                self._flush()
                now = time.monotonic()
                if now >= health_deadline:
                    self._emit_health()
                    health_deadline = now + 1.0
                await asyncio.sleep(0.05)
        finally:
            if active:
                self._end_active_interval()
                try:
                    await transport.write(self.config.write_uuid, W2CommandBuilder.stop_collect())
                except Exception as exc:  # pragma: no cover - disconnect dependent
                    self._emit("log", f"W2 {self.source_id} stop warning: {exc}")
            try:
                await transport.disconnect()
            finally:
                self._emit("log", f"Disconnected W2 BLE {self.source_id}.")

    def _handle_notification(self, _sender, received_data: bytearray) -> None:
        self.notification_count += 1
        if self.notification_count == 1:
            self._emit(
                "log",
                f"Received first W2 notification ({len(received_data)} bytes): "
                f"{bytes(received_data[:32]).hex(' ')}",
            )
        self._consume_data(received_data)

class W2WorkerGroup(SourceWorkerGroup):
    """Compatibility name for the shared source-worker group."""


@dataclass(frozen=True)
class BLEW2Source:
    config: W2Config = field(default_factory=default_w2_config)

    name: ClassVar[SourceName] = "ble_w2"
    display_name: ClassVar[str] = "W2 (BLE / Serial)"
    supports_managed_lifecycle: ClassVar[bool] = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "config", self.config.normalized())

    def display_text(self) -> str:
        return self.config.display_text()

    def inspect_data(self) -> tuple[str, ...]:
        return (
            "Transports: reusable SerialByteTransport / BleGattTransport",
            "Protocol parser: DeviceInterface.w2_protocol.W2StreamParser (unchanged)",
            "One device-specific StreamBlock per configured W2",
            "Schema: time_s, value (no ADS-shaped zero padding)",
            "Pause: collection command stops while the transport remains connected",
            f"Current config: {self.config.display_text()}",
        )

    def stream_specs(self) -> tuple[StreamSpec, ...]:
        return tuple(
            w2_stream_spec(
                self.config,
                stream_id=w2_stream_id(device.device_id),
                channel_label=device.device_id,
            )
            for device in self.config.effective_devices()
        )

    def capture_metadata(self) -> dict[str, Any]:
        return {
            "protocol": "w2",
            "config": asdict(self.config),
            "devices": [asdict(device) for device in self.config.effective_devices()],
        }

    def with_config(self, config: W2Config) -> "BLEW2Source":
        return BLEW2Source(config=config)

    def create_worker(
        self,
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        resume_state: CaptureResumeState = CaptureResumeState(),
        *,
        control: WorkerControl | None = None,
    ) -> SourceWorker:
        devices = self.config.effective_devices()
        specs = self.stream_specs()
        workers: list[SourceWorker] = []
        for device, spec in zip(devices, specs, strict=True):
            if device.transport == "serial":
                workers.append(
                    SerialW2Worker(
                        self.config,
                        W2SerialDeviceConfig(device.device_id, device.port),
                        spec,
                        data_queue,
                        event_queue,
                        stop_event,
                        resume_state,
                        device=device,
                        control=control,
                    )
                )
            else:
                workers.append(
                    BLEW2Worker(
                        self.config,
                        data_queue,
                        event_queue,
                        stop_event,
                        resume_state,
                        device=device,
                        spec=spec,
                        control=control,
                    )
                )
        if len(workers) == 1:
            return workers[0]
        return W2WorkerGroup(tuple(workers), data_queue, event_queue, stop_event)


__all__ = [
    "BLEW2Source",
    "BLEW2Worker",
    "DEFAULT_W2_DEVICES",
    "DEFAULT_W2_SERIAL_BAUD_RATE",
    "MAX_W2_DEVICES",
    "SerialW2Worker",
    "W2BLEConfig",
    "W2Config",
    "W2DeviceConfig",
    "W2SerialDeviceConfig",
    "W2StreamAdapter",
    "W2WorkerGroup",
    "W2_MODE_NAMES",
    "W2_TRANSPORT_NAMES",
    "default_w2_config",
    "w2_serial_stream_id",
    "w2_stream_id",
    "w2_stream_spec",
]
