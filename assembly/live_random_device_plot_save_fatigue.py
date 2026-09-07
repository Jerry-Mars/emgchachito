"""Random-device Plot + Save composition for the timed fatigue paradigm.

This is deliberately a sibling of ``live_random_device_plot_save_miil.py``.
It reuses the existing device/runtime/plot/save primitives without changing the
MIIL composition.  The only new experiment semantics are the preconfigured
fatigue interval sequence, Q-triggered interval starts, automatic timed ends,
and retry-on-drop behavior.
"""

from __future__ import annotations

import json
import queue
import re
import shutil
import time
from pathlib import Path
from uuid import uuid4

import dearpygui.dearpygui as dpg

from assembly.acquisition.BLE.bwt901_ingest import BWT901RecordIngestor
from assembly.acquisition.BLE.bwt901_worker import BWT901BLEWorker, BWT901Record
from assembly.acquisition.BLE.myo_ingest import MyoRecordIngestor
from assembly.acquisition.BLE.myo_worker import MyoRecord, MyoWorker
from assembly.acquisition.runtime.queue_pump import QueuePump
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore, StreamSchema
from assembly.acquisition.runtime.worker_group import ManagedWorker, WorkerGroup
from assembly.acquisition.serial.w2_ingest import W2RecordIngestor
from assembly.acquisition.serial.w2_worker import SerialW2Worker, W2Record, resolve_w2_configs
from assembly.experiment.fatigue import (
    FatigueController,
    FatigueIntervalSpec,
    FatigueState,
    capture_host_boundary,
)
from assembly.live_random_device_plot_save_miil import (
    BWT901_DEVICES,
    BWT901_QUEUE_SIZE,
    MAX_RECORDS_PER_PUMP_PER_FRAME,
    MYO_DEVICES,
    MYO_QUEUE_SIZE,
    RETENTION_SECONDS,
    SHUTDOWN_TIMEOUT_S,
    W2_DEVICES,
    W2_QUEUE_SIZE,
    SessionState,
    _plot_specs,
    _print_worker_summary,
    _resolve_myo_devices,
    _schemas,
    _startup_timeout_s,
    _validate_configs,
)
from assembly.plot.plot_window import create_plot_window
from assembly.plot.realtime_provider import BufferedPlotProvider
from assembly.save.selectable_recorder import SelectableStreamRecorder
from assembly.save.store_tap import StreamStoreTap


WAITING_COLOR = (20, 20, 20, 255)
FATIGUE_INTERVAL_COLORS: tuple[tuple[int, int, int, int], ...] = (
    (35, 105, 185, 255),
    (30, 145, 175, 255),
    (35, 155, 110, 255),
    (105, 155, 55, 255),
    (175, 145, 35, 255),
    (205, 115, 35, 255),
    (205, 80, 45, 255),
    (190, 60, 85, 255),
    (160, 65, 145, 255),
    (115, 70, 180, 255),
)


