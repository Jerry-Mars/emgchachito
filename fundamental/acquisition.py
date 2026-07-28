"""Acquisition controller for one legacy source or a coordinated W2/BWT source set."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from fundamental import csv_writer
from fundamental.capture_store import CaptureStore
from fundamental.messages import AcquisitionState, SerialConfig, WorkerEvent
from fundamental.sources.base import (
    AcquisitionSource,
    CaptureClock,
    SourceName,
    SourceWorker,
    SourceWorkerGroup,
    WorkerControl,
)
from fundamental.sources.ble_w2 import (
    BLEW2Source,
    MAX_W2_DEVICES,
    W2Config,
    W2DeviceConfig,
    W2SerialDeviceConfig,
    W2_MODE_NAMES,
    W2_TRANSPORT_NAMES,
)
from fundamental.sources.bwt901 import (
    BWT901BLEConfig,
    BWT901DeviceConfig,
    BWT901Source,
    MAX_BWT901_DEVICES,
)
from fundamental.sources.myo import MyoBLEConfig, MyoSource
from fundamental.sources.serial_ads1299 import SerialADS1299Source
from fundamental.streams import StreamBlock, StreamSpec


LogSink = Callable[[str], None]
W2ModeName = Literal["emg_raw", "emg_rms", "eeg_raw"]


class AcquisitionController:
    """Own selected sources, coordinated lifecycle, buffers, health, and persistence."""

    def __init__(self) -> None:
        self._sources: dict[SourceName, AcquisitionSource] = {
            SerialADS1299Source.name: SerialADS1299Source(),
            BLEW2Source.name: BLEW2Source(),
            BWT901Source.name: BWT901Source(),
            MyoSource.name: MyoSource(),
        }
        self.source_name: SourceName = SerialADS1299Source.name
        self._active_source_names: tuple[SourceName, ...] = (self.source_name,)
        self.state = AcquisitionState.STOPPED
        self.buffer = CaptureStore(stream_specs=self._active_stream_specs())
        self.data_queue: queue.Queue[StreamBlock] = queue.Queue()
        self.event_queue: queue.Queue[WorkerEvent] = queue.Queue()
        self.worker: SourceWorker | None = None
        self.workers: tuple[SourceWorker, ...] = ()
        self.stop_event: threading.Event | None = None
        self.control: WorkerControl | None = None
        self._managed_session = False
        self._timeline_uses_shared_clock = False
        self._timeline_time_s = 0.0
        self.last_save_path = str(csv_writer.default_capture_path())
        self.capture_metadata: dict[str, Any] = {}
        self.device_health: dict[str, dict[str, Any]] = {}
        self.last_error = ""

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
    def bwt901_source(self) -> BWT901Source:
        return cast(BWT901Source, self._sources[BWT901Source.name])

    @bwt901_source.setter
    def bwt901_source(self, value: AcquisitionSource) -> None:
        self._sources[BWT901Source.name] = value

    @property
    def myo_source(self) -> MyoSource:
        return cast(MyoSource, self._sources[MyoSource.name])

    @myo_source.setter
    def myo_source(self, value: AcquisitionSource) -> None:
        self._sources[MyoSource.name] = value

    @property
    def config(self) -> SerialConfig:
        return self.serial_source.config

    @config.setter
    def config(self, value: SerialConfig) -> None:
        self.serial_source = self.serial_source.with_config(value)

    @property
    def w2_config(self) -> W2Config:
        return self.w2_source.config

    @property
    def bwt901_config(self) -> BWT901BLEConfig:
        return self.bwt901_source.config

    @property
    def myo_config(self) -> MyoBLEConfig:
        return self.myo_source.config

    @property
    def source(self) -> AcquisitionSource:
        """Compatibility alias for the primary selected source."""

        return self._sources[self.source_name]

    @property
    def active_source_names(self) -> tuple[SourceName, ...]:
        return self._active_source_names

    @property
    def timeline_time_s(self) -> float:
        """Return active-capture time, frozen across pauses and after stop.

        Coordinated sources use their shared capture clock. Legacy sources keep
        their existing sample-time behavior so this accessor does not change
        protocol parsing or timestamp generation.
        """

        if self._timeline_uses_shared_clock:
            if self.control is not None:
                return max(0.0, float(self.control.clock.now()))
            return max(0.0, float(self._timeline_time_s))
        return max(0.0, float(self.buffer.latest_time_s))

    def active_sources(self) -> tuple[AcquisitionSource, ...]:
        return tuple(self._sources[name] for name in self._active_source_names)

    def available_sources(self) -> tuple[tuple[SourceName, str], ...]:
        return tuple((name, source.display_name) for name, source in self._sources.items())

    def source_display_text(self) -> str:
        return " + ".join(source.display_text() for source in self.active_sources())

    def configured_source(self, source_name: SourceName) -> AcquisitionSource:
        return self._sources[source_name]

    def select_source(self, source_name: str) -> str | None:
        """Select exactly one source; retained for ADS1299/Myo and compatibility."""

        return self.select_sources((cast(SourceName, source_name.strip()),))

    def select_sources(self, source_names: Sequence[str]) -> str | None:
        normalized = tuple(dict.fromkeys(name.strip() for name in source_names if name.strip()))
        available = {name for name, _label in self.available_sources()}
        unknown = next((name for name in normalized if name not in available), None)
        if unknown is not None:
            return f"Unknown acquisition source: {unknown}"
        if not normalized:
            return "Select at least one acquisition source."
        if normalized == self._active_source_names:
            return None
        if self.state != AcquisitionState.STOPPED:
            return "Stop acquisition before changing source."

        typed_names = cast(tuple[SourceName, ...], normalized)
        specs = self._stream_specs_for(typed_names)
        stream_ids = [spec.stream_id for spec in specs]
        if len(stream_ids) != len(set(stream_ids)):
            return "Selected sources expose duplicate stream IDs."

        ble_error = self._validate_active_ble_addresses(typed_names)
        if ble_error:
            return ble_error
        self._active_source_names = typed_names
        self.source_name = typed_names[0]
        self.buffer.configure_streams(specs, clear=True)
        return None

    def update_config(
        self,
        port: str | None = None,
        baud_rate: int | None = None,
        timeout_s: float | None = None,
    ) -> str | None:
        return self.update_serial_config(port, baud_rate, timeout_s)

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
        self._refresh_buffer_if_active(SerialADS1299Source.name)
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
        transport: str | None = None,
        serial_baud_rate: int | None = None,
        serial_timeout_s: float | None = None,
        serial_devices: tuple[W2SerialDeviceConfig, ...] | None = None,
        devices: tuple[W2DeviceConfig, ...] | None = None,
    ) -> str | None:
        if self.state != AcquisitionState.STOPPED:
            return "Stop acquisition before changing W2 configuration."

        current = self.w2_config
        mode_value = current.mode if mode is None else mode.strip()
        if mode_value not in W2_MODE_NAMES:
            return f"Unsupported W2 mode: {mode_value}"
        transport_value = current.transport if transport is None else transport.strip()
        if transport_value not in W2_TRANSPORT_NAMES:
            return f"Unsupported W2 transport: {transport_value}"

        use_legacy_rows = devices is None and (transport is not None or serial_devices is not None)
        next_devices = () if use_legacy_rows else (current.devices if devices is None else devices)
        next_config = W2Config(
            address=current.address if address is None else address,
            device_name_filter=(
                current.device_name_filter if device_name_filter is None else device_name_filter
            ),
            notify_uuid=current.notify_uuid if notify_uuid is None else notify_uuid,
            write_uuid=current.write_uuid if write_uuid is None else write_uuid,
            mode=cast(W2ModeName, mode_value),
            sample_rate_hz=current.sample_rate_hz if sample_rate_hz is None else sample_rate_hz,
            scan_timeout_s=current.scan_timeout_s if scan_timeout_s is None else scan_timeout_s,
            transport=cast(Any, transport_value),
            serial_baud_rate=(
                current.serial_baud_rate if serial_baud_rate is None else serial_baud_rate
            ),
            serial_timeout_s=(
                current.serial_timeout_s if serial_timeout_s is None else serial_timeout_s
            ),
            serial_devices=current.serial_devices if serial_devices is None else serial_devices,
            devices=next_devices,
        ).normalized()
        error = self._validate_w2_config(next_config)
        if error:
            return error
        self.w2_source = self.w2_source.with_config(next_config)
        self._refresh_buffer_if_active(BLEW2Source.name)
        return None

    def update_bwt901_config(
        self,
        *,
        devices: tuple[BWT901DeviceConfig, ...] | None = None,
        service_uuid: str | None = None,
        notify_uuid: str | None = None,
        write_uuid: str | None = None,
        scan_timeout_s: float | None = None,
    ) -> str | None:
        if self.state != AcquisitionState.STOPPED:
            return "Stop acquisition before changing BWT901 configuration."
        current = self.bwt901_config
        next_config = BWT901BLEConfig(
            devices=current.devices if devices is None else devices,
            service_uuid=current.service_uuid if service_uuid is None else service_uuid,
            notify_uuid=current.notify_uuid if notify_uuid is None else notify_uuid,
            write_uuid=current.write_uuid if write_uuid is None else write_uuid,
            scan_timeout_s=current.scan_timeout_s if scan_timeout_s is None else scan_timeout_s,
        ).normalized()
        error = self._validate_bwt901_config(next_config)
        if error:
            return error
        self.bwt901_source = self.bwt901_source.with_config(next_config)
        self._refresh_buffer_if_active(BWT901Source.name)
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
        current = self.myo_config
        next_config = MyoBLEConfig(
            address=current.address if address is None else address,
            device_name_filter=(
                current.device_name_filter if device_name_filter is None else device_name_filter
            ),
            scan_timeout_s=current.scan_timeout_s if scan_timeout_s is None else scan_timeout_s,
            connect_timeout_s=(
                current.connect_timeout_s if connect_timeout_s is None else connect_timeout_s
            ),
            enable_emg=current.enable_emg if enable_emg is None else enable_emg,
            enable_imu=current.enable_imu if enable_imu is None else enable_imu,
        ).normalized()
        if not next_config.address and not next_config.device_name_filter:
            return "Myo BLE address and name filter cannot both be empty."
        if not next_config.enable_emg and not next_config.enable_imu:
            return "Enable at least one Myo data stream."
        self.myo_source = self.myo_source.with_config(next_config)
        self._refresh_buffer_if_active(MyoSource.name)
        return None

    def start(self) -> str:
        if self.state in (AcquisitionState.STARTING, AcquisitionState.RUNNING):
            return "Acquisition is already starting or running."

        if self.state == AcquisitionState.PAUSED and self._managed_session:
            assert self.control is not None
            self.control.clock.resume()
            self.control.capture_event.set()
            for health in self.device_health.values():
                if health.get("status") != "error":
                    health["status"] = "resuming"
            self.state = AcquisitionState.RUNNING
            return f"Acquisition resumed with {self.source_display_text()}."

        if self.state == AcquisitionState.STOPPED:
            specs = self._active_stream_specs()
            self.buffer.reset(specs)
            self._timeline_time_s = 0.0
            self.last_save_path = str(csv_writer.default_capture_path(create_directory=True))
            self.capture_metadata = {
                "capture_started_at": datetime.now().astimezone().isoformat(),
                "sources": {
                    source.name: source.capture_metadata() for source in self.active_sources()
                },
            }
            self.device_health.clear()
            self.last_error = ""

        resume_state = self.buffer.resume_state()
        self._clear_queues()
        self.stop_event = threading.Event()
        self._managed_session = all(
            bool(getattr(source, "supports_managed_lifecycle", False))
            for source in self.active_sources()
        )
        self._timeline_uses_shared_clock = self._managed_session
        self.control = None
        if self._managed_session:
            self.control = WorkerControl(
                self.stop_event,
                clock=CaptureClock(resume_state.latest_time_s),
            )

        created: list[SourceWorker] = []
        try:
            for source in self.active_sources():
                if self._managed_session:
                    worker = source.create_worker(  # type: ignore[call-arg]
                        self.data_queue,
                        self.event_queue,
                        self.stop_event,
                        resume_state,
                        control=self.control,
                    )
                else:
                    worker = source.create_worker(
                        self.data_queue,
                        self.event_queue,
                        self.stop_event,
                        resume_state,
                    )
                created.append(worker)
            self.workers = tuple(created)
            self.worker = (
                created[0]
                if len(created) == 1
                else SourceWorkerGroup(
                    tuple(created), self.data_queue, self.event_queue, self.stop_event
                )
            )
            self.worker.start()
        except Exception:
            self.stop_event.set()
            for worker in created:
                if worker.is_alive():
                    worker.join(timeout=1.0)
            self.worker = None
            self.workers = ()
            self.stop_event = None
            self.control = None
            self.state = AcquisitionState.STOPPED
            raise

        if self._managed_session:
            self.state = AcquisitionState.STARTING
            return f"Acquisition connecting to {self.source_display_text()}."
        self.state = AcquisitionState.RUNNING
        return f"Acquisition started with {self.source_display_text()}."

    def pause(self) -> str:
        if self.state != AcquisitionState.RUNNING:
            return "Acquisition is not running."
        if self._managed_session and self.control is not None:
            self.control.capture_event.clear()
            self.control.clock.pause()
            self._timeline_time_s = self.control.clock.now()
            self._drain_data_queue()
        else:
            self._stop_worker()
        for health in self.device_health.values():
            if health.get("status") != "error":
                health["status"] = "paused"
        self.state = AcquisitionState.PAUSED
        return (
            f"Acquisition paused with {self.buffer.row_count} rows buffered across "
            f"{self.buffer.stream_count} stream(s)."
        )

    def stop(self) -> str:
        if self.worker is not None:
            self._stop_worker()
        for health in self.device_health.values():
            if health.get("status") != "error":
                health["status"] = "stopped"
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
        stimulus_code_for_sample: csv_writer.SampleStimulusCodeResolver | None = None,
        stimulus_metadata: dict[str, Any] | None = None,
    ) -> str:
        if self.state in (AcquisitionState.STARTING, AcquisitionState.RUNNING):
            return "Pause or stop acquisition before saving."
        snapshots = self.buffer.snapshots()
        if not snapshots:
            return "No samples to save."
        save_path = str(path).strip() if path is not None else self.last_save_path
        if not save_path:
            save_path = self.last_save_path
        metadata = dict(self.capture_metadata)
        metadata["device_health"] = {
            source_id: dict(health) for source_id, health in self.device_health.items()
        }
        if stimulus_metadata is not None:
            metadata["stimulus"] = dict(stimulus_metadata)
        result = csv_writer.save_capture(
            save_path,
            snapshots,
            stimulus_code_for_time=stimulus_code_for_time,
            stimulus_code_for_sample=stimulus_code_for_sample,
            stimulus_log_rows=stimulus_log_rows,
            metadata=metadata,
        )
        self.last_save_path = str(save_path)
        stream_text = ", ".join(
            f"{stream.stream_id}: {stream.row_count} rows -> {stream.path}"
            for stream in result.streams
        )
        message = (
            f"Saved {result.total_rows} rows ({stream_text}); "
            f"metadata -> {result.metadata_path}."
        )
        if result.stimulus_path is not None:
            message += f" Stimulus events: {result.stimulus_rows} -> {result.stimulus_path}."
        return message

    def drain_queues(self, log_sink: LogSink | None = None, max_batches: int = 64) -> int:
        failed = False
        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break
            if log_sink is not None and event.message:
                log_sink(event.message)
            self._handle_event(event)
            failed = failed or event.kind == "error"

        if failed:
            self._stop_worker(join_timeout_s=1.0)
            self.state = AcquisitionState.STOPPED
        elif self.state == AcquisitionState.STARTING:
            self._advance_starting(log_sink)

        appended = 0
        for _ in range(max(1, int(max_batches))):
            try:
                block = self.data_queue.get_nowait()
            except queue.Empty:
                break
            appended += self.buffer.append_block(block)
        return appended

    def health_lines(self) -> tuple[str, ...]:
        lines: list[str] = []
        for source_id, health in sorted(self.device_health.items()):
            rate = health.get("observed_rate_hz")
            age = health.get("last_frame_age_s")
            rate_text = "-" if rate is None else f"{float(rate):.1f} Hz"
            age_text = "-" if age is None else f"{float(age):.2f}s"
            lines.append(
                f"{source_id}: {health.get('status', '-')} | frames={health.get('frames', 0)} "
                f"parse_errors={health.get('parser_errors', 0)} "
                f"skipped_bytes={health.get('skipped_bytes', 0)} | "
                f"rate={rate_text} | last={age_text}"
            )
        if self.last_error:
            lines.append(f"Last error: {self.last_error}")
        return tuple(lines)

    def shutdown(self) -> None:
        self._stop_worker()
        self.state = AcquisitionState.STOPPED

    def _advance_starting(self, log_sink: LogSink | None) -> None:
        if not self.workers:
            return
        if all(_worker_ready(worker) for worker in self.workers):
            assert self.control is not None
            self.control.clock.resume()
            self.control.capture_event.set()
            self.state = AcquisitionState.RUNNING
            if log_sink is not None:
                log_sink("All configured devices are ready; acquisition started.")
            return
        if any(not worker.is_alive() for worker in self.workers):
            self.last_error = "A source worker exited before all configured devices were ready."
            if log_sink is not None:
                log_sink(self.last_error)
            self._stop_worker(join_timeout_s=1.0)
            self.state = AcquisitionState.STOPPED

    def _handle_event(self, event: WorkerEvent) -> None:
        if event.kind == "metadata" and event.data is not None:
            runtime = self.capture_metadata.setdefault("runtime", {})
            if event.source_id:
                runtime[event.source_id] = dict(event.data)
            else:
                self.capture_metadata.update(dict(event.data))
        elif event.kind in ("ready", "health") and event.source_id:
            health = self.device_health.setdefault(event.source_id, {})
            if event.kind == "ready":
                health["status"] = "ready"
            if event.data is not None:
                health.update(dict(event.data))
        elif event.kind == "error":
            self.last_error = event.message or f"Source {event.source_id or '-'} failed."
            if event.source_id:
                health = self.device_health.setdefault(event.source_id, {})
                health["status"] = "error"
                health["error"] = self.last_error
            self.capture_metadata["failure"] = {
                "source_id": event.source_id,
                "message": self.last_error,
                "time": datetime.now().astimezone().isoformat(),
            }

    def _stop_worker(self, join_timeout_s: float = 5.0) -> None:
        if self.control is not None:
            self.control.clock.pause()
            self._timeline_time_s = self.control.clock.now()
        if self.stop_event is not None:
            self.stop_event.set()
        if self.control is not None:
            self.control.capture_event.set()
        if self.worker is not None and self.worker.is_alive():
            self.worker.join(timeout=join_timeout_s)
        self.worker = None
        self.workers = ()
        self.stop_event = None
        self.control = None
        self._managed_session = False
        self._drain_data_queue()
        self._drain_event_queue()

    def _active_stream_specs(self) -> tuple[StreamSpec, ...]:
        return self._stream_specs_for(self._active_source_names)

    def _stream_specs_for(self, names: Sequence[SourceName]) -> tuple[StreamSpec, ...]:
        return tuple(spec for name in names for spec in self._sources[name].stream_specs())

    def _refresh_buffer_if_active(self, source_name: SourceName) -> None:
        if source_name in self._active_source_names:
            self.buffer.configure_streams(self._active_stream_specs(), clear=True)

    def _validate_w2_config(self, config: W2Config) -> str | None:
        devices = config.effective_devices()
        if not devices:
            return "Configure at least one W2 device."
        if len(devices) > MAX_W2_DEVICES:
            return f"Configure at most {MAX_W2_DEVICES} W2 devices."
        ids = [device.device_id.casefold() for device in devices]
        if any(not device.device_id for device in devices):
            return "W2 device ID cannot be empty."
        if len(ids) != len(set(ids)):
            return "W2 device IDs must be unique."
        serial_devices = [device for device in devices if device.transport == "serial"]
        if any(not device.port for device in serial_devices):
            return "W2 serial Port cannot be empty."
        ports = [device.port.casefold() for device in serial_devices]
        if len(ports) != len(set(ports)):
            return "Each W2 serial device must use a different Port."
        ble_devices = [device for device in devices if device.transport == "ble"]
        if any(not device.address and not device.device_name_filter for device in ble_devices):
            return "W2 BLE address and name filter cannot both be empty."
        if len(ble_devices) > 1 and any(not device.address for device in ble_devices):
            return "Each W2 BLE device needs an explicit address when multiple devices are used."
        addresses = [device.address.casefold() for device in ble_devices if device.address]
        if len(addresses) != len(set(addresses)):
            return "W2 BLE addresses must be unique."
        if ble_devices and not config.notify_uuid:
            return "W2 BLE notify UUID cannot be empty."
        if ble_devices and not config.write_uuid:
            return "W2 BLE write UUID cannot be empty."
        return None

    def _validate_bwt901_config(self, config: BWT901BLEConfig) -> str | None:
        devices = config.devices
        if not devices:
            return "Configure at least one BWT901 device."
        if len(devices) > MAX_BWT901_DEVICES:
            return f"Configure at most {MAX_BWT901_DEVICES} BWT901 devices."
        ids = [device.device_id.casefold() for device in devices]
        if any(not device.device_id for device in devices):
            return "BWT901 device ID cannot be empty."
        if len(ids) != len(set(ids)):
            return "BWT901 device IDs must be unique."
        if any(not device.address and not device.name_filter for device in devices):
            return "BWT901 address and name filter cannot both be empty."
        if len(devices) > 1 and any(not device.address for device in devices):
            return "Each BWT901 needs an explicit BLE address when multiple devices are used."
        addresses = [device.address.casefold() for device in devices if device.address]
        if len(addresses) != len(set(addresses)):
            return "BWT901 BLE addresses must be unique."
        if not config.notify_uuid:
            return "BWT901 notify UUID cannot be empty."
        return None

    def _validate_active_ble_addresses(self, names: Sequence[SourceName]) -> str | None:
        addresses: list[str] = []
        if BLEW2Source.name in names:
            addresses.extend(
                device.address.casefold()
                for device in self.w2_config.effective_devices()
                if device.transport == "ble" and device.address
            )
        if BWT901Source.name in names:
            addresses.extend(
                device.address.casefold()
                for device in self.bwt901_config.devices
                if device.address
            )
        if len(addresses) != len(set(addresses)):
            return "Every active BLE device must use a unique address."
        return None

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
                block = self.data_queue.get_nowait()
            except queue.Empty:
                return
            self.buffer.append_block(block)

    def _drain_event_queue(self) -> None:
        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                return
            self._handle_event(event)


def _worker_ready(worker: SourceWorker) -> bool:
    ready_event = getattr(worker, "ready_event", None)
    return bool(ready_event is None or ready_event.is_set())


__all__ = ["AcquisitionController"]
