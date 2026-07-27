"""BLE and serial source for RunE W2 devices."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar, Literal

from DeviceInterface.w2_protocol import W2CommandBuilder, W2Packet, W2RmsPacket, W2StreamParser
from fundamental.messages import (
    DEFAULT_MAX_FRAMES_PER_BATCH,
    DEFAULT_SERIAL_PORT,
    DEFAULT_SERIAL_TIMEOUT,
    WorkerEvent,
)
from fundamental.sources.base import SourceName, SourceWorker
from fundamental.streams import CaptureResumeState, FieldSpec, StreamBlock, StreamSpec

try:
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakBluetoothNotAvailableError
except ImportError:  # pragma: no cover - depends on local runtime
    BleakClient = None
    BleakScanner = None
    BleakBluetoothNotAvailableError = None

try:
    import serial
except ImportError:  # pragma: no cover - depends on local runtime
    serial = None


DEFAULT_W2_DEVICE_NAME = "RunE W2"
# The address in device_host_demo/main.py belongs to the demo unit. Scanning by
# the advertised RunE W2 name is a safer default for a different physical unit.
DEFAULT_W2_ADDRESS = ""
DEFAULT_W2_NOTIFY_UUID = "0000FFF4-0000-1000-8000-00805F9B34FB"
DEFAULT_W2_WRITE_UUID = "0000FFF3-0000-1000-8000-00805F9B34FB"
DEFAULT_W2_SAMPLE_RATE_HZ = 1000.0
DEFAULT_W2_SERIAL_BAUD_RATE = 25600
W2_MODE_NAMES = ("emg_raw", "emg_rms", "eeg_raw")
W2_TRANSPORT_NAMES = ("ble", "serial")
W2_STREAM_ID = "ble_w2.signal"
W2TransportName = Literal["ble", "serial"]


@dataclass(frozen=True)
class W2SerialDeviceConfig:
    """Connection settings for one W2 serial device/channel."""

    channel_id: str = "ch1"
    port: str = DEFAULT_SERIAL_PORT

    def normalized(self) -> "W2SerialDeviceConfig":
        return W2SerialDeviceConfig(
            channel_id=self.channel_id.strip(),
            port=self.port.strip(),
        )

    def display_text(self) -> str:
        return f"{self.channel_id or '-'}={self.port or '-'}"


@dataclass(frozen=True)
class W2BLEConfig:
    """Connection and acquisition settings for the W2 source."""

    address: str = DEFAULT_W2_ADDRESS
    device_name_filter: str = DEFAULT_W2_DEVICE_NAME
    notify_uuid: str = DEFAULT_W2_NOTIFY_UUID
    write_uuid: str = DEFAULT_W2_WRITE_UUID
    mode: Literal["emg_raw", "emg_rms", "eeg_raw"] = "emg_raw"
    sample_rate_hz: float = DEFAULT_W2_SAMPLE_RATE_HZ
    scan_timeout_s: float = 5.0
    transport: W2TransportName = "ble"
    serial_baud_rate: int = DEFAULT_W2_SERIAL_BAUD_RATE
    serial_timeout_s: float = DEFAULT_SERIAL_TIMEOUT
    serial_devices: tuple[W2SerialDeviceConfig, ...] = field(
        default_factory=lambda: (W2SerialDeviceConfig(),)
    )

    def normalized(self) -> "W2BLEConfig":
        mode = self.mode if self.mode in W2_MODE_NAMES else "emg_raw"
        transport: W2TransportName = (
            self.transport if self.transport in W2_TRANSPORT_NAMES else "ble"
        )
        return W2BLEConfig(
            transport=transport,
            address=self.address.strip(),
            device_name_filter=self.device_name_filter.strip(),
            notify_uuid=self.notify_uuid.strip(),
            write_uuid=self.write_uuid.strip(),
            mode=mode,
            sample_rate_hz=max(0.001, float(self.sample_rate_hz)),
            scan_timeout_s=max(0.1, float(self.scan_timeout_s)),
            serial_baud_rate=max(1, int(self.serial_baud_rate)),
            serial_timeout_s=max(0.001, float(self.serial_timeout_s)),
            serial_devices=tuple(device.normalized() for device in self.serial_devices),
        )

    def display_text(self) -> str:
        if self.transport == "serial":
            devices = "; ".join(device.display_text() for device in self.serial_devices) or "none"
            return (
                f"W2 Serial [{devices}] @ {self.serial_baud_rate}, "
                f"timeout {self.serial_timeout_s:.3f}s, mode {self.mode}"
            )
        target = self.address.strip() or f"name contains {self.device_name_filter!r}"
        return f"W2 BLE {target}, notify {self.notify_uuid}, write {self.write_uuid}, mode {self.mode}"


def w2_serial_stream_id(channel_id: str) -> str:
    """Build the stable stream identity for one configured serial channel."""

    return f"ble_w2.serial.{channel_id.strip()}.signal"


def w2_stream_spec(
    config: W2BLEConfig,
    *,
    stream_id: str = W2_STREAM_ID,
    channel_label: str | None = None,
) -> StreamSpec:
    kind = {
        "emg_raw": "emg",
        "emg_rms": "generic",
        "eeg_raw": "eeg",
    }[config.mode]
    label = {
        "emg_raw": "EMG Raw",
        "emg_rms": "EMG RMS",
        "eeg_raw": "EEG Raw",
    }[config.mode]
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
        time_source="host_generated_at_configured_rate",
    )


class W2StreamAdapter:
    """Convert parsed W2 packets to the generic stream contract."""

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
            cursor.last_time_s + 1.0 / self.sample_rate_hz
            if cursor is not None
            else 0.0
        )

    def packet_to_block(self, packet: W2Packet) -> StreamBlock:
        if isinstance(packet, W2RmsPacket):
            values = (float(packet.rms),)
        else:
            values = tuple(float(value) for value in packet.values)
        times = tuple(
            self._next_time_s + index / self.sample_rate_hz
            for index in range(len(values))
        )
        self._next_time_s += len(values) / self.sample_rate_hz
        return StreamBlock(self.spec, times, tuple((value,) for value in values))


class _W2StreamWorker(threading.Thread):
    """Shared W2 byte-stream decoding and batching for every transport."""

    def __init__(
        self,
        *,
        thread_name: str,
        config: W2BLEConfig,
        spec: StreamSpec,
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        resume_state: CaptureResumeState,
        max_frames_per_batch: int,
    ) -> None:
        super().__init__(name=thread_name, daemon=True)
        self.config = config
        self.data_queue = data_queue
        self.event_queue = event_queue
        self.stop_event = stop_event
        self.max_frames_per_batch = max(1, int(max_frames_per_batch))
        self.parser = W2StreamParser()
        self.spec = spec
        self.adapter = W2StreamAdapter(
            spec=self.spec,
            sample_rate_hz=config.sample_rate_hz,
            resume_state=resume_state,
        )
        self._times: list[float] = []
        self._rows: list[tuple[int | float, ...]] = []
        self._last_logged_parser_counters = (0, 0, 0)
        self.rx_byte_count = 0
        self.decoded_packet_count = 0

    def _consume_data(self, received_data: bytes | bytearray) -> None:
        self.rx_byte_count += len(received_data)
        packets = self.parser.feed(bytes(received_data))
        self.decoded_packet_count += len(packets)
        for packet in packets:
            block = self.adapter.packet_to_block(packet)
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
            self.event_queue.put(
                WorkerEvent(
                    "log",
                    f"W2 parser counters for {self.spec.stream_id}: "
                    f"bad_checksum={self.parser.bad_checksum_count}, "
                    f"bad_tail={self.parser.bad_tail_count}, "
                    f"bad_payload={self.parser.bad_payload_count}.",
                )
            )

    def _flush(self) -> None:
        if not self._rows:
            return
        self.data_queue.put(StreamBlock(self.spec, tuple(self._times), tuple(self._rows)))
        self._times.clear()
        self._rows.clear()


class BLEW2Worker(_W2StreamWorker):
    """Connect to a W2 BLE device and publish StreamBlock objects."""

    def __init__(
        self,
        config: W2BLEConfig,
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        resume_state: CaptureResumeState = CaptureResumeState(),
        max_frames_per_batch: int = DEFAULT_MAX_FRAMES_PER_BATCH,
    ) -> None:
        super().__init__(
            thread_name="BLEW2Worker",
            config=config,
            spec=w2_stream_spec(config),
            data_queue=data_queue,
            event_queue=event_queue,
            stop_event=stop_event,
            resume_state=resume_state,
            max_frames_per_batch=max_frames_per_batch,
        )
        self.notification_count = 0
        self._resolved_device = None

    def run(self) -> None:
        if BleakClient is None or BleakScanner is None:
            self.event_queue.put(WorkerEvent("error", "bleak is not installed; W2 BLE acquisition is unavailable."))
            return

        try:
            asyncio.run(self._run_async())
        except Exception as exc:  # pragma: no cover - hardware/event-loop dependent
            if BleakBluetoothNotAvailableError is not None and isinstance(
                exc, BleakBluetoothNotAvailableError
            ):
                message = (
                    "Bluetooth is unavailable or powered off. "
                    "Turn on the Windows Bluetooth radio and retry."
                )
            else:
                message = f"W2 BLE worker failed: {type(exc).__name__}: {exc}"
            self.event_queue.put(WorkerEvent("error", message))

    async def _run_async(self) -> None:
        address = await self._resolve_address()
        if self.stop_event.is_set():
            self.event_queue.put(WorkerEvent("log", "W2 BLE start cancelled before connection."))
            return
        if not address:
            self.event_queue.put(WorkerEvent("error", "No W2 BLE device matched the configured address/name."))
            return

        # Reuse the BLEDevice returned by the scan. Passing only its address to
        # BleakClient can trigger a second implicit discovery on Windows.
        async with BleakClient(self._resolved_device or address) as client:
            self.event_queue.put(WorkerEvent("log", f"Connected to W2 BLE device {address}."))
            if self.stop_event.is_set():
                self.event_queue.put(WorkerEvent("log", "W2 BLE start cancelled after connection."))
                return

            notify_started = False
            collection_started = False
            try:
                await client.start_notify(self.config.notify_uuid, self._handle_notification)
                notify_started = True
                self.event_queue.put(WorkerEvent("log", f"Subscribed to W2 notifications {self.config.notify_uuid}."))
                if self.stop_event.is_set():
                    self.event_queue.put(WorkerEvent("log", "W2 BLE start cancelled before collection."))
                    return

                await client.write_gatt_char(self.config.write_uuid, W2CommandBuilder.start_for_mode(self.config.mode))
                collection_started = True
                self.event_queue.put(WorkerEvent("log", f"Started W2 collection mode {self.config.mode}."))

                diagnostic_deadline = asyncio.get_running_loop().time() + 2.0
                while not self.stop_event.is_set():
                    self._flush()
                    now = asyncio.get_running_loop().time()
                    if now >= diagnostic_deadline and self.decoded_packet_count == 0:
                        self.event_queue.put(
                            WorkerEvent(
                                "log",
                                "W2 collection started but no data frame has decoded: "
                                f"notifications={self.notification_count}, rx_bytes={self.rx_byte_count}, "
                                f"skipped={self.parser.skipped_bytes}, "
                                f"unsupported={self.parser.unsupported_frame_count}, "
                                f"bad_checksum={self.parser.bad_checksum_count}, "
                                f"bad_tail={self.parser.bad_tail_count}, "
                                f"bad_payload={self.parser.bad_payload_count}.",
                            )
                        )
                        diagnostic_deadline = now + 5.0
                    await asyncio.sleep(0.05)
            finally:
                if notify_started:
                    await self._stop_client(client, send_stop_command=collection_started)

    async def _stop_client(self, client, send_stop_command: bool = True) -> None:
        if send_stop_command:
            try:
                await client.write_gatt_char(self.config.write_uuid, W2CommandBuilder.stop_collect())
            except Exception as exc:  # pragma: no cover - hardware/disconnect dependent
                self.event_queue.put(WorkerEvent("error", f"Failed to send W2 stop command: {exc}"))
        try:
            await client.stop_notify(self.config.notify_uuid)
        except Exception as exc:  # pragma: no cover - hardware/disconnect dependent
            self.event_queue.put(WorkerEvent("error", f"Failed to stop W2 notifications: {exc}"))
        self._flush()
        self.event_queue.put(WorkerEvent("log", "Stopped W2 BLE collection."))

    async def _resolve_address(self) -> str | None:
        configured = self.config.address.strip()
        if configured:
            self.event_queue.put(
                WorkerEvent("log", f"Scanning for configured W2 address {configured}...")
            )
            device = await BleakScanner.find_device_by_address(
                configured, timeout=self.config.scan_timeout_s
            )
        else:
            name_filter = self.config.device_name_filter
            self.event_queue.put(
                WorkerEvent("log", f"Scanning for BLE device name containing {name_filter!r}...")
            )
            folded_filter = name_filter.casefold()
            device = await BleakScanner.find_device_by_filter(
                lambda candidate, advertisement: (
                    folded_filter in (candidate.name or "").casefold()
                    or folded_filter in (advertisement.local_name or "").casefold()
                ),
                timeout=self.config.scan_timeout_s,
            )
        if device is None:
            return None
        self._resolved_device = device
        self.event_queue.put(
            WorkerEvent("log", f"Found W2 BLE device {device.name or '-'} at {device.address}.")
        )
        return str(device.address)

    def _handle_notification(self, _sender, received_data: bytearray) -> None:
        self.notification_count += 1
        if self.notification_count == 1:
            preview = bytes(received_data[:32]).hex(" ")
            self.event_queue.put(
                WorkerEvent(
                    "log",
                    f"Received first W2 notification ({len(received_data)} bytes): {preview}",
                )
            )
        self._consume_data(received_data)


class SerialW2Worker(_W2StreamWorker):
    """Read one W2 device through pyserial using the unchanged W2 protocol."""

    def __init__(
        self,
        config: W2BLEConfig,
        serial_config: W2SerialDeviceConfig,
        spec: StreamSpec,
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        resume_state: CaptureResumeState = CaptureResumeState(),
        max_frames_per_batch: int = DEFAULT_MAX_FRAMES_PER_BATCH,
    ) -> None:
        self.serial_config = serial_config.normalized()
        super().__init__(
            thread_name=f"SerialW2Worker-{self.serial_config.channel_id}",
            config=config,
            spec=spec,
            data_queue=data_queue,
            event_queue=event_queue,
            stop_event=stop_event,
            resume_state=resume_state,
            max_frames_per_batch=max_frames_per_batch,
        )
        self.read_count = 0
        self.first_rx_preview = ""

    def run(self) -> None:
        if serial is None:
            self.event_queue.put(
                WorkerEvent("error", "pyserial is not installed; W2 serial acquisition is unavailable.")
            )
            return
        if not self.serial_config.port:
            self.event_queue.put(
                WorkerEvent("error", f"W2 serial port is empty for {self.serial_config.channel_id}.")
            )
            return
        if self.stop_event.is_set():
            self.event_queue.put(
                WorkerEvent("log", f"W2 serial start cancelled for {self.serial_config.channel_id}.")
            )
            return

        serial_handle = None
        collection_started = False
        try:
            serial_handle = serial.Serial(
                self.serial_config.port,
                self.config.serial_baud_rate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.config.serial_timeout_s,
            )
            serial_handle.reset_input_buffer()
            self.event_queue.put(
                WorkerEvent(
                    "log",
                    f"Opened W2 serial channel {self.serial_config.display_text()} @ "
                    f"{self.config.serial_baud_rate}, timeout "
                    f"{self.config.serial_timeout_s:.3f}s.",
                )
            )

            serial_handle.write(W2CommandBuilder.start_for_mode(self.config.mode))
            serial_handle.flush()
            collection_started = True
            self.event_queue.put(
                WorkerEvent(
                    "log",
                    f"Started W2 collection mode {self.config.mode} on "
                    f"{self.serial_config.channel_id}.",
                )
            )

            last_diagnostic_s = time.monotonic()
            while not self.stop_event.is_set():
                chunk = serial_handle.read(512)
                if not chunk:
                    self._flush()
                    last_diagnostic_s = self._report_no_frames_if_due(last_diagnostic_s)
                    continue

                self.read_count += 1
                if not self.first_rx_preview:
                    self.first_rx_preview = bytes(chunk[:32]).hex(" ")
                    self.event_queue.put(
                        WorkerEvent(
                            "log",
                            f"Received first W2 serial chunk on {self.serial_config.channel_id} "
                            f"({len(chunk)} bytes): {self.first_rx_preview}",
                        )
                    )
                self._consume_data(chunk)
                last_diagnostic_s = self._report_no_frames_if_due(last_diagnostic_s)
        except serial.SerialException as exc:
            self.event_queue.put(
                WorkerEvent(
                    "error",
                    f"W2 serial failure on {self.serial_config.channel_id} "
                    f"({self.serial_config.port}): {exc}",
                )
            )
        except OSError as exc:
            self.event_queue.put(
                WorkerEvent(
                    "error",
                    f"W2 serial device error on {self.serial_config.channel_id} "
                    f"({self.serial_config.port}): {exc}",
                )
            )
        finally:
            self._stop_serial(serial_handle, send_stop_command=collection_started)

    def _report_no_frames_if_due(self, last_report_s: float) -> float:
        now = time.monotonic()
        if self.decoded_packet_count or now - last_report_s < 2.0:
            return last_report_s
        if self.rx_byte_count == 0:
            self.event_queue.put(
                WorkerEvent(
                    "log",
                    f"W2 serial channel {self.serial_config.channel_id} is open but no bytes "
                    "have arrived. Check the Port, cable, and whether another program owns it.",
                )
            )
            return now
        self.event_queue.put(
            WorkerEvent(
                "log",
                f"W2 serial bytes are arriving on {self.serial_config.channel_id} but no frame "
                f"has decoded: rx_bytes={self.rx_byte_count}, buffered={len(self.parser.buffer)}, "
                f"skipped={self.parser.skipped_bytes}, "
                f"unsupported={self.parser.unsupported_frame_count}, "
                f"bad_checksum={self.parser.bad_checksum_count}, "
                f"bad_tail={self.parser.bad_tail_count}, "
                f"bad_payload={self.parser.bad_payload_count}, "
                f"first_bytes={self.first_rx_preview}.",
            )
        )
        return now

    def _stop_serial(self, serial_handle, send_stop_command: bool = True) -> None:
        if serial_handle is None:
            self._flush()
            return
        if send_stop_command:
            try:
                serial_handle.write(W2CommandBuilder.stop_collect())
                serial_handle.flush()
            except Exception as exc:  # pragma: no cover - hardware/disconnect dependent
                self.event_queue.put(
                    WorkerEvent(
                        "error",
                        f"Failed to send W2 stop command on {self.serial_config.channel_id}: {exc}",
                    )
                )
        self._flush()
        try:
            serial_handle.close()
            self.event_queue.put(
                WorkerEvent("log", f"Closed W2 serial channel {self.serial_config.channel_id}.")
            )
        except Exception as exc:  # pragma: no cover - hardware dependent
            self.event_queue.put(
                WorkerEvent(
                    "error",
                    f"Failed to close W2 serial channel {self.serial_config.channel_id}: {exc}",
                )
            )


class W2WorkerGroup:
    """Expose several W2 serial workers as one acquisition-source worker."""

    def __init__(
        self,
        workers: tuple[SerialW2Worker, ...],
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
    ) -> None:
        self.workers = workers
        self.data_queue = data_queue
        self.event_queue = event_queue
        self.stop_event = stop_event

    def start(self) -> None:
        started: list[SerialW2Worker] = []
        try:
            for worker in self.workers:
                worker.start()
                started.append(worker)
        except Exception:
            self.stop_event.set()
            for worker in started:
                worker.join(timeout=1.0)
            raise

    def is_alive(self) -> bool:
        return any(worker.is_alive() for worker in self.workers)

    def join(self, timeout: float | None = None) -> None:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        for worker in self.workers:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            worker.join(timeout=remaining)


@dataclass(frozen=True)
class BLEW2Source:
    """W2 acquisition source with BLE or one-or-more serial connections."""

    config: W2BLEConfig = field(default_factory=W2BLEConfig)

    name: ClassVar[SourceName] = "ble_w2"
    display_name: ClassVar[str] = "W2 (BLE / Serial)"

    def __post_init__(self) -> None:
        object.__setattr__(self, "config", self.config.normalized())

    def display_text(self) -> str:
        return f"{self.display_name}: {self.config.display_text()}"

    def inspect_data(self) -> tuple[str, ...]:
        if self.config.transport == "serial":
            worker_text = "W2WorkerGroup -> one SerialW2Worker per configured Port"
            transport_text = "Transport handle: pyserial serial.Serial (8N1, no parity)"
            frame_text = "Device frame: W2 serial byte stream -> W2RawPacket or W2RmsPacket"
            stream_text = "Worker output: one uniquely identified StreamBlock per serial channel"
        else:
            worker_text = "BLEW2Worker"
            transport_text = "Transport handle: bleak BleakClient"
            frame_text = "Device frame: W2 BLE notify frame -> W2RawPacket or W2RmsPacket"
            stream_text = "Worker output: StreamBlock(stream_id='ble_w2.signal')"
        return (
            f"Source handle: {type(self).__name__}.create_worker(...) -> {worker_text}",
            transport_text,
            "Protocol parser: DeviceInterface.w2_protocol.W2StreamParser",
            frame_text,
            stream_text,
            "Schema: time_s, value (no ADS-shaped zero padding)",
            f"Current config: {self.config.display_text()}",
        )

    def stream_specs(self) -> tuple[StreamSpec, ...]:
        if self.config.transport == "ble":
            return (w2_stream_spec(self.config),)
        return tuple(
            w2_stream_spec(
                self.config,
                stream_id=w2_serial_stream_id(device.channel_id),
                channel_label=device.channel_id,
            )
            for device in self.config.serial_devices
        )

    def capture_metadata(self) -> dict[str, Any]:
        return {"transport": self.config.transport, "config": asdict(self.config)}

    def with_config(self, config: W2BLEConfig) -> "BLEW2Source":
        return BLEW2Source(config=config)

    def create_worker(
        self,
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        resume_state: CaptureResumeState = CaptureResumeState(),
    ) -> SourceWorker:
        if self.config.transport == "ble":
            return BLEW2Worker(
                config=self.config,
                data_queue=data_queue,
                event_queue=event_queue,
                stop_event=stop_event,
                resume_state=resume_state,
            )

        specs = self.stream_specs()
        workers = tuple(
            SerialW2Worker(
                config=self.config,
                serial_config=device,
                spec=spec,
                data_queue=data_queue,
                event_queue=event_queue,
                stop_event=stop_event,
                resume_state=resume_state,
            )
            for device, spec in zip(self.config.serial_devices, specs, strict=True)
        )
        return W2WorkerGroup(
            workers=workers,
            data_queue=data_queue,
            event_queue=event_queue,
            stop_event=stop_event,
        )


__all__ = [
    "BLEW2Source",
    "BLEW2Worker",
    "DEFAULT_W2_SERIAL_BAUD_RATE",
    "SerialW2Worker",
    "W2BLEConfig",
    "W2SerialDeviceConfig",
    "W2StreamAdapter",
    "W2WorkerGroup",
    "W2_TRANSPORT_NAMES",
    "w2_serial_stream_id",
    "w2_stream_spec",
]