class IntegratedSaveFatiguePanel:
    """Composition-level staged Save + timed fatigue operator/participant UI."""

    def __init__(
        self,
        recorder: SelectableStreamRecorder,
        schemas: tuple[StreamSchema, ...],
        *,
        tag_prefix: str = "assembly.random_device_plot_save_fatigue",
        default_directory: str | Path = "captures",
        default_session_name: str = "fatigue_capture",
    ) -> None:
        self.recorder = recorder
        self.schemas = schemas
        self.fatigue = FatigueController()
        self.prefix = tag_prefix
        self.session_state = SessionState.IDLE
        self._default_directory = str(default_directory)
        self._default_session_name = self._normalize_session_name(default_session_name)
        self._last_message = "Ready."
        self._plan_dirty = False
        self._editor_plan = list(self.fatigue.plan)
        self._history_signature: tuple[tuple[object, ...], ...] = ()
        self._progress_theme_tags: dict[int, str] = {}
        self._waiting_theme_tag = f"{tag_prefix}.progress_theme.waiting"
        self._last_progress_theme: str | None = None

        self._staging_root: Path | None = None
        self._staging_data_path: Path | None = None
        self._session_save_root: Path | None = None
        self._session_format: str | None = None

        self.directory_tag = f"{tag_prefix}.directory"
        self.session_name_tag = f"{tag_prefix}.session_name"
        self.format_tag = f"{tag_prefix}.format"
        self.preview_tag = f"{tag_prefix}.preview"
        self.start_tag = f"{tag_prefix}.start"
        self.stop_tag = f"{tag_prefix}.stop"
        self.save_tag = f"{tag_prefix}.save"
        self.discard_tag = f"{tag_prefix}.discard"
        self.status_tag = f"{tag_prefix}.status"
        self.rows_tag = f"{tag_prefix}.rows"
        self.path_tag = f"{tag_prefix}.path"
        self.pending_tag = f"{tag_prefix}.pending"
        self.plan_header_tag = f"{tag_prefix}.plan_header"
        self.plan_editor_tag = f"{tag_prefix}.plan_editor"
        self.add_interval_tag = f"{tag_prefix}.add_interval"
        self.apply_plan_tag = f"{tag_prefix}.apply_plan"
        self.plan_status_tag = f"{tag_prefix}.plan_status"
        self.current_tag = f"{tag_prefix}.current"
        self.next_tag = f"{tag_prefix}.next"
        self.progress_tag = f"{tag_prefix}.progress"
        self.drop_tag = f"{tag_prefix}.drop"
        self.latest_history_tag = f"{tag_prefix}.latest_history"
        self.history_tag = f"{tag_prefix}.history"
        self.discard_modal_tag = f"{tag_prefix}.discard_modal"
        self.keyboard_handler_tag = f"{tag_prefix}.keyboard_handlers"

    def build(self) -> None:
        dpg.add_text("Recording Session + Timed Fatigue Interval Sequence")
        dpg.add_text("Session Start begins recording in No Stimulus. Press Q to start each planned interval.")
        dpg.add_text("Intervals end automatically; between intervals the display is black and waits for Q.")

        dpg.add_separator()
        dpg.add_text("Session Output")
        dpg.add_input_text(
            label="Save root", tag=self.directory_tag, default_value=self._default_directory, width=500
        )
        dpg.add_input_text(
            label="Session name",
            tag=self.session_name_tag,
            default_value=self._default_session_name,
            width=500,
        )
        dpg.add_combo(("HDF5", "CSV"), label="Format", tag=self.format_tag, default_value="HDF5", width=160)
        dpg.add_text("", tag=self.preview_tag, wrap=720)
        with dpg.group(horizontal=True):
            dpg.add_button(label="Start Session", tag=self.start_tag, callback=self._on_start, width=145)
            dpg.add_button(label="Stop Session", tag=self.stop_tag, callback=self._on_stop, width=145)
        with dpg.group(horizontal=True):
            dpg.add_button(label="Save Session", tag=self.save_tag, callback=self._on_save, width=145)
            dpg.add_button(label="Discard Session", tag=self.discard_tag, callback=self._on_discard, width=145)
        dpg.add_text("", tag=self.status_tag)
        dpg.add_text("", tag=self.rows_tag)
        dpg.add_text("", tag=self.path_tag, wrap=720)
        dpg.add_text("", tag=self.pending_tag, wrap=720)

        dpg.add_separator()
        with dpg.collapsing_header(
            label="Fatigue Interval Plan",
            tag=self.plan_header_tag,
            default_open=True,
        ):
            dpg.add_text("Configure ordered intervals before Start: action label, duration, and stimulus code.")
            with dpg.child_window(tag=self.plan_editor_tag, width=-1, height=190, border=True):
                pass
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Add Interval", tag=self.add_interval_tag, callback=self._on_add_interval, width=120
                )
                dpg.add_button(
                    label="Apply Plan", tag=self.apply_plan_tag, callback=self._on_apply_plan, width=120
                )
            dpg.add_text("", tag=self.plan_status_tag)

        dpg.add_separator()
        dpg.add_text("Fatigue Participant Console")
        dpg.add_text("Q = start pending interval | Drop = invalidate current/recent interval and retry")
        dpg.add_text("", tag=self.current_tag)
        dpg.add_text("", tag=self.next_tag)
        dpg.add_progress_bar(default_value=0.0, tag=self.progress_tag, width=-1, overlay="WAITING - PRESS Q")
        dpg.add_button(
            label="Drop Current / Previous Interval (-1)",
            tag=self.drop_tag,
            callback=self._on_drop,
            width=270,
        )

        dpg.add_text("Interval History")
        dpg.add_text("Latest Event: -", tag=self.latest_history_tag)
        with dpg.child_window(tag=self.history_tag, width=-1, height=175, horizontal_scrollbar=True):
            pass

        with dpg.window(
            label="Confirm Discard",
            tag=self.discard_modal_tag,
            modal=True,
            show=False,
            no_resize=True,
            width=430,
            height=145,
        ):
            dpg.add_text("Delete this pending recording from staging? This cannot be undone.")
            with dpg.group(horizontal=True):
                dpg.add_button(label="Confirm Discard", callback=self._confirm_discard, width=150)
                dpg.add_button(
                    label="Cancel",
                    callback=lambda *_: dpg.configure_item(self.discard_modal_tag, show=False),
                    width=100,
                )

        self._build_progress_themes()
        self._build_keyboard_handlers()
        self._rebuild_editor()
        self.refresh(force_history=True)

    def refresh(self, *, force_history: bool = False) -> None:
        if not dpg.does_item_exist(self.status_tag):
            return

        if self.session_state is SessionState.RECORDING:
            update_message = self.fatigue.update(capture_host_boundary())
            if update_message:
                self._last_message = update_message
                force_history = True

        dpg.set_value(
            self.status_tag,
            f"Session: {self.session_state.value.upper()} | Recorder: {self.recorder.state.value.upper()} | "
            f"Fatigue: {self.fatigue.state.value.upper()} | {self._last_message}",
        )
        dpg.set_value(self.rows_tag, f"Rows written: {self.recorder.rows_written}")
        dpg.set_value(self.preview_tag, self._output_preview_text())

        if self.session_state is SessionState.RECORDING:
            output_text = "Staging output: -" if self._staging_data_path is None else f"Staging output: {self._staging_data_path}"
        elif self.session_state is SessionState.PENDING_SAVE:
            output_text = "Pending staging: -" if self._staging_root is None else f"Pending staging: {self._staging_root}"
        else:
            output_text = "Staging output: -"
        dpg.set_value(self.path_tag, output_text)

        pending_text = ""
        if self.session_state is SessionState.PENDING_SAVE:
            pending_text = (
                f"Pending confirmation: {self.recorder.rows_written} rows, "
                f"{len(self.fatigue.segments)} fatigue timeline segment(s). "
                "Choose Save Session or Discard Session."
            )
        dpg.set_value(self.pending_tag, pending_text)
        dpg.set_value(
            self.plan_status_tag,
            "Interval plan has unapplied edits." if self._plan_dirty else f"Plan applied: {len(self.fatigue.plan)} interval(s).",
        )

        self._refresh_participant_console()
        self._refresh_control_states()
        self._refresh_history(force=force_history)

    def shutdown_preserving_staging(self) -> None:
        if self.session_state is SessionState.RECORDING:
            try:
                self._stop_recording_to_pending()
                print(f"Pending fatigue recording preserved in staging: {self._staging_root}")
            except Exception as exc:
                print(f"Failed to finalize pending fatigue recording during shutdown: {exc}")
        elif self.fatigue.state in {FatigueState.WAITING, FatigueState.RUNNING, FatigueState.COMPLETE}:
            self.fatigue.stop(capture_host_boundary())

    # ------------------------------------------------------------------
    # Session lifecycle / staged save
    # ------------------------------------------------------------------

    def _on_start(self, *_args) -> None:
        if self.session_state is not SessionState.IDLE:
            self._last_message = "Resolve the current session before starting another."
            self.refresh()
            return
        if self._plan_dirty:
            self._last_message = "Apply edited fatigue intervals before Start."
            self.refresh()
            return

        staging_root: Path | None = None
        try:
            save_root = self._save_root_from_window()
            session_format = self._selected_format_from_window()
            self._session_name_from_window()
            save_root.mkdir(parents=True, exist_ok=True)
            staging_parent = save_root / ".staging"
            staging_parent.mkdir(parents=True, exist_ok=True)
            staging_root = staging_parent / uuid4().hex
            staging_root.mkdir(parents=False, exist_ok=False)

            self.recorder.set_format(session_format)  # type: ignore[arg-type]
            requested = staging_root / ("data.h5" if session_format == "hdf5" else "data")
            output = self.recorder.start(requested, self.schemas)

            self._staging_root = staging_root
            self._staging_data_path = output
            self._session_save_root = save_root
            self._session_format = session_format
            self.session_state = SessionState.RECORDING
            self._last_message = self.fatigue.start(capture_host_boundary())
            self._set_plan_open(False)
        except Exception as exc:
            if self.recorder.is_recording:
                self.recorder.stop()
            if staging_root is not None and staging_root.exists():
                shutil.rmtree(staging_root, ignore_errors=True)
            self._clear_staging_state()
            self.session_state = SessionState.IDLE
            self._last_message = f"Start failed: {exc}"
        self.refresh(force_history=True)

    def _on_stop(self, *_args) -> None:
        if self.session_state is not SessionState.RECORDING:
            self._last_message = "No recording session is active."
            self.refresh()
            return
        try:
            self._stop_recording_to_pending()
            self._last_message = "Session stopped. Review it, then Save or Discard."
        except Exception as exc:
            self._last_message = f"Stop failed: {exc}"
        self.refresh(force_history=True)

    def _stop_recording_to_pending(self) -> None:
        if self.fatigue.state in {FatigueState.WAITING, FatigueState.RUNNING, FatigueState.COMPLETE}:
            self.fatigue.stop(capture_host_boundary())
        output = self.recorder.stop()
        if output is not None:
            self._staging_data_path = output
        self.session_state = SessionState.PENDING_SAVE
        self._write_staging_sidecar(final_output=None)

    def _on_save(self, *_args) -> None:
        if self.session_state is not SessionState.PENDING_SAVE:
            self._last_message = "No pending recording to save."
            self.refresh()
            return
        try:
            final_output = self._commit_pending_session()
            self._last_message = f"Session saved: {final_output}"
        except Exception as exc:
            self._last_message = f"Save failed: {exc}"
        self.refresh(force_history=True)

    def _commit_pending_session(self) -> Path:
        staging_root = self._require_staging_root()
        staging_data = self._require_staging_data_path()
        save_root = self._require_session_save_root()
        session_format = self._require_session_format()
        session_name = self._session_name_from_window()

        if session_format == "hdf5":
            final_data = save_root / f"{session_name}.h5"
            final_sidecar = save_root / f"{session_name}.fatigue.json"
            if final_data.exists() or final_sidecar.exists():
                raise FileExistsError(
                    f"Final HDF5 fatigue session already exists: {final_data} or {final_sidecar}"
                )
            staging_sidecar = self._write_staging_sidecar(final_output=final_data)
            moved_data = False
            try:
                staging_data.replace(final_data)
                moved_data = True
                staging_sidecar.replace(final_sidecar)
            except BaseException:
                if moved_data and final_data.exists() and not staging_data.exists():
                    try:
                        final_data.replace(staging_data)
                    except OSError:
                        pass
                raise
            self._remove_empty_staging_root(staging_root)
            final_output = final_data
        else:
            final_output = save_root / session_name
            if final_output.exists():
                raise FileExistsError(f"Final CSV fatigue session directory already exists: {final_output}")
            self._write_staging_sidecar(final_output=final_output)
            staging_data.replace(final_output)
            self._remove_empty_staging_root(staging_root)

        self.session_state = SessionState.IDLE
        self._clear_staging_state()
        return final_output

    def _on_discard(self, *_args) -> None:
        if self.session_state is not SessionState.PENDING_SAVE:
            self._last_message = "No pending recording to discard."
            self.refresh()
            return
        dpg.configure_item(self.discard_modal_tag, show=True)

    def _confirm_discard(self, *_args) -> None:
        dpg.configure_item(self.discard_modal_tag, show=False)
        try:
            staging_root = self._require_staging_root()
            if staging_root.exists():
                shutil.rmtree(staging_root)
            self._cleanup_staging_parent(staging_root.parent)
            self.session_state = SessionState.IDLE
            self._clear_staging_state()
            self.fatigue.reset_timeline()
            self._history_signature = ()
            self._last_message = "Pending recording discarded."
        except Exception as exc:
            self._last_message = f"Discard failed: {exc}"
        self.refresh(force_history=True)

    # ------------------------------------------------------------------
    # Fatigue plan + runtime controls
    # ------------------------------------------------------------------

    def _on_q(self, *_args) -> None:
        if self.session_state is not SessionState.RECORDING:
            return
        if self._keyboard_shortcut_blocked():
            return
        if self.fatigue.state is FatigueState.WAITING:
            self._last_message = self.fatigue.start_next(capture_host_boundary())
            self.refresh(force_history=True)

    def _on_drop(self, *_args) -> None:
        if self.session_state is SessionState.RECORDING:
            self._last_message = self.fatigue.drop(capture_host_boundary())
        self.refresh(force_history=True)

    def _on_add_interval(self, *_args) -> None:
        if self.session_state is not SessionState.IDLE:
            return
        self._sync_editor_values()
        used_codes = {item.code for item in self._editor_plan}
        code = 1
        while code in used_codes:
            code += 1
        self._editor_plan.append(FatigueIntervalSpec(f"action_{code}", f"Action {code}", code, 10.0))
        self._plan_dirty = True
        self._rebuild_editor()
        self.refresh()

    def _on_remove_interval(self, _sender, _app_data, index) -> None:
        if self.session_state is not SessionState.IDLE:
            return
        self._sync_editor_values()
        index = int(index)
        if 0 <= index < len(self._editor_plan):
            del self._editor_plan[index]
            self._plan_dirty = True
            self._rebuild_editor()
        self.refresh()

    def _on_editor_changed(self, *_args) -> None:
        if self.session_state is SessionState.IDLE:
            self._plan_dirty = True
        self.refresh()

    def _on_apply_plan(self, *_args) -> None:
        if self.session_state is not SessionState.IDLE:
            self._last_message = "Resolve the current session before applying the fatigue plan."
            self.refresh()
            return
        try:
            self._sync_editor_values()
            error = self.fatigue.configure_plan(self._editor_plan)
            if error is not None:
                self._last_message = error
                self._plan_dirty = True
            else:
                self._editor_plan = list(self.fatigue.plan)
                self._plan_dirty = False
                self._last_message = f"Applied {len(self.fatigue.plan)} fatigue interval(s)."
                self._build_progress_themes()
        except Exception as exc:
            self._last_message = f"Apply failed: {exc}"
            self._plan_dirty = True
        self.refresh()

    def _rebuild_editor(self) -> None:
        if not dpg.does_item_exist(self.plan_editor_tag):
            return
        dpg.delete_item(self.plan_editor_tag, children_only=True)
        for index, interval in enumerate(self._editor_plan):
            with dpg.group(horizontal=True, parent=self.plan_editor_tag):
                dpg.add_text(f"{index + 1:02d}", color=(160, 170, 185, 255))
                dpg.add_input_int(
                    tag=self._editor_tag(index, "code"),
                    label="Code",
                    default_value=interval.code,
                    width=85,
                    min_value=1,
                    min_clamped=True,
                    callback=self._on_editor_changed,
                )
                dpg.add_input_text(
                    tag=self._editor_tag(index, "label"),
                    label="Action",
                    default_value=interval.label,
                    width=240,
                    callback=self._on_editor_changed,
                )
                dpg.add_input_float(
                    tag=self._editor_tag(index, "duration"),
                    label="Duration (s)",
                    default_value=interval.duration_s,
                    width=120,
                    min_value=0.01,
                    min_clamped=True,
                    format="%.2f",
                    callback=self._on_editor_changed,
                )
                dpg.add_button(
                    label="Remove",
                    tag=self._editor_tag(index, "remove"),
                    user_data=index,
                    callback=self._on_remove_interval,
                    width=80,
                )

    def _sync_editor_values(self) -> None:
        updated: list[FatigueIntervalSpec] = []
        for index in range(len(self._editor_plan)):
            code = int(dpg.get_value(self._editor_tag(index, "code")))
            label = str(dpg.get_value(self._editor_tag(index, "label"))).strip()
            duration = float(dpg.get_value(self._editor_tag(index, "duration")))
            updated.append(FatigueIntervalSpec(self._action_key(label, code), label, code, duration))
        self._editor_plan = updated

    def _refresh_participant_console(self) -> None:
        now_ns = time.perf_counter_ns()
        state = self.fatigue.state
        if state is FatigueState.RUNNING:
            segment = self.fatigue.current_segment
            assert segment is not None and segment.planned_duration_s is not None
            elapsed = self.fatigue.current_elapsed_s(now_ns)
            fraction = self.fatigue.progress_fraction(now_ns)
            number = 0 if segment.interval_index is None else segment.interval_index + 1
            dpg.set_value(
                self.current_tag,
                f"ACTIVE {number}/{len(self.fatigue.plan)} | {segment.label} | code {segment.effective_code}",
            )
            dpg.set_value(self.next_tag, f"Attempt {segment.attempt} | {elapsed:.1f} / {segment.planned_duration_s:.1f} s")
            dpg.set_value(self.progress_tag, fraction)
            dpg.configure_item(
                self.progress_tag,
                overlay=f"{segment.label.upper()}   {elapsed:.1f} / {segment.planned_duration_s:.1f} s",
            )
            if segment.interval_index is not None:
                self._bind_progress_theme(self._progress_theme_tags.get(segment.interval_index, self._waiting_theme_tag))
            return

        dpg.set_value(self.progress_tag, 0.0)
        self._bind_progress_theme(self._waiting_theme_tag)
        if state is FatigueState.WAITING:
            interval = self.fatigue.next_interval
            number = self.fatigue.current_interval_index
            dpg.set_value(self.current_tag, "NO STIMULUS / WAITING")
            if interval is None or number is None:
                dpg.set_value(self.next_tag, "Press Q when ready.")
                dpg.configure_item(self.progress_tag, overlay="WAITING - PRESS Q")
            else:
                dpg.set_value(
                    self.next_tag,
                    f"Next {number + 1}/{len(self.fatigue.plan)}: {interval.label} | "
                    f"code {interval.code} | {interval.duration_s:g} s | PRESS Q",
                )
                dpg.configure_item(self.progress_tag, overlay=f"WAITING FOR {interval.label.upper()} - PRESS Q")
        elif state is FatigueState.COMPLETE:
            dpg.set_value(self.current_tag, "PROTOCOL COMPLETE")
            dpg.set_value(self.next_tag, "All planned intervals completed. Stop Session when ready.")
            dpg.configure_item(self.progress_tag, overlay="PROTOCOL COMPLETE")
        else:
            dpg.set_value(self.current_tag, "NO STIMULUS")
            dpg.set_value(self.next_tag, "Start Session to begin recording.")
            dpg.configure_item(self.progress_tag, overlay="NOT RUNNING")

    def _refresh_control_states(self) -> None:
        idle = self.session_state is SessionState.IDLE
        recording = self.session_state is SessionState.RECORDING
        pending = self.session_state is SessionState.PENDING_SAVE
        dpg.configure_item(self.directory_tag, enabled=idle)
        dpg.configure_item(self.format_tag, enabled=idle)
        dpg.configure_item(self.session_name_tag, enabled=idle or pending)
        dpg.configure_item(self.start_tag, enabled=idle)
        dpg.configure_item(self.stop_tag, enabled=recording)
        dpg.configure_item(self.save_tag, enabled=pending)
        dpg.configure_item(self.discard_tag, enabled=pending)
        dpg.configure_item(self.add_interval_tag, enabled=idle)
        dpg.configure_item(self.apply_plan_tag, enabled=idle)
        for index in range(len(self._editor_plan)):
            for field in ("code", "label", "duration", "remove"):
                tag = self._editor_tag(index, field)
                if dpg.does_item_exist(tag):
                    dpg.configure_item(tag, enabled=idle)
        dpg.configure_item(
            self.drop_tag,
            enabled=recording and self.fatigue.state in {FatigueState.RUNNING, FatigueState.WAITING, FatigueState.COMPLETE},
        )

    def _refresh_history(self, *, force: bool = False) -> None:
        rows = self.fatigue.event_log_rows()
        signature = tuple(
            (
                row["event_index"],
                row["stimulus_code"],
                row["start_monotonic_ns"],
                row["end_monotonic_ns"],
                row["status"],
            )
            for row in rows
        )
        if not force and signature == self._history_signature:
            return
        self._history_signature = signature
        dpg.delete_item(self.history_tag, children_only=True)
        if not rows:
            dpg.set_value(self.latest_history_tag, "Latest Event: -")
            return
        origin_ns = int(rows[0]["start_monotonic_ns"])
        for row in rows:
            start = (int(row["start_monotonic_ns"]) - origin_ns) / 1e9
            end_ns = row["end_monotonic_ns"]
            end = "..." if end_ns is None else f"{(int(end_ns) - origin_ns) / 1e9:.3f}"
            interval = "wait" if row["interval_number"] is None else f"I{row['interval_number']}/A{row['attempt']}"
            dpg.add_text(
                f"#{row['event_index']} {interval} code={row['stimulus_code']:>2} "
                f"{row['label']} | {start:.3f} -> {end} s | {row['status']}",
                parent=self.history_tag,
            )
        latest = rows[-1]
        dpg.set_value(
            self.latest_history_tag,
            f"Latest Event: #{latest['event_index']} | code {latest['stimulus_code']} | "
            f"{latest['label']} | {latest['status']}",
        )
        try:
            dpg.set_y_scroll(self.history_tag, dpg.get_y_scroll_max(self.history_tag))
        except (RuntimeError, SystemError):
            pass

    # ------------------------------------------------------------------
    # Keyboard / progress themes
    # ------------------------------------------------------------------

    def _build_keyboard_handlers(self) -> None:
        if dpg.does_item_exist(self.keyboard_handler_tag):
            dpg.delete_item(self.keyboard_handler_tag)
        with dpg.handler_registry(tag=self.keyboard_handler_tag):
            dpg.add_key_press_handler(key=dpg.mvKey_Q, callback=self._on_q)

    @staticmethod
    def _keyboard_shortcut_blocked() -> bool:
        for modifier_names in (
            ("mvKey_Control", "mvKey_LControl", "mvKey_RControl"),
            ("mvKey_Shift", "mvKey_LShift", "mvKey_RShift"),
            ("mvKey_LAlt", "mvKey_RAlt"),
        ):
            for name in modifier_names:
                key = getattr(dpg, name, None)
                if key is not None and dpg.is_key_down(key):
                    return True
        try:
            focused = dpg.get_focused_item()
        except (RuntimeError, SystemError):
            return False
        if not focused:
            return False
        try:
            item_type = str(dpg.get_item_type(focused))
        except (RuntimeError, SystemError):
            return False
        return any(token in item_type for token in ("mvInput", "mvDrag", "mvSlider", "mvCombo"))

    def _build_progress_themes(self) -> None:
        # DearPyGui does not allow deleting a theme while it is still bound to an
        # item.  The plan may be reapplied while idle, so explicitly unbind the
        # progress bar before rebuilding its interval themes.
        if dpg.does_item_exist(self.progress_tag):
            dpg.bind_item_theme(self.progress_tag, 0)
        self._last_progress_theme = None
        for tag in (*self._progress_theme_tags.values(), self._waiting_theme_tag):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        self._progress_theme_tags = {}
        self._create_progress_theme(self._waiting_theme_tag, WAITING_COLOR)
        for index, _interval in enumerate(self.fatigue.plan):
            tag = f"{self.prefix}.progress_theme.interval.{index}"
            color = FATIGUE_INTERVAL_COLORS[index % len(FATIGUE_INTERVAL_COLORS)]
            self._create_progress_theme(tag, color)
            self._progress_theme_tags[index] = tag
        self._last_progress_theme = None

    @staticmethod
    def _create_progress_theme(tag: str, color: tuple[int, int, int, int]) -> None:
        with dpg.theme(tag=tag):
            with dpg.theme_component(dpg.mvProgressBar):
                dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, color)
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (12, 12, 12, 255))

    def _bind_progress_theme(self, theme_tag: str) -> None:
        if theme_tag == self._last_progress_theme:
            return
        dpg.bind_item_theme(self.progress_tag, theme_tag)
        self._last_progress_theme = theme_tag

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _write_staging_sidecar(self, *, final_output: Path | None) -> Path:
        staging_data = self._require_staging_data_path()
        session_format = self._require_session_format()
        sidecar = staging_data / "fatigue.json" if session_format == "csv" else self._require_staging_root() / "fatigue.json"
        metadata = self.fatigue.metadata_snapshot()
        metadata["alignment_key"] = "host_monotonic_ns"
        metadata["recording_format"] = session_format
        metadata["session_status"] = "saved" if final_output is not None else "pending_save"
        metadata["recording_output"] = None if final_output is None else str(final_output)
        sidecar.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        return sidecar

    def _output_preview_text(self) -> str:
        try:
            save_root = (
                self._session_save_root
                if self.session_state is not SessionState.IDLE and self._session_save_root is not None
                else self._save_root_from_window()
            )
            session_format = (
                self._session_format
                if self.session_state is not SessionState.IDLE and self._session_format is not None
                else self._selected_format_from_window()
            )
            name = self._session_name_from_window()
        except Exception as exc:
            return f"Output preview: invalid configuration ({exc})"
        if session_format == "hdf5":
            return (
                "Output preview (HDF5):\n"
                f"  {save_root / (name + '.h5')}\n"
                f"  {save_root / (name + '.fatigue.json')}"
            )
        return (
            "Output preview (CSV session directory):\n"
            f"  {save_root / name}/\n"
            "    metadata.json\n"
            "    fatigue.json\n"
            "    streams/*.csv"
        )

    def _save_root_from_window(self) -> Path:
        directory = str(dpg.get_value(self.directory_tag)).strip()
        if not directory:
            raise ValueError("Save root must not be empty.")
        return Path(directory).expanduser()

    def _session_name_from_window(self) -> str:
        return self._normalize_session_name(str(dpg.get_value(self.session_name_tag)))

    @staticmethod
    def _normalize_session_name(value: str) -> str:
        name = Path(str(value).strip()).name
        for suffix in (".hdf5", ".h5", ".csv"):
            if name.casefold().endswith(suffix):
                name = name[: -len(suffix)]
                break
        name = name.strip().strip(".")
        if not name:
            raise ValueError("Session name must not be empty after removing a format suffix.")
        return name

    def _selected_format_from_window(self) -> str:
        selected = str(dpg.get_value(self.format_tag)).strip().casefold()
        if selected == "hdf5":
            return "hdf5"
        if selected == "csv":
            return "csv"
        raise ValueError(f"Unsupported save format: {selected!r}")

    def _set_plan_open(self, is_open: bool) -> None:
        if dpg.does_item_exist(self.plan_header_tag):
            try:
                dpg.set_value(self.plan_header_tag, bool(is_open))
            except (RuntimeError, SystemError):
                pass

    def _require_staging_root(self) -> Path:
        if self._staging_root is None:
            raise RuntimeError("Session has no staging root.")
        return self._staging_root

    def _require_staging_data_path(self) -> Path:
        if self._staging_data_path is None:
            raise RuntimeError("Session has no staging data path.")
        return self._staging_data_path

    def _require_session_save_root(self) -> Path:
        if self._session_save_root is None:
            raise RuntimeError("Session has no frozen save root.")
        return self._session_save_root

    def _require_session_format(self) -> str:
        if self._session_format not in {"hdf5", "csv"}:
            raise RuntimeError("Session has no frozen save format.")
        return self._session_format

    def _clear_staging_state(self) -> None:
        self._staging_root = None
        self._staging_data_path = None
        self._session_save_root = None
        self._session_format = None

    def _remove_empty_staging_root(self, staging_root: Path) -> None:
        if staging_root.exists():
            try:
                staging_root.rmdir()
            except OSError:
                return
        self._cleanup_staging_parent(staging_root.parent)

    @staticmethod
    def _cleanup_staging_parent(staging_parent: Path) -> None:
        try:
            staging_parent.rmdir()
        except OSError:
            pass

    @staticmethod
    def _action_key(label: str, code: int) -> str:
        key = re.sub(r"[^0-9a-zA-Z]+", "_", label.strip().casefold()).strip("_")
        return key or f"action_{code}"

    def _editor_tag(self, index: int, field: str) -> str:
        return f"{self.prefix}.editor.{index}.{field}"


