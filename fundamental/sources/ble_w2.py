"""BLE source for the RunE W2 demo device."""

from __future__ import annotations

import asyncio
import queue
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar, Literal

from DeviceInterface.w2_protocol import W2CommandBuilder, W2Packet, W2RmsPacket, W2StreamParser
from fundamental.messages import (
    DEFAULT_MAX_FRAMES_PER_BATCH,
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


DEFAULT_W2_DEVICE_NAME = "RunE W2"
# The address in device_host_demo/main.py belongs to the demo unit. Scanning by
# the advertised RunE W2 name is a safer default for a different physical unit.
DEFAULT_W2_ADDRESS = ""
DEFAULT_W2_NOTIFY_UUID = "0000FFF4-0000-1000-8000-00805F9B34FB"
DEFAULT_W2_WRITE_UUID = "0000FFF3-0000-1000-8000-00805F9B34FB"
DEFAULT_W2_SAMPLE_RATE_HZ = 1000.0
W2_MODE_NAMES = ("emg_raw", "emg_rms", "eeg_raw")
W2_STREAM_ID = "ble_w2.signal"


@dataclass(frozen=True)
class W2BLEConfig:
    """Connection and acquisition settings for a W2 BLE source."""

    address: str = DEFAULT_W2_ADDRESS
    device_name_filter: str = DEFAULT_W2_DEVICE_NAME
    notify_uuid: str = DEFAULT_W2_NOTIFY_UUID
    write_uuid: str = DEFAULT_W2_WRITE_UUID
    mode: Literal["emg_raw", "emg_rms", "eeg_raw"] = "emg_raw"
    sample_rate_hz: float = DEFAULT_W2_SAMPLE_RATE_HZ
    scan_timeout_s: float = 5.0

    def normalized(self) -> "W2BLEConfig":
        mode = self.mode if self.mode in W2_MODE_NAMES else "emg_raw"
        return W2BLEConfig(
            address=self.address.strip(),
            device_name_filter=self.device_name_filter.strip(),
            notify_uuid=self.notify_uuid.strip(),
            write_uuid=self.write_uuid.strip(),
            mode=mode,
            sample_rate_hz=max(0.001, float(self.sample_rate_hz)),
            scan_timeout_s=max(0.1, float(self.scan_timeout_s)),
        )

    def display_text(self) -> str:
        target = self.address.strip() or f"name contains {self.device_name_filter!r}"
        return f"W2 BLE {target}, notify {self.notify_uuid}, write {self.write_uuid}, mode {self.mode}"


def w2_stream_spec(config: W2BLEConfig) -> StreamSpec:
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
    return StreamSpec(
        stream_id=W2_STREAM_ID,
        display_name=f"W2 {label}",
        nominal_rate_hz=config.sample_rate_hz,
        fields=(
            FieldSpec(
                "value",
                label,
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


class BLEW2Worker(threading.Thread):
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
        super().__init__(name="BLEW2Worker", daemon=True)
        self.config = config
        self.data_queue = data_queue
        self.event_queue = event_queue
        self.stop_event = stop_event
        self.max_frames_per_batch = max(1, int(max_frames_per_batch))
        self.parser = W2StreamParser()
        self.spec = w2_stream_spec(config)
        self.adapter = W2StreamAdapter(
            spec=self.spec,
            sample_rate_hz=config.sample_rate_hz,
            resume_state=resume_state,
        )
        self._times: list[float] = []
        self._rows: list[tuple[int | float, ...]] = []
        self._last_logged_parser_counters = (0, 0, 0)
        self.notification_count = 0
        self.rx_byte_count = 0
        self.decoded_packet_count = 0
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
        self.rx_byte_count += len(received_data)
        packets = self.parser.feed(bytes(received_data))
        self.decoded_packet_count += len(packets)
        if self.notification_count == 1:
            preview = bytes(received_data[:32]).hex(" ")
            self.event_queue.put(
                WorkerEvent(
                    "log",
                    f"Received first W2 notification ({len(received_data)} bytes): {preview}",
                )
            )
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
                    "W2 parser counters: "
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


@dataclass(frozen=True)
class BLEW2Source:
    """BLE W2 acquisition source configuration."""

    config: W2BLEConfig = field(default_factory=W2BLEConfig)

    name: ClassVar[SourceName] = "ble_w2"
    display_name: ClassVar[str] = "BLE W2"

    def __post_init__(self) -> None:
        object.__setattr__(self, "config", self.config.normalized())

    def display_text(self) -> str:
        return f"{self.display_name}: {self.config.display_text()}"

    def inspect_data(self) -> tuple[str, ...]:
        return (
            f"Source handle: {type(self).__name__}.create_worker(...) -> BLEW2Worker",
            "Transport handle: bleak BleakClient",
            "Protocol parser: DeviceInterface.w2_protocol.W2StreamParser",
            "Device frame: W2 BLE notify frame -> W2RawPacket or W2RmsPacket",
            "Worker output: StreamBlock(stream_id='ble_w2.signal')",
            "Schema: time_s, value (no ADS-shaped zero padding)",
            f"Current config: {self.config.display_text()}",
        )

    def stream_specs(self) -> tuple[StreamSpec, ...]:
        return (w2_stream_spec(self.config),)

    def capture_metadata(self) -> dict[str, Any]:
        return {"transport": "ble", "config": asdict(self.config)}

    def with_config(self, config: W2BLEConfig) -> "BLEW2Source":
        return BLEW2Source(config=config)

    def create_worker(
        self,
        data_queue: queue.Queue[StreamBlock],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        resume_state: CaptureResumeState = CaptureResumeState(),
    ) -> SourceWorker:
        return BLEW2Worker(
            config=self.config,
            data_queue=data_queue,
            event_queue=event_queue,
            stop_event=stop_event,
            resume_state=resume_state,
        )


__all__ = [
    "BLEW2Source",
    "BLEW2Worker",
    "W2BLEConfig",
    "W2StreamAdapter",
    "w2_stream_spec",
]
