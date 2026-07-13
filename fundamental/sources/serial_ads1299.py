"""ADS1299 serial source worker."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import ClassVar

from DeviceInterface.ads1299_protocol import ADS1299StreamParser, SAMPLE_RATE_HZ
from fundamental.messages import (
    DEFAULT_MAX_FRAMES_PER_BATCH,
    SampleBatch,
    SampleFrame,
    SerialConfig,
    WorkerEvent,
)
from fundamental.sources.base import SourceName, SourceWorker

try:
    import serial
except ImportError:  # pragma: no cover - depends on local runtime
    serial = None


class SerialWorker(threading.Thread):
    """Read serial frames in the background and publish sample batches."""

    def __init__(
        self,
        config: SerialConfig,
        data_queue: queue.Queue[SampleBatch],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        timestamp_offset_s: float = 0.0,
        expected_counter: int | None = None,
        max_frames_per_batch: int = DEFAULT_MAX_FRAMES_PER_BATCH,
    ) -> None:
        super().__init__(name="SerialWorker", daemon=True)
        self.config = config.normalized()
        self.data_queue = data_queue
        self.event_queue = event_queue
        self.stop_event = stop_event
        self.timestamp_offset_s = timestamp_offset_s
        self.expected_counter = expected_counter
        self.max_frames_per_batch = max(1, int(max_frames_per_batch))
        self._counter_origin: int | None = None
        self._timestamp_origin_s = timestamp_offset_s
        self._last_timestamp_s = timestamp_offset_s

    def run(self) -> None:
        if serial is None:
            self.event_queue.put(
                WorkerEvent("error", "pyserial is not installed; serial acquisition is unavailable.")
            )
            return

        if not self.config.port:
            self.event_queue.put(WorkerEvent("error", "Serial port is empty."))
            return

        serial_handle = None
        try:
            serial_handle = serial.Serial(
                self.config.port,
                self.config.baud_rate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.config.timeout_s,
            )
            serial_handle.reset_input_buffer()
            self.event_queue.put(WorkerEvent("log", f"Opened serial port {self.config.display_text()}"))

            parser = ADS1299StreamParser()
            parser.expected_counter = self.expected_counter
            frames: list[SampleFrame] = []
            logged_skipped_bytes = 0
            while not self.stop_event.is_set():
                try:
                    chunk = serial_handle.read(512)
                except serial.SerialException as exc:
                    self.event_queue.put(WorkerEvent("error", f"Serial read failed: {exc}"))
                    return
                except OSError as exc:
                    self.event_queue.put(WorkerEvent("error", f"Serial device error: {exc}"))
                    return

                if not chunk:
                    self._flush(frames)
                    continue

                parsed_frames = parser.feed(chunk)
                if parser.skipped_bytes - logged_skipped_bytes >= 256:
                    logged_skipped_bytes = parser.skipped_bytes
                    self.event_queue.put(
                        WorkerEvent(
                            "log",
                            f"Skipped {parser.skipped_bytes} bytes while resynchronizing serial frames.",
                        )
                    )

                for parsed_frame in parsed_frames:
                    if parsed_frame.dropped_frames_before:
                        self.event_queue.put(
                            WorkerEvent(
                                "log",
                                "Frame counter discontinuity before "
                                f"{parsed_frame.counter}: dropped={parsed_frame.dropped_frames_before}.",
                            )
                        )
                    timestamp = self._timestamp_for_counter(parsed_frame.counter)
                    frames.append(
                        SampleFrame(
                            time_s=timestamp,
                            counter=parsed_frame.counter,
                            dropped_frames_before=parsed_frame.dropped_frames_before,
                            values=parsed_frame.channels_code,
                            emg_channel_count=parsed_frame.emg_channel_count,
                        )
                    )
                    if len(frames) >= self.max_frames_per_batch:
                        self._flush(frames)
        except serial.SerialException as exc:
            self.event_queue.put(WorkerEvent("error", f"Failed to open serial port: {exc}"))
        except OSError as exc:
            self.event_queue.put(WorkerEvent("error", f"Failed to access serial device: {exc}"))
        finally:
            self._close_handle(serial_handle)

    def _flush(self, frames: list[SampleFrame]) -> None:
        if not frames:
            return
        self.data_queue.put(SampleBatch(tuple(frames)))
        frames.clear()

    def _timestamp_for_counter(self, counter: int) -> float:
        if self._counter_origin is None:
            if self.expected_counter is None:
                self._counter_origin = counter
            else:
                self._counter_origin = self.expected_counter - 1
            self._timestamp_origin_s = self.timestamp_offset_s

        elapsed_samples = counter - self._counter_origin
        if elapsed_samples < 0:
            self._counter_origin = counter
            self._timestamp_origin_s = self._last_timestamp_s + 1.0 / SAMPLE_RATE_HZ
            elapsed_samples = 0

        timestamp = self._timestamp_origin_s + elapsed_samples / SAMPLE_RATE_HZ
        self._last_timestamp_s = max(self._last_timestamp_s, timestamp)
        return timestamp

    def _close_handle(self, serial_handle) -> None:
        if serial_handle is None:
            return
        try:
            serial_handle.close()
            self.event_queue.put(WorkerEvent("log", "Serial port closed."))
        except Exception as exc:  # pragma: no cover - hardware dependent
            self.event_queue.put(WorkerEvent("error", f"Serial close failed: {exc}"))


@dataclass(frozen=True)
class SerialADS1299Source:
    """Serial ADS1299 acquisition source configuration."""

    config: SerialConfig = field(default_factory=SerialConfig)

    name: ClassVar[SourceName] = "serial_ads1299"
    display_name: ClassVar[str] = "Serial ADS1299"

    def __post_init__(self) -> None:
        object.__setattr__(self, "config", self.config.normalized())

    def display_text(self) -> str:
        return f"{self.display_name}: {self.config.display_text()}"

    def inspect_data(self) -> tuple[str, ...]:
        return (
            f"Source handle: {type(self).__name__}.create_worker(...) -> SerialWorker",
            "Transport handle: pyserial serial.Serial",
            "Protocol parser: DeviceInterface.ads1299_protocol.ADS1299StreamParser",
            "Device frame: 0xAA, emg_channel_count, 8 x int24 channel codes, uint64 counter, 0xBB",
            "Worker output: SampleBatch(frames=tuple[SampleFrame, ...])",
            "SampleFrame: time_s, counter, dropped_frames_before, emg_channel_count, values[8]",
            "Timing: host timestamps derived from device frame_counter and ADS1299 SAMPLE_RATE_HZ",
            f"Current config: {self.config.display_text()}",
        )

    def with_config(self, config: SerialConfig) -> "SerialADS1299Source":
        return SerialADS1299Source(config=config)

    def create_worker(
        self,
        data_queue: queue.Queue[SampleBatch],
        event_queue: queue.Queue[WorkerEvent],
        stop_event: threading.Event,
        timestamp_offset_s: float = 0.0,
        expected_counter: int | None = None,
    ) -> SourceWorker:
        return SerialWorker(
            config=self.config,
            data_queue=data_queue,
            event_queue=event_queue,
            stop_event=stop_event,
            timestamp_offset_s=timestamp_offset_s,
            expected_counter=expected_counter,
        )


__all__ = ["SerialADS1299Source", "SerialWorker"]