# ----------------------------------------------------------------------
# Existing acquisition/plot composition, with only the experiment panel swapped.
# ----------------------------------------------------------------------


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
            myo_devices[config.device_id], records, connect_timeout_s=config.connect_timeout_s
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
    session_panel: IntegratedSaveFatiguePanel | None = None
    try:
        plot_state = create_plot_window(provider)
        with dpg.window(
            label="Save + Fatigue",
            tag="assembly.random_device_plot_save_fatigue.window",
            width=760,
            height=900,
            pos=(980, 40),
        ):
            session_panel = IntegratedSaveFatiguePanel(
                recorder,
                schemas,
                default_directory="captures",
                default_session_name="fatigue_capture",
            )
            session_panel.build()

        device_failure_alert_tag = "assembly.random_device_plot_save_fatigue.device_failure_alert"
        device_failure_text_tag = "assembly.random_device_plot_save_fatigue.device_failure_text"
        with dpg.window(
            label="Device Offline",
            tag=device_failure_alert_tag,
            modal=True,
            show=False,
            no_resize=True,
            width=520,
            height=175,
        ):
            dpg.add_text("An acquisition device has gone offline.")
            dpg.add_text("", tag=device_failure_text_tag, wrap=480)
            dpg.add_button(
                label="Acknowledge",
                callback=lambda *_: dpg.configure_item(device_failure_alert_tag, show=False),
                width=120,
            )

        dpg.create_viewport(
            title="Random Device Plot + Save + Fatigue",
            width=1760,
            height=960,
            x_pos=40,
            y_pos=40,
        )
        dpg.setup_dearpygui()
        dpg.show_viewport()

        notified_failures: set[str] = set()
        while dpg.is_dearpygui_running():
            for pump in pumps:
                pump.drain(max_items=MAX_RECORDS_PER_PUMP_PER_FRAME)

            failures = group.failures()
            new_failures = tuple(worker_id for worker_id in failures if worker_id not in notified_failures)
            if new_failures:
                notified_failures.update(new_failures)
                dpg.set_value(
                    device_failure_text_tag,
                    "\n".join(f"{worker_id}: {failures[worker_id]}" for worker_id in new_failures),
                )
                dpg.configure_item(device_failure_alert_tag, show=True)

            plot_state.refresh(provider)
            session_panel.refresh()
            dpg.render_dearpygui_frame()
    finally:
        try:
            group.close(SHUTDOWN_TIMEOUT_S)
        except BaseException as exc:
            close_error = exc
        finally:
            for pump, capacity in zip(pumps, queue_capacities):
                pump.drain(max_items=capacity)
            if session_panel is not None:
                session_panel.shutdown_preserving_staging()
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
