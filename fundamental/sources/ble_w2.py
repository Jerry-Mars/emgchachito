"""BLE source for the RunE W2 demo device.

The module keeps BLE transport separate from the W2 byte protocol. It currently
emits the existing SampleBatch contract as a compatibility bridge; the W2 packet
and parser types are intentionally independent so they can later feed a
SignalBlock adapter directly.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from DeviceInterface.w2_protocol import W2CommandBuilder, W2Packet, W2RawPacket, W2RmsPacket, W2StreamParser
from fundamental.messages import (
    CHANNEL_COUNT,
    DEFAULT_MAX_FRAMES_PER_BATCH,
    SampleBatch,
    SampleFrame,
    WorkerEvent,
)
from fundamental.sources.base import SourceName, SourceWorker

try:
    from bleak import BleakClient, BleakScanner
except ImportError:  # pragma: no cover - depends on local runtime
    BleakClient = None
    BleakScanner = None


DEFAULT_W2_DEVICE_NAME = "RunE W2"
DEFAULT_W2_ADDRESS = "31:23:04:00:00:11"
DEFAULT_W2_NOTIFY_UUID = "0000FFF4-0000-1000-8000-00805F9B34FB"
DEFAULT_W2_WRITE_UUID = "0000FFF3-0000-1000-8000-00805F9B34FB"
DEFAULT_W2_SAMPLE_RATE_HZ = 1000.0
W2_MODE_NAMES = ("emg_raw", "emg_rms", "eeg_raw")


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


class W2SampleAdapter:
    """Temporary W2 packet to SampleBatch bridge.

    W2 values are rounded and padded into the current fixed-width SampleFrame
    format. This keeps the source usable before SignalBlock replaces the
    ADS-shaped sample contract.
    """

    def __init__(
        self,
        sample_rate_hz: float = DEFAULT_W2_SAMPLE_RATE_HZ,
        timestamp_offset_s: float = 0.0,
        expected_counter: int | None = None,
    ) -> None:
        self.sample_rate_hz = max(0.001, float(sample_rate_hz))
        self._next_counter = 0 if expected_counter is None else int(expected_counter)
        self._counter_origin = 0 if expected_counter is None else int(expected_counter) - 1
        self._timestamp_origin_s = float(timestamp_offset_s)

    def packet_to_frames(self, packet: W2Packet) -> list[SampleFrame]:
        if isinstance(packet, W2RmsPacket):
            return [self._sample_frame(float(packet.rms))]
        return [self._sample_frame(value) for value in packet.values]

    def _sample_frame(self, value: float) -> SampleFrame:
        counter = self._next_counter
        self._next_counter += 1
        timestamp = self._timestamp_origin_s + (counter - self._counter_origin) / self.sample_rate_hz
        first_channel = int(round(value))
        return SampleFrame(
            time_s=timestamp,
            counter=counter,
            dropped_frames_before=0,
            values=(first_channel,) + tuple(0 for _ in range(CHANNEL_COUNT - 1)),
            emg_channel_count=1,
        )


class BLEW2Worker(threading.Thread):
    """Connect to a W2 BLE device and publish SampleBatch objects."""

    def __init__(
        self,
        config: W2BLEConfig,
        data_queue: queue.Queue[SampleBatch],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        timestamp_offset_s: float = 0.0,
        expected_counter: int | None = None,
        max_frames_per_batch: int = DEFAULT_MAX_FRAMES_PER_BATCH,
    ) -> None:
        super().__init__(name="BLEW2Worker", daemon=True)
        self.config = config
        self.data_queue = data_queue
        self.event_queue = event_queue
        self.stop_event = stop_event
        self.max_frames_per_batch = max(1, int(max_frames_per_batch))
        self.parser = W2StreamParser()
        self.adapter = W2SampleAdapter(
            sample_rate_hz=config.sample_rate_hz,
            timestamp_offset_s=timestamp_offset_s,
            expected_counter=expected_counter,
        )
        self._frames: list[SampleFrame] = []
        self._last_logged_parser_counters = (0, 0, 0)

    def run(self) -> None:
        if BleakClient is None or BleakScanner is None:
            self.event_queue.put(WorkerEvent("error", "bleak is not installed; W2 BLE acquisition is unavailable."))
            return

        try:
            asyncio.run(self._run_async())
        except Exception as exc:  # pragma: no cover - hardware/event-loop dependent
            self.event_queue.put(WorkerEvent("error", f"W2 BLE worker failed: {exc}"))

    async def _run_async(self) -> None:
        address = await self._resolve_address()
        if self.stop_event.is_set():
            self.event_queue.put(WorkerEvent("log", "W2 BLE start cancelled before connection."))
            return
        if not address:
            self.event_queue.put(WorkerEvent("error", "No W2 BLE device matched the configured address/name."))
            return

        async with BleakClient(address) as client:
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

                while not self.stop_event.is_set():
                    self._flush()
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
            return configured

        devices = await BleakScanner.discover(timeout=self.config.scan_timeout_s)
        matches = [
            device
            for device in devices
            if getattr(device, "name", None) and self.config.device_name_filter in str(device.name)
        ]
        if not matches:
            return None
        matches.sort(key=lambda device: getattr(device, "rssi", -999) or -999, reverse=True)
        return str(matches[0].address)

    def _handle_notification(self, _sender, received_data: bytearray) -> None:
        for packet in self.parser.feed(bytes(received_data)):
            self._frames.extend(self.adapter.packet_to_frames(packet))
            if len(self._frames) >= self.max_frames_per_batch:
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
        if not self._frames:
            return
        self.data_queue.put(SampleBatch(tuple(self._frames)))
        self._frames.clear()


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
            "Worker output: SampleBatch(frames=tuple[SampleFrame, ...])",
            "SampleFrame bridge: W2 value goes to values[0], remaining channels padded with 0",
            "Active channel count: emg_channel_count=1 until SignalBlock replaces the bridge",
            f"Current config: {self.config.display_text()}",
        )

    def with_config(self, config: W2BLEConfig) -> "BLEW2Source":
        return BLEW2Source(config=config)

    def create_worker(
        self,
        data_queue: queue.Queue[SampleBatch],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        timestamp_offset_s: float = 0.0,
        expected_counter: int | None = None,
    ) -> SourceWorker:
        return BLEW2Worker(
            config=self.config,
            data_queue=data_queue,
            event_queue=event_queue,
            stop_event=stop_event,
            timestamp_offset_s=timestamp_offset_s,
            expected_counter=expected_counter,
        )


__all__ = [
    "BLEW2Source",
    "BLEW2Worker",
    "W2BLEConfig",
    "W2SampleAdapter",
]
