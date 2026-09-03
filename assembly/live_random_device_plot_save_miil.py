"""Arbitrary Myo / RunE W2 / BWT901 composition with Plot + Save + manual MIIL.

This file is intentionally a composition harness rather than a device framework.
Each device keeps its own worker and ingestor semantics while all normalized
streams share the same runtime Store, Plot provider, and Recorder.

Configuration may contain zero, one, or many instances of each device type.  At
least one physical device must be configured overall.
"""

from __future__ import annotations

import asyncio
import json
import queue
import re
import time
from dataclasses import dataclass
from pathlib import Path

import dearpygui.dearpygui as dpg
from bleak import BleakScanner
from bleak.backends.device import BLEDevice

from assembly.acquisition.BLE.bwt901_ingest import (
    BWT901_FIELD_KEYS,
    BWT901RecordIngestor,
    bwt901_stream_id,
    make_bwt901_stream_schema,
)
from assembly.acquisition.BLE.bwt901_worker import (
    BWT901BLEConfig,
    BWT901BLEWorker,
    BWT901Record,
)
from assembly.acquisition.BLE.myo_ingest import (
    MyoRecordIngestor,
    make_myo_stream_schemas,
    myo_emg_stream_id,
    myo_imu_stream_id,
)
from assembly.acquisition.BLE.myo_worker import MyoRecord, MyoWorker
from assembly.acquisition.runtime.queue_pump import QueuePump
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore, StreamSchema
from assembly.acquisition.runtime.worker_group import ManagedWorker, WorkerGroup
from assembly.acquisition.serial.w2_ingest import (
    W2RecordIngestor,
    make_w2_stream_schema,
    w2_stream_id,
)
from assembly.acquisition.serial.w2_worker import (
    ResolvedW2SerialConfig,
    SerialW2Worker,
    W2Record,
    W2SerialConfig,
    resolve_w2_configs,
)
from assembly.experiment.miil import (
    MIILAction,
    MIILController,
    MIILState,
    capture_host_boundary,
)
from assembly.plot.models import SeriesSpec
from assembly.plot.plot_window import create_plot_window
from assembly.plot.realtime_provider import BufferedPlotProvider
from assembly.save.selectable_recorder import SelectableStreamRecorder
from assembly.save.store_tap import StreamStoreTap


@dataclass(frozen=True, slots=True)
class MyoDeviceConfig:
    """Composition-level identity and BLE address for one physical Myo."""

    device_id: str
    address: str
    scan_timeout_s: float = 10.0
    connect_timeout_s: float = 20.0

    def __post_init__(self) -> None:
        device_id = self.device_id.strip()
        address = self.address.strip()
        if not device_id:
            raise ValueError("Myo device_id must not be empty.")
        if not address:
            raise ValueError("Myo address must not be empty.")
        if self.scan_timeout_s <= 0 or self.connect_timeout_s <= 0:
            raise ValueError("Myo scan/connect timeouts must be positive.")
        object.__setattr__(self, "device_id", device_id)
        object.__setattr__(self, "address", address)
        object.__setattr__(self, "scan_timeout_s", float(self.scan_timeout_s))
        object.__setattr__(self, "connect_timeout_s", float(self.connect_timeout_s))


# ======================================================================
# HARDWARE CONFIGURATION
# ======================================================================
#
# Each tuple may contain 0, 1, or N devices.  Keep only devices that should
# participate in this run.  The default preserves the already-used 2x W2 setup;
# examples for Myo/BWT901 are left below as comments to avoid placeholder BLE
# addresses making the script fail before the configured devices can start.

MYO_DEVICES: tuple[MyoDeviceConfig, ...] = (
    # MyoDeviceConfig("left_arm", "AA:BB:CC:DD:EE:FF"),
    # MyoDeviceConfig("right_arm", "11:22:33:44:55:66"),
)

W2_DEVICES: tuple[W2SerialConfig, ...] = (
    W2SerialConfig("COM9"),
    W2SerialConfig("COM11"),
)

