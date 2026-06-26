"""Serial acquisition worker and controller."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from pathlib import Path

from DeviceInterface.ads1299_protocol import ADS1299StreamParser, SAMPLE_RATE_HZ
from fundamental import csv_writer
from fundamental.messages import (
    DEFAULT_MAX_FRAMES_PER_BATCH,
    AcquisitionState,
    SampleBatch,
    SampleFrame,
    SerialConfig,
    WorkerEvent,
)
from fundamental.signal_buffer import SignalBuffer

try:
    import serial
except ImportError:  # pragma: no cover - depends on local runtime
    serial = None


LogSink = Callable[[str], None]


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


class AcquisitionController:
    """Own acquisition state, queues, buffers, and persistence."""

    def __init__(self) -> None:
        self.config = SerialConfig()
        self.state = AcquisitionState.STOPPED
        self.buffer = SignalBuffer()
        self.data_queue: queue.Queue[SampleBatch] = queue.Queue()
        self.event_queue: queue.Queue[WorkerEvent] = queue.Queue()
        self.worker: SerialWorker | None = None
        self.stop_event: threading.Event | None = None
        self.last_save_path = str(csv_writer.default_capture_path())

    def update_config(
        self,
        port: str | None = None,
        baud_rate: int | None = None,
        timeout_s: float | None = None,
    ) -> str | None:
        if self.state == AcquisitionState.RUNNING:
            return "Stop or pause acquisition before changing serial configuration."

        next_config = SerialConfig(
            port=self.config.port if port is None else port,
            baud_rate=self.config.baud_rate if baud_rate is None else baud_rate,
            timeout_s=self.config.timeout_s if timeout_s is None else timeout_s,
        ).normalized()
        self.config = next_config
        return None

    def start(self) -> str:
        if self.state == AcquisitionState.RUNNING:
            return "Acquisition is already running."

        timestamp_offset = self.buffer.latest_time_s
        expected_counter = None
        if self.state == AcquisitionState.STOPPED:
            self.buffer.reset()
            timestamp_offset = 0.0
            self.last_save_path = str(csv_writer.default_capture_path())
        elif self.buffer.frames:
            expected_counter = self.buffer.frames[-1].counter + 1

        self._clear_queues()
        self.stop_event = threading.Event()
        self.worker = SerialWorker(
            config=self.config,
            data_queue=self.data_queue,
            event_queue=self.event_queue,
            stop_event=self.stop_event,
            timestamp_offset_s=timestamp_offset,
            expected_counter=expected_counter,
        )
        self.worker.start()
        self.state = AcquisitionState.RUNNING
        return f"Acquisition started with {self.config.display_text()}."

    def pause(self) -> str:
        if self.state != AcquisitionState.RUNNING:
            return "Acquisition is not running."
        self._stop_worker()
        self.state = AcquisitionState.PAUSED
        return f"Acquisition paused with {self.buffer.frame_count} samples buffered."

    def stop(self) -> str:
        if self.state == AcquisitionState.RUNNING:
            self._stop_worker()
        elif self.worker is not None:
            self._stop_worker()
        self.state = AcquisitionState.STOPPED
        return f"Acquisition stopped with {self.buffer.frame_count} samples buffered."

    def save(self, path: str | Path | None = None) -> str:
        if self.state == AcquisitionState.RUNNING:
            return "Pause or stop acquisition before saving."

        frames = self.buffer.snapshot_frames()
        if not frames:
            return "No samples to save."

        save_path = str(path).strip() if path is not None else self.last_save_path
        if not save_path:
            save_path = self.last_save_path
        output_path, row_count = csv_writer.save_frames(save_path, frames)
        self.last_save_path = str(output_path)
        return f"Saved {row_count} samples to {output_path}."

    def drain_queues(self, log_sink: LogSink | None = None, max_batches: int = 64) -> int:
        appended = 0
        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break

            if log_sink is not None:
                log_sink(event.message)
            if event.kind == "error":
                self.state = AcquisitionState.STOPPED
                self._stop_worker(join_timeout_s=0.0)

        for _ in range(max(1, int(max_batches))):
            try:
                batch = self.data_queue.get_nowait()
            except queue.Empty:
                break
            appended += self.buffer.append_batch(batch)

        return appended

    def shutdown(self) -> None:
        self._stop_worker()
        self.state = AcquisitionState.STOPPED

    def _stop_worker(self, join_timeout_s: float = 1.0) -> None:
        if self.stop_event is not None:
            self.stop_event.set()

        if self.worker is not None and self.worker.is_alive():
            self.worker.join(timeout=join_timeout_s)

        self.worker = None
        self.stop_event = None
        self._drain_data_queue()

    def _clear_queues(self) -> None:
        self._drain_queue(self.data_queue)
        self._drain_queue(self.event_queue)

    @staticmethod
    def _drain_queue(target_queue: queue.Queue) -> None:
        while True:
            try:
                target_queue.get_nowait()
            except queue.Empty:
                return

    def _drain_data_queue(self) -> None:
        while True:
            try:
                batch = self.data_queue.get_nowait()
            except queue.Empty:
                return
            self.buffer.append_batch(batch)
