"""Acquisition controller."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from fundamental import csv_writer
from fundamental.capture_store import CaptureStore
from fundamental.messages import (
    AcquisitionState,
    SerialConfig,
    WorkerEvent,
)
from fundamental.sources.base import AcquisitionSource, SourceName, SourceWorker
from fundamental.sources.ble_w2 import BLEW2Source, W2BLEConfig, W2_MODE_NAMES
from fundamental.sources.myo import MyoBLEConfig, MyoSource
from fundamental.sources.serial_ads1299 import SerialADS1299Source
from fundamental.streams import StreamBlock


LogSink = Callable[[str], None]
W2ModeName = Literal["emg_raw", "emg_rms", "eeg_raw"]


class AcquisitionController:
    """Own acquisition state, queues, buffers, and persistence."""

    def __init__(self) -> None:
        self._sources: dict[SourceName, AcquisitionSource] = {
            SerialADS1299Source.name: SerialADS1299Source(),
            BLEW2Source.name: BLEW2Source(),
            MyoSource.name: MyoSource(),
        }
        self.source_name: SourceName = SerialADS1299Source.name
        self.state = AcquisitionState.STOPPED
        self.buffer = CaptureStore(stream_specs=self.source.stream_specs())
        self.data_queue: queue.Queue[StreamBlock] = queue.Queue()
        self.event_queue: queue.Queue[WorkerEvent] = queue.Queue()
        self.worker: SourceWorker | None = None
        self.stop_event: threading.Event | None = None
        self.last_save_path = str(csv_writer.default_capture_path())
        self.capture_metadata: dict[str, Any] = {}

    @property
    def serial_source(self) -> SerialADS1299Source:
        return cast(SerialADS1299Source, self._sources[SerialADS1299Source.name])

    @serial_source.setter
    def serial_source(self, value: AcquisitionSource) -> None:
        self._sources[SerialADS1299Source.name] = value

    @property
    def w2_source(self) -> BLEW2Source:
        return cast(BLEW2Source, self._sources[BLEW2Source.name])

    @w2_source.setter
    def w2_source(self, value: AcquisitionSource) -> None:
        self._sources[BLEW2Source.name] = value

    @property
    def myo_source(self) -> MyoSource:
        return cast(MyoSource, self._sources[MyoSource.name])

    @myo_source.setter
    def myo_source(self, value: AcquisitionSource) -> None:
        self._sources[MyoSource.name] = value

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
    def myo_config(self) -> MyoBLEConfig:
        return self.myo_source.config

    @property
    def source(self) -> AcquisitionSource:
        return self._sources[self.source_name]

    def available_sources(self) -> tuple[tuple[SourceName, str], ...]:
        return tuple((name, source.display_name) for name, source in self._sources.items())

    def source_display_text(self) -> str:
        return self.source.display_text()

    def configured_source(self, source_name: SourceName) -> AcquisitionSource:
        return self._sources[source_name]

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
        self.buffer.configure_streams(self.source.stream_specs(), clear=True)
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
        if self.source_name == SerialADS1299Source.name:
            self.buffer.configure_streams(self.source.stream_specs(), clear=True)
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
        if not next_config.address and not next_config.device_name_filter:
            return "W2 BLE address and name filter cannot both be empty."

        self.w2_source = self.w2_source.with_config(next_config)
        if self.source_name == BLEW2Source.name:
            self.buffer.configure_streams(self.source.stream_specs(), clear=True)
        return None

    def update_myo_config(
        self,
        address: str | None = None,
        device_name_filter: str | None = None,
        scan_timeout_s: float | None = None,
        connect_timeout_s: float | None = None,
        enable_emg: bool | None = None,
        enable_imu: bool | None = None,
    ) -> str | None:
        if self.state != AcquisitionState.STOPPED:
            return "Stop acquisition before changing Myo BLE configuration."

        next_config = MyoBLEConfig(
            address=self.myo_config.address if address is None else address,
            device_name_filter=(
                self.myo_config.device_name_filter
                if device_name_filter is None
                else device_name_filter
            ),
            scan_timeout_s=(
                self.myo_config.scan_timeout_s if scan_timeout_s is None else scan_timeout_s
            ),
            connect_timeout_s=(
                self.myo_config.connect_timeout_s
                if connect_timeout_s is None
                else connect_timeout_s
            ),
            enable_emg=self.myo_config.enable_emg if enable_emg is None else enable_emg,
            enable_imu=self.myo_config.enable_imu if enable_imu is None else enable_imu,
        ).normalized()
        if not next_config.address and not next_config.device_name_filter:
            return "Myo BLE address and name filter cannot both be empty."
        if not next_config.enable_emg and not next_config.enable_imu:
            return "Enable at least one Myo data stream."

        self.myo_source = self.myo_source.with_config(next_config)
        if self.source_name == MyoSource.name:
            self.buffer.configure_streams(self.source.stream_specs(), clear=True)
        return None

    def start(self) -> str:
        if self.state == AcquisitionState.RUNNING:
            return "Acquisition is already running."

        if self.state == AcquisitionState.STOPPED:
            self.buffer.reset(self.source.stream_specs())
            self.last_save_path = str(csv_writer.default_capture_path())
            self.capture_metadata = {
                "capture_started_at": datetime.now().astimezone().isoformat(),
                "source": self.source.name,
                **self.source.capture_metadata(),
            }

        resume_state = self.buffer.resume_state()

        self._clear_queues()
        self.stop_event = threading.Event()
        self.worker = self.source.create_worker(
            data_queue=self.data_queue,
            event_queue=self.event_queue,
            stop_event=self.stop_event,
            resume_state=resume_state,
        )
        self.worker.start()
        self.state = AcquisitionState.RUNNING
        return f"Acquisition started with {self.source.display_text()}."

    def pause(self) -> str:
        if self.state != AcquisitionState.RUNNING:
            return "Acquisition is not running."
        self._stop_worker()
        self.state = AcquisitionState.PAUSED
        return (
            f"Acquisition paused with {self.buffer.row_count} rows buffered across "
            f"{self.buffer.stream_count} stream(s)."
        )

    def stop(self) -> str:
        if self.state == AcquisitionState.RUNNING:
            self._stop_worker()
        elif self.worker is not None:
            self._stop_worker()
        self.state = AcquisitionState.STOPPED
        return (
            f"Acquisition stopped with {self.buffer.row_count} rows buffered across "
            f"{self.buffer.stream_count} stream(s)."
        )

    def save(
        self,
        path: str | Path | None = None,
        stimulus_code_for_time: csv_writer.StimulusCodeResolver | None = None,
        stimulus_log_rows: Sequence[dict[str, Any]] | None = None,
    ) -> str:
        if self.state == AcquisitionState.RUNNING:
            return "Pause or stop acquisition before saving."

        snapshots = self.buffer.snapshots()
        if not snapshots:
            return "No samples to save."

        save_path = str(path).strip() if path is not None else self.last_save_path
        if not save_path:
            save_path = self.last_save_path
        result = csv_writer.save_capture(
            save_path,
            snapshots,
            stimulus_code_for_time=stimulus_code_for_time,
            stimulus_log_rows=stimulus_log_rows,
            metadata=self.capture_metadata,
        )
        self.last_save_path = str(save_path)
        stream_text = ", ".join(
            f"{stream.stream_id}: {stream.row_count} rows -> {stream.path}"
            for stream in result.streams
        )
        message = f"Saved {result.total_rows} rows ({stream_text}); metadata -> {result.metadata_path}."
        if result.stimulus_path is not None:
            message = (
                f"{message} Stimulus events: {result.stimulus_rows} -> "
                f"{result.stimulus_path}."
            )
        return message

    def drain_queues(self, log_sink: LogSink | None = None, max_batches: int = 64) -> int:
        appended = 0
        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break

            if log_sink is not None:
                if event.message:
                    log_sink(event.message)
            if event.kind == "metadata" and event.data is not None:
                self.capture_metadata.update(dict(event.data))
            if event.kind == "error":
                self.state = AcquisitionState.STOPPED
                self._stop_worker(join_timeout_s=0.0)

        for _ in range(max(1, int(max_batches))):
            try:
                batch = self.data_queue.get_nowait()
            except queue.Empty:
                break
            appended += self.buffer.append_block(batch)

        return appended

    def shutdown(self) -> None:
        self._stop_worker()
        self.state = AcquisitionState.STOPPED

    def _stop_worker(self, join_timeout_s: float = 5.0) -> None:
        if self.stop_event is not None:
            self.stop_event.set()

        if self.worker is not None and self.worker.is_alive():
            self.worker.join(timeout=join_timeout_s)

        self.worker = None
        self.stop_event = None
        self._drain_data_queue()
        self._drain_event_queue()

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
            self.buffer.append_block(batch)

    def _drain_event_queue(self) -> None:
        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                return
            if event.kind == "metadata" and event.data is not None:
                self.capture_metadata.update(dict(event.data))