BWT901_DEVICES: tuple[BWT901BLEConfig, ...] = (
    # BWT901BLEConfig(
    #     "imu_1",
    #     address="E9:34:17:08:9F:4A",
    #     name_filter="WT901BLE67",
    # ),
)


# ======================================================================
# RUNTIME CONFIGURATION
# ======================================================================

RETENTION_SECONDS = 35.0
MYO_QUEUE_SIZE = 4096
W2_QUEUE_SIZE = 4096
BWT901_QUEUE_SIZE = 2048
MAX_RECORDS_PER_PUMP_PER_FRAME = 4096
SHUTDOWN_TIMEOUT_S = 8.0


_BWT901_FIELD_PLOT_CONFIG = {
    "acc_x_g": ("Accel X", "g", "acceleration", (-16.0, 16.0), True),
    "acc_y_g": ("Accel Y", "g", "acceleration", (-16.0, 16.0), True),
    "acc_z_g": ("Accel Z", "g", "acceleration", (-16.0, 16.0), True),
    "gyro_x_dps": ("Gyro X", "deg/s", "angular_velocity", (-2000.0, 2000.0), False),
    "gyro_y_dps": ("Gyro Y", "deg/s", "angular_velocity", (-2000.0, 2000.0), False),
    "gyro_z_dps": ("Gyro Z", "deg/s", "angular_velocity", (-2000.0, 2000.0), False),
    "angle_x_deg": ("Angle X", "deg", "generic", (-180.0, 180.0), False),
    "angle_y_deg": ("Angle Y", "deg", "generic", (-180.0, 180.0), False),
    "angle_z_deg": ("Angle Z", "deg", "generic", (-180.0, 180.0), False),
}


def _validate_configs(
    myos: tuple[MyoDeviceConfig, ...],
    w2s: tuple[W2SerialConfig, ...],
    bwts: tuple[BWT901BLEConfig, ...],
) -> None:
    if not myos and not w2s and not bwts:
        raise ValueError("Configure at least one Myo, W2, or BWT901 device.")

    myo_ids = [config.device_id.casefold() for config in myos]
    if len(set(myo_ids)) != len(myo_ids):
        raise ValueError("Myo device IDs must be unique.")
    myo_addresses = [config.address.casefold() for config in myos]
    if len(set(myo_addresses)) != len(myo_addresses):
        raise ValueError("Myo BLE addresses must be unique.")

    w2_ports = [config.port.casefold() for config in w2s]
    if len(set(w2_ports)) != len(w2_ports):
        raise ValueError("Each W2 device must use a different serial port.")

    bwt_ids = [config.device_id.casefold() for config in bwts]
    if len(set(bwt_ids)) != len(bwt_ids):
        raise ValueError("BWT901 device IDs must be unique.")
    if len(bwts) > 1:
        addresses = [config.address.casefold() for config in bwts]
        if any(not address for address in addresses):
            raise ValueError("Multiple BWT901 devices require explicit BLE addresses.")
        if len(set(addresses)) != len(addresses):
            raise ValueError("BWT901 BLE addresses must be unique.")


def _schemas(
    myos: tuple[MyoDeviceConfig, ...],
    w2s: tuple[ResolvedW2SerialConfig, ...],
    bwts: tuple[BWT901BLEConfig, ...],
) -> tuple[StreamSchema, ...]:
    schemas: list[StreamSchema] = []
    for config in myos:
        schemas.extend(make_myo_stream_schemas(config.device_id))
    schemas.extend(
        make_w2_stream_schema(config.device_id, nominal_rate_hz=config.nominal_rate_hz)
        for config in w2s
    )
    schemas.extend(make_bwt901_stream_schema(config.device_id) for config in bwts)
    return tuple(schemas)


