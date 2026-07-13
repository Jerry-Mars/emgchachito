"""Acquisition controller."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal, cast

from fundamental import csv_writer
from fundamental.messages import (
    AcquisitionState,
    SampleBatch,
    SerialConfig,
    WorkerEvent,
)
from fundamental.signal_buffer import SignalBuffer
from fundamental.sources.base import AcquisitionSource, SourceName, SourceWorker
from fundamental.sources.ble_w2 import BLEW2Source, W2BLEConfig, W2_MODE_NAMES
from fundamental.sources.serial_ads1299 import SerialADS1299Source


LogSink = Callable[[str], None]
W2ModeName = Literal["emg_raw", "emg_rms", "eeg_raw"]


class AcquisitionController:
    """Own acquisition state, queues, buffers, and persistence."""

    def __init__(self) -> None:
        self.serial_source = SerialADS1299Source()
        self.w2_source = BLEW2Source()
        self.source_name: SourceName = SerialADS1299Source.name
        self.state = AcquisitionState.STOPPED
        self.buffer = SignalBuffer()
        self.data_queue: queue.Queue[SampleBatch] = queue.Queue()
        self.event_queue: queue.Queue[WorkerEvent] = queue.Queue()
        self.worker: SourceWorker | None = None
        self.stop_event: threading.Event | None = None
        self.last_save_path = str(csv_writer.default_capture_path())

    @property
    def config(self) -> SerialConfig:
        """Serial config compatibility alias for existing UI/tests."""

        return self.serial_source.config

    @config.setter
    def config(self, value: SerialConfig) -> None:
        self.serial_source = self.serial_source.with_config(value)

    @property
    def w2_config(self) -> W2BLEConfig:
        return self.w2_source.config

    @property
    def source(self) -> AcquisitionSource:
        if self.source_name == BLEW2Source.name:
            return self.w2_source
        return self.serial_source

    def available_sources(self) -> tuple[tuple[SourceName, str], ...]:
        return (
            (SerialADS1299Source.name, SerialADS1299Source.display_name),
            (BLEW2Source.name, BLEW2Source.display_name),
        )

    def source_display_text(self) -> str:
        return self.source.display_text()

    def select_source(self, source_name: str) -> str | None:
        normalized = source_name.strip()
        available = {name for name, _label in self.available_sources()}
        if normalized not in available:
            return f"Unknown acquisition source: {source_name}"
        if normalized == self.source_name:
            return None
        if self.state != AcquisitionState.STOPPED:
            return "Stop acquisition before changing source."

        self.source_name = cast(SourceName, normalized)
        return None

    def update_config(
        self,
        port: str | None = None,
        baud_rate: int | None = None,
        timeout_s: float | None = None,
    ) -> str | None:
        return self.update_serial_config(port=port, baud_rate=baud_rate, timeout_s=timeout_s)

    def update_serial_config(
        self,
        port: str | None = None,
        baud_rate: int | None = None,
        timeout_s: float | None = None,
    ) -> str | None:
        if self.state != AcquisitionState.STOPPED:
            return "Stop acquisition before changing serial configuration."

        next_config = SerialConfig(
            port=self.config.port if port is None else port,
            baud_rate=self.config.baud_rate if baud_rate is None else baud_rate,
            timeout_s=self.config.timeout_s if timeout_s is None else timeout_s,
        ).normalized()
        self.serial_source = self.serial_source.with_config(next_config)
        return None

    def update_w2_config(
        self,
        address: str | None = None,
        device_name_filter: str | None = None,
        notify_uuid: str | None = None,
        write_uuid: str | None = None,
        mode: str | None = None,
        sample_rate_hz: float | None = None,
        scan_timeout_s: float | None = None,
    ) -> str | None:
        if self.state != AcquisitionState.STOPPED:
            return "Stop acquisition before changing W2 BLE configuration."

        mode_value = self.w2_config.mode if mode is None else mode.strip()
        if mode_value not in W2_MODE_NAMES:
            return f"Unsupported W2 BLE mode: {mode_value}"
        next_config = W2BLEConfig(
            address=self.w2_config.address if address is None else address,
            device_name_filter=(
                self.w2_config.device_name_filter if device_name_filter is None else device_name_filter
            ),
            notify_uuid=self.w2_config.notify_uuid if notify_uuid is None else notify_uuid,
            write_uuid=self.w2_config.write_uuid if write_uuid is None else write_uuid,
            mode=cast(W2ModeName, mode_value),
            sample_rate_hz=self.w2_config.sample_rate_hz if sample_rate_hz is None else sample_rate_hz,
            scan_timeout_s=self.w2_config.scan_timeout_s if scan_timeout_s is None else scan_timeout_s,
        ).normalized()
        if not next_config.notify_uuid:
            return "W2 BLE notify UUID cannot be empty."
        if not next_config.write_uuid:
            return "W2 BLE write UUID cannot be empty."

        self.w2_source = self.w2_source.with_config(next_config)
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
        self.worker = self.source.create_worker(
            data_queue=self.data_queue,
            event_queue=self.event_queue,
            stop_event=self.stop_event,
            timestamp_offset_s=timestamp_offset,
            expected_counter=expected_counter,
        )
        self.worker.start()
        self.state = AcquisitionState.RUNNING
        return f"Acquisition started with {self.source.display_text()}."

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

    def save(
        self,
        path: str | Path | None = None,
        stimulus_code_for_time: csv_writer.StimulusCodeResolver | None = None,
        stimulus_log_rows: Sequence[dict[str, Any]] | None = None,
    ) -> str:
        if self.state == AcquisitionState.RUNNING:
            return "Pause or stop acquisition before saving."

        frames = self.buffer.snapshot_frames()
        if not frames:
            return "No samples to save."

        save_path = str(path).strip() if path is not None else self.last_save_path
        if not save_path:
            save_path = self.last_save_path
        output_path, row_count = csv_writer.save_frames(
            save_path,
            frames,
            stimulus_code_for_time=stimulus_code_for_time,
        )
        self.last_save_path = str(output_path)
        if stimulus_log_rows is not None:
            log_path, log_rows = csv_writer.save_stimulus_log(
                csv_writer.stimulus_log_path(output_path),
                stimulus_log_rows,
            )
            return f"Saved {row_count} samples to {output_path} and {log_rows} stimulus events to {log_path}."
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