def _myo_plot_specs(config: MyoDeviceConfig) -> tuple[SeriesSpec, ...]:
    emg_stream_id = myo_emg_stream_id(config.device_id)
    imu_stream_id = myo_imu_stream_id(config.device_id)
    label_prefix = f"Myo {config.device_id}"
    return (
        *tuple(
            SeriesSpec(
                series_id=f"{emg_stream_id}/ch{channel}",
                stream_id=emg_stream_id,
                field_key=f"emg_ch{channel}_code",
                label=f"{label_prefix} EMG CH {channel}",
                unit="code",
                signal_kind="emg",
                default_plot=True,
                fixed_range=(-128.0, 127.0),
            )
            for channel in range(1, 9)
        ),
        *tuple(
            SeriesSpec(
                series_id=f"{imu_stream_id}/quat_{axis}",
                stream_id=imu_stream_id,
                field_key=f"quat_{axis}",
                label=f"{label_prefix} Quaternion {axis.upper()}",
                unit="",
                signal_kind="quaternion",
                default_plot=False,
                fixed_range=(-1.0, 1.0),
            )
            for axis in "wxyz"
        ),
        *tuple(
            SeriesSpec(
                series_id=f"{imu_stream_id}/accel_{axis}_g",
                stream_id=imu_stream_id,
                field_key=f"accel_{axis}_g",
                label=f"{label_prefix} Acceleration {axis.upper()}",
                unit="g",
                signal_kind="acceleration",
                default_plot=False,
                fixed_range=(-16.0, 16.0),
            )
            for axis in "xyz"
        ),
        *tuple(
            SeriesSpec(
                series_id=f"{imu_stream_id}/gyro_{axis}_dps",
                stream_id=imu_stream_id,
                field_key=f"gyro_{axis}_dps",
                label=f"{label_prefix} Gyroscope {axis.upper()}",
                unit="deg/s",
                signal_kind="angular_velocity",
                default_plot=False,
                fixed_range=(-2048.0, 2048.0),
            )
            for axis in "xyz"
        ),
    )


def _w2_plot_spec(config: ResolvedW2SerialConfig) -> SeriesSpec:
    stream_id = w2_stream_id(config.device_id)
    signal_kind = {
        "emg_raw": "emg",
        "emg_rms": "generic",
        "eeg_raw": "eeg",
    }[config.mode]
    return SeriesSpec(
        series_id=f"{stream_id}/value",
        stream_id=stream_id,
        field_key="value",
        label=f"W2 {config.device_id}",
        unit="code",
        signal_kind=signal_kind,  # type: ignore[arg-type]
        default_plot=True,
        fixed_range=None,
    )


def _bwt901_plot_specs(config: BWT901BLEConfig) -> tuple[SeriesSpec, ...]:
    stream_id = bwt901_stream_id(config.device_id)
    specs: list[SeriesSpec] = []
    for field_key in BWT901_FIELD_KEYS:
        label, unit, signal_kind, fixed_range, default_plot = _BWT901_FIELD_PLOT_CONFIG[field_key]
        specs.append(
            SeriesSpec(
                series_id=f"{stream_id}/{field_key}",
                stream_id=stream_id,
                field_key=field_key,
                label=f"BWT901 {config.device_id} {label}",
                unit=unit,
                signal_kind=signal_kind,  # type: ignore[arg-type]
                default_plot=default_plot,
                fixed_range=fixed_range,
            )
        )
    return tuple(specs)


def _plot_specs(
    myos: tuple[MyoDeviceConfig, ...],
    w2s: tuple[ResolvedW2SerialConfig, ...],
    bwts: tuple[BWT901BLEConfig, ...],
) -> tuple[SeriesSpec, ...]:
    specs: list[SeriesSpec] = []
    for config in myos:
        specs.extend(_myo_plot_specs(config))
    specs.extend(_w2_plot_spec(config) for config in w2s)
    for config in bwts:
        specs.extend(_bwt901_plot_specs(config))
    return tuple(specs)


async def _find_myo_device(config: MyoDeviceConfig) -> BLEDevice:
    device = await BleakScanner.find_device_by_address(
        config.address,
        timeout=config.scan_timeout_s,
    )
    if device is None:
        raise RuntimeError(
            f"Could not find Myo {config.device_id!r} at BLE address {config.address!r}."
        )
    return device


def _resolve_myo_devices(
    configs: tuple[MyoDeviceConfig, ...],
) -> dict[str, BLEDevice]:
    # Discovery is deliberately sequential.  It is setup work, and avoiding
    # concurrent Windows BLE scans is more predictable than optimizing startup.
    resolved: dict[str, BLEDevice] = {}
    for config in configs:
        resolved[config.device_id] = asyncio.run(_find_myo_device(config))
    return resolved


def _startup_timeout_s(
    myos: tuple[MyoDeviceConfig, ...],
    bwts: tuple[BWT901BLEConfig, ...],
) -> float:
    # BWT901 workers deliberately serialize Windows BLE scan/connect attempts,
    # so their scan budgets accumulate.  Myo workers may connect concurrently.
    myo_budget = max((config.connect_timeout_s + 10.0 for config in myos), default=0.0)
    bwt_budget = sum(config.scan_timeout_s + 10.0 for config in bwts)
    return max(15.0, myo_budget, bwt_budget)


def _print_worker_summary(workers: dict[str, ManagedWorker]) -> None:
    print("\n[Workers]")
    for worker_id, worker in workers.items():
        print(
            f"{worker_id:24} "
            f"alive={worker.is_alive()} "
            f"startup={worker.startup_event.is_set()} "
            f"stopped={worker.stopped_event.is_set()} "
            f"error={worker.error!r}"
        )




class IntegratedSaveMIILPanel:
    """Composition-only coordinator for Recorder + manual MIIL.

    Acquisition itself continues independently. Starting a session opens the
    Recorder and MIIL at one operator boundary; stopping closes MIIL, stops the
    Recorder, and writes a JSON sidecar next to the recording output.
    """

    def __init__(
        self,
        recorder: SelectableStreamRecorder,
        schemas: tuple[StreamSchema, ...],
        *,
        tag_prefix: str = "assembly.random_device_plot_save_miil",
        default_directory: str | Path = "captures",
        default_filename: str = "multi_device_capture.h5",
    ) -> None:
        self.recorder = recorder
        self.schemas = schemas
        self.miil = MIILController()
        self.prefix = tag_prefix
        self._default_directory = str(default_directory)
        self._default_filename = default_filename
        self._last_message = "Ready."
        self._configuration_dirty = False
        self._editor_actions = list(self.miil.actions)
        self._history_signature: tuple[tuple[object, ...], ...] = ()

        self.directory_tag = f"{tag_prefix}.directory"
        self.filename_tag = f"{tag_prefix}.filename"
        self.format_tag = f"{tag_prefix}.format"
        self.start_tag = f"{tag_prefix}.start"
        self.stop_tag = f"{tag_prefix}.stop"
        self.pause_tag = f"{tag_prefix}.pause"
        self.resume_tag = f"{tag_prefix}.resume"
        self.status_tag = f"{tag_prefix}.status"
        self.current_tag = f"{tag_prefix}.current"
        self.rows_tag = f"{tag_prefix}.rows"
        self.path_tag = f"{tag_prefix}.path"
        self.config_status_tag = f"{tag_prefix}.config_status"
        self.action_editor_tag = f"{tag_prefix}.action_editor"
        self.action_buttons_tag = f"{tag_prefix}.action_buttons"
        self.history_tag = f"{tag_prefix}.history"

    def build(self) -> None:
        dpg.add_text("Recording Session + Manual Instruction Interval Labeling (MIIL)")
        dpg.add_text(
            "Acquisition/Plot run continuously. Session Start/Stop controls only Recorder + MIIL."
        )
        dpg.add_text(
            "Pause MIIL pauses operator changes only; recording continues and the current instruction remains active."
        )
        dpg.add_separator()
        dpg.add_input_text(
            label="Directory", tag=self.directory_tag, default_value=self._default_directory, width=500
        )
        dpg.add_input_text(
            label="File name", tag=self.filename_tag, default_value=self._default_filename, width=500
        )
        dpg.add_combo(
            ("HDF5", "CSV"), label="Format", tag=self.format_tag, default_value="HDF5", width=160
        )
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Start Session", tag=self.start_tag, callback=self._on_start, width=150
            )
            dpg.add_button(
                label="Stop Session", tag=self.stop_tag, callback=self._on_stop, width=150
            )
            dpg.add_button(
                label="Pause MIIL", tag=self.pause_tag, callback=self._on_pause, width=120
            )
            dpg.add_button(
                label="Resume MIIL", tag=self.resume_tag, callback=self._on_resume, width=120
            )
        dpg.add_text("", tag=self.status_tag)
        dpg.add_text("", tag=self.rows_tag)
        dpg.add_text("", tag=self.path_tag, wrap=720)

        dpg.add_separator()
        dpg.add_text("MIIL Action Configuration")
        dpg.add_text(
            "Edit actions while stopped, then Apply. Action keys are derived from labels for metadata."
        )
        with dpg.child_window(tag=self.action_editor_tag, width=-1, height=145, border=True):
            pass
        with dpg.group(horizontal=True):
            dpg.add_button(label="Add Action", callback=self._on_add_action, width=120)
            dpg.add_button(label="Apply Actions", callback=self._on_apply_actions, width=120)
        dpg.add_text("", tag=self.config_status_tag)

        dpg.add_separator()
        dpg.add_text("MIIL Operator Console")
        dpg.add_text("", tag=self.current_tag)
        with dpg.child_window(tag=self.action_buttons_tag, width=-1, height=62, horizontal_scrollbar=True):
            pass
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="No Stimulus (0)", callback=self._on_no_stimulus, width=180
            )
            dpg.add_button(
                label="Drop Current Interval (-1)", callback=self._on_drop, width=220
            )

        dpg.add_text("Interval History")
        with dpg.child_window(tag=self.history_tag, width=-1, height=175, horizontal_scrollbar=True):
            pass

        self._rebuild_editor()
        self._rebuild_action_buttons()
        self.refresh(force_history=True)

    def refresh(self, *, force_history: bool = False) -> None:
        if not dpg.does_item_exist(self.status_tag):
            return
        state = self.miil.state.value.upper()
        dpg.set_value(
            self.status_tag,
            f"Recorder: {self.recorder.state.value.upper()} | MIIL: {state} | {self._last_message}",
        )
        dpg.set_value(self.rows_tag, f"Rows written: {self.recorder.rows_written}")
        output = self.recorder.path
        dpg.set_value(self.path_tag, "Output: -" if output is None else f"Output: {output}")
        dpg.set_value(
            self.current_tag,
            f"Current: {self.miil.current_label} | code {self.miil.current_code} | "
            f"elapsed {self.miil.current_elapsed_s(time.perf_counter_ns()):.3f} s",
        )
        dpg.set_value(
            self.config_status_tag,
            "Action configuration has unapplied edits." if self._configuration_dirty else "Actions applied.",
        )

        active = self.recorder.is_recording
        running = self.miil.state is MIILState.RUNNING
        paused = self.miil.state is MIILState.PAUSED
        for tag in (self.directory_tag, self.filename_tag, self.format_tag):
            dpg.configure_item(tag, enabled=not active)
        dpg.configure_item(self.start_tag, enabled=not active)
        dpg.configure_item(self.stop_tag, enabled=active)
        dpg.configure_item(self.pause_tag, enabled=active and running)
        dpg.configure_item(self.resume_tag, enabled=active and paused)

        self._refresh_history(force=force_history)

    def stop_if_active(self) -> None:
        if self.recorder.is_recording:
            self._stop_session(write_sidecar=True)
        elif self.miil.state in (MIILState.RUNNING, MIILState.PAUSED):
            self.miil.stop(capture_host_boundary())

    def _on_start(self, *_args) -> None:
        if self._configuration_dirty:
            self._last_message = "Apply edited MIIL actions before Start."
            self.refresh()
            return
        try:
            selected = str(dpg.get_value(self.format_tag)).strip().casefold()
            self.recorder.set_format("csv" if selected == "csv" else "hdf5")
            output = self.recorder.start(self._requested_output_path(), self.schemas)
            boundary = capture_host_boundary()
            message = self.miil.start(boundary)
            self._last_message = f"{message} Recording started: {output.name}"
        except Exception as exc:
            if self.recorder.is_recording:
                self.recorder.stop()
            self._last_message = f"Start failed: {exc}"
        self.refresh(force_history=True)

    def _on_stop(self, *_args) -> None:
        try:
            output, sidecar = self._stop_session(write_sidecar=True)
            if output is None:
                self._last_message = "Session was not recording."
            else:
                self._last_message = f"Saved {output.name}; MIIL: {sidecar.name if sidecar else '-'}"
        except Exception as exc:
            self._last_message = f"Stop failed: {exc}"
        self.refresh(force_history=True)

    def _stop_session(self, *, write_sidecar: bool) -> tuple[Path | None, Path | None]:
        if self.miil.state in (MIILState.RUNNING, MIILState.PAUSED):
            self.miil.stop(capture_host_boundary())
        output = self.recorder.stop()
        sidecar = None
        if write_sidecar and output is not None and self.miil.intervals:
            sidecar = self._write_sidecar(output)
        return output, sidecar

    def _on_pause(self, *_args) -> None:
        self._last_message = self.miil.pause(capture_host_boundary())
        self.refresh()

    def _on_resume(self, *_args) -> None:
        self._last_message = self.miil.resume(capture_host_boundary())
        self.refresh()

    def _on_no_stimulus(self, *_args) -> None:
        self._last_message = self.miil.select_no_stimulus(capture_host_boundary())
        self.refresh(force_history=True)

    def _on_drop(self, *_args) -> None:
        self._last_message = self.miil.drop_current(capture_host_boundary())
        self.refresh(force_history=True)

    def _on_action(self, _sender, _app_data, code) -> None:
        self._last_message = self.miil.select_action(int(code), capture_host_boundary())
        self.refresh(force_history=True)

    def _on_add_action(self, *_args) -> None:
        if self.recorder.is_recording:
            self._last_message = "Stop the session before editing MIIL actions."
            self.refresh()
            return
        used = {action.code for action in self._editor_actions}
        code = 1
        while code in used:
            code += 1
        self._editor_actions.append(MIILAction(f"action_{code}", f"Action {code}", code))
        self._configuration_dirty = True
        self._rebuild_editor()
        self.refresh()

    def _on_remove_action(self, _sender, _app_data, index) -> None:
        if self.recorder.is_recording:
            return
        self._sync_editor_values()
        index = int(index)
        if 0 <= index < len(self._editor_actions):
            del self._editor_actions[index]
            self._configuration_dirty = True
            self._rebuild_editor()
            self.refresh()

    def _on_editor_changed(self, *_args) -> None:
        self._configuration_dirty = True
        self.refresh()

    def _on_apply_actions(self, *_args) -> None:
        if self.recorder.is_recording:
            self._last_message = "Stop the session before applying MIIL actions."
            self.refresh()
            return
        try:
            self._sync_editor_values()
            error = self.miil.configure_actions(self._editor_actions)
            if error is not None:
                self._last_message = error
                self._configuration_dirty = True
            else:
                self._editor_actions = list(self.miil.actions)
                self._configuration_dirty = False
                self._last_message = f"Applied {len(self.miil.actions)} MIIL action(s)."
                self._rebuild_action_buttons()
        except Exception as exc:
            self._last_message = f"Apply failed: {exc}"
            self._configuration_dirty = True
        self.refresh()

    def _rebuild_editor(self) -> None:
        if not dpg.does_item_exist(self.action_editor_tag):
            return
        dpg.delete_item(self.action_editor_tag, children_only=True)
        for index, action in enumerate(self._editor_actions):
            with dpg.group(horizontal=True, parent=self.action_editor_tag):
                dpg.add_input_int(
                    tag=self._editor_tag(index, "code"),
                    label="Code",
                    default_value=action.code,
                    width=95,
                    min_value=1,
                    min_clamped=True,
                    callback=self._on_editor_changed,
                )
                dpg.add_input_text(
                    tag=self._editor_tag(index, "label"),
                    label="Action",
                    default_value=action.label,
                    width=280,
                    callback=self._on_editor_changed,
                )
                dpg.add_button(
                    label="Remove", user_data=index, callback=self._on_remove_action, width=85
                )

    def _sync_editor_values(self) -> None:
        updated: list[MIILAction] = []
        for index in range(len(self._editor_actions)):
            code = int(dpg.get_value(self._editor_tag(index, "code")))
            label = str(dpg.get_value(self._editor_tag(index, "label"))).strip()
            updated.append(MIILAction(self._action_key(label, code), label, code))
        self._editor_actions = updated

    def _rebuild_action_buttons(self) -> None:
        if not dpg.does_item_exist(self.action_buttons_tag):
            return
        dpg.delete_item(self.action_buttons_tag, children_only=True)
        with dpg.group(horizontal=True, parent=self.action_buttons_tag):
            for action in self.miil.actions:
                dpg.add_button(
                    label=f"{action.label} ({action.code})",
                    user_data=action.code,
                    callback=self._on_action,
                    width=max(120, min(210, 32 + 9 * len(action.label))),
                )

    def _refresh_history(self, *, force: bool = False) -> None:
        if not dpg.does_item_exist(self.history_tag):
            return
        rows = self.miil.event_log_rows()
        signature = tuple(
            (
                row["event_index"], row["stimulus_code"], row["start_monotonic_ns"],
                row["end_monotonic_ns"], row["status"],
            )
            for row in rows
        )
        if not force and signature == self._history_signature:
            return
        self._history_signature = signature
        dpg.delete_item(self.history_tag, children_only=True)
        if not rows:
            return
        origin_ns = int(rows[0]["start_monotonic_ns"])
        for row in rows:
            start = (int(row["start_monotonic_ns"]) - origin_ns) / 1e9
            end_ns = row["end_monotonic_ns"]
            end = "..." if end_ns is None else f"{(int(end_ns) - origin_ns) / 1e9:.3f}"
            dpg.add_text(
                f"#{row['event_index']} code={row['stimulus_code']:>2} "
                f"{row['label']} | {start:.3f} -> {end} s | {row['status']}",
                parent=self.history_tag,
            )

    def _requested_output_path(self) -> Path:
        directory = str(dpg.get_value(self.directory_tag)).strip()
        filename = str(dpg.get_value(self.filename_tag)).strip()
        if not directory or not filename:
            raise ValueError("Directory and file name must not be empty.")
        return Path(directory).expanduser() / filename

    def _write_sidecar(self, output: Path) -> Path:
        sidecar = self._sidecar_path(output)
        if sidecar.exists():
            raise FileExistsError(f"MIIL sidecar already exists: {sidecar}")
        metadata = self.miil.metadata_snapshot()
        metadata["recording_output"] = str(output)
        metadata["alignment_key"] = "host_monotonic_ns"
        sidecar.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        return sidecar

    @staticmethod
    def _sidecar_path(output: Path) -> Path:
        if output.suffix:
            return output.with_suffix(".miil.json")
        return output.with_name(f"{output.name}.miil.json")

    @staticmethod
    def _action_key(label: str, code: int) -> str:
        key = re.sub(r"[^0-9a-zA-Z]+", "_", label.strip().casefold()).strip("_")
        return key or f"action_{code}"

    def _editor_tag(self, index: int, field: str) -> str:
        return f"{self.prefix}.editor.{index}.{field}"


def main() -> None:
    myo_configs = MYO_DEVICES
    requested_w2_configs = W2_DEVICES
    bwt_configs = BWT901_DEVICES
    _validate_configs(myo_configs, requested_w2_configs, bwt_configs)
    w2_configs = resolve_w2_configs(requested_w2_configs)
    if w2_configs:
        print("Resolved W2 identities:")
        for config in w2_configs:
            print(f"  {config.device_name} -> {config.device_id} @ {config.port}")

    schemas = _schemas(myo_configs, w2_configs, bwt_configs)
    series_specs = _plot_specs(myo_configs, w2_configs, bwt_configs)
    myo_devices = _resolve_myo_devices(myo_configs)

    store = RealtimeStreamStore(schemas, retention_seconds=RETENTION_SECONDS)
    recorder = SelectableStreamRecorder()
    tapped_store = StreamStoreTap(store, recorder)

    workers: dict[str, ManagedWorker] = {}
    pumps: list[QueuePump] = []
    queue_capacities: list[int] = []

    for config in myo_configs:
        records: queue.Queue[MyoRecord] = queue.Queue(maxsize=MYO_QUEUE_SIZE)
        worker_id = f"myo.{config.device_id}"
        worker = MyoWorker(
            myo_devices[config.device_id],
            records,
            connect_timeout_s=config.connect_timeout_s,
        )
        ingestor = MyoRecordIngestor(tapped_store, config.device_id)  # type: ignore[arg-type]
        workers[worker_id] = worker
        pumps.append(QueuePump(records, ingestor.ingest))
        queue_capacities.append(MYO_QUEUE_SIZE)

    for config in w2_configs:
        records: queue.Queue[W2Record] = queue.Queue(maxsize=W2_QUEUE_SIZE)
        worker_id = f"w2.{config.device_id}"
        worker = SerialW2Worker(config, records)
        ingestor = W2RecordIngestor(tapped_store, config.device_id)  # type: ignore[arg-type]
        workers[worker_id] = worker
        pumps.append(QueuePump(records, ingestor.ingest))
        queue_capacities.append(W2_QUEUE_SIZE)

    for config in bwt_configs:
        records: queue.Queue[BWT901Record] = queue.Queue(maxsize=BWT901_QUEUE_SIZE)
        worker_id = f"bwt901.{config.device_id}"
        worker = BWT901BLEWorker(config, records)
        ingestor = BWT901RecordIngestor(tapped_store, config.device_id)  # type: ignore[arg-type]
        workers[worker_id] = worker
        pumps.append(QueuePump(records, ingestor.ingest))
        queue_capacities.append(BWT901_QUEUE_SIZE)

    group = WorkerGroup(workers)
    provider = BufferedPlotProvider(store, series_specs)

    try:
        group.start()
        group.wait_ready(_startup_timeout_s(myo_configs, bwt_configs))
    except BaseException:
        try:
            group.close(SHUTDOWN_TIMEOUT_S)
        finally:
            _print_worker_summary(workers)
        raise

    _print_worker_summary(workers)

    dpg.create_context()
    close_error: BaseException | None = None
    session_panel: IntegratedSaveMIILPanel | None = None
    try:
        plot_state = create_plot_window(provider)

        with dpg.window(
            label="Save + MIIL",
            tag="assembly.random_device_plot_save_miil.window",
            width=760,
            height=770,
            pos=(980, 80),
        ):
            session_panel = IntegratedSaveMIILPanel(
                recorder,
                schemas,
                default_directory="captures",
                default_filename="multi_device_capture.h5",
            )
            session_panel.build()

        dpg.create_viewport(
            title="Random Device Plot + Save + MIIL",
            width=1760,
            height=920,
            x_pos=40,
            y_pos=40,
        )
        dpg.setup_dearpygui()
        dpg.show_viewport()

        while dpg.is_dearpygui_running():
            for pump in pumps:
                pump.drain(max_items=MAX_RECORDS_PER_PUMP_PER_FRAME)

            if group.failures():
                break

            plot_state.refresh(provider)
            session_panel.refresh()
            dpg.render_dearpygui_frame()
    finally:
        # Stop every producer, then drain records already emitted while Recorder
        # is still open.  Each queue is drained up to its full configured capacity.
        try:
            group.close(SHUTDOWN_TIMEOUT_S)
        except BaseException as exc:
            close_error = exc
        finally:
            for pump, capacity in zip(pumps, queue_capacities):
                pump.drain(max_items=capacity)
            if session_panel is not None:
                session_panel.stop_if_active()
            else:
                recorder.stop()
            dpg.destroy_context()
            _print_worker_summary(workers)

    failures = group.failures()
    if failures:
        failed_ids = ", ".join(failures)
        first_error = next(iter(failures.values()))
        raise RuntimeError(f"Acquisition failed: {failed_ids}.") from first_error
    if close_error is not None:
        raise close_error


if __name__ == "__main__":
    main()
