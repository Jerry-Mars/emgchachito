"""Random-device Plot + Save composition for term-based fatigue evaluation.

This is a standalone experiment composition. It owns its device selection,
fatigue-evaluation UI, and experiment sidecar while reusing existing assembly
workers, ingestors, realtime store, plot provider, recorder, and small device
composition helpers.

Keyboard semantics:
- Q: record one action event inside the currently running term.
- T: end any currently running term immediately.

Timed terms also end automatically at their exact planned boundary. Between
terms the experiment remains in no_stimulus so the participant can provide a
CR10 rating and the operator can configure the next term or finish.
"""

from __future__ import annotations

import json
import queue
import shutil
import time
from pathlib import Path
from uuid import uuid4

import dearpygui.dearpygui as dpg

from assembly.acquisition.BLE.bwt901_ingest import BWT901RecordIngestor
from assembly.acquisition.BLE.bwt901_worker import BWT901BLEConfig, BWT901BLEWorker, BWT901Record
from assembly.acquisition.BLE.myo_ingest import MyoRecordIngestor
from assembly.acquisition.BLE.myo_worker import MyoRecord, MyoWorker
from assembly.acquisition.runtime.queue_pump import QueuePump
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore, StreamSchema
from assembly.acquisition.runtime.worker_group import ManagedWorker, WorkerGroup
from assembly.acquisition.serial.w2_ingest import W2RecordIngestor
from assembly.acquisition.serial.w2_worker import SerialW2Worker, W2Record, W2SerialConfig, resolve_w2_configs
from assembly.experiment.fatigue_evaluation import (
    CR10_REFERENCE,
    FatigueEvaluationController,
    FatigueEvaluationSegment,
    FatigueEvaluationState,
    FatigueEvaluationTermSpec,
    capture_host_boundary,
)
from assembly.live_random_device_plot_save_miil import (
    BWT901_QUEUE_SIZE,
    MAX_RECORDS_PER_PUMP_PER_FRAME,
    MYO_QUEUE_SIZE,
    RETENTION_SECONDS,
    SHUTDOWN_TIMEOUT_S,
    W2_QUEUE_SIZE,
    MyoDeviceConfig,
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


# ======================================================================
# HARDWARE CONFIGURATION
# ======================================================================
# Each tuple may contain zero, one, or many devices. This fatigue-evaluation
# composition owns its device selection; changing it does not affect MIIL or the
# timed-fatigue composition.

MYO_DEVICES: tuple[MyoDeviceConfig, ...] = (
    # MyoDeviceConfig("left_arm", "AA:BB:CC:DD:EE:FF"),
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


DEFAULT_TERM_NAME = "Fatigue Term"
DEFAULT_TERM_DURATION_S = 60.0
WAITING_COLOR = (18, 18, 18, 255)
RUNNING_COLOR = (35, 125, 195, 255)


class IntegratedSaveFatigueEvaluationPanel:
    """Staged Save + dynamic term configuration + post-term CR10 evaluation."""

    def __init__(
        self,
        recorder: SelectableStreamRecorder,
        schemas: tuple[StreamSchema, ...],
        *,
        tag_prefix: str = "assembly.random_device_plot_save_fatigue_evaluation",
        default_directory: str | Path = "captures",
        default_session_name: str = "fatigue_evaluation_capture",
    ) -> None:
        self.recorder = recorder
        self.schemas = schemas
        self.evaluation = FatigueEvaluationController()
        self.prefix = tag_prefix
        self.session_state = SessionState.IDLE
        self._default_directory = str(default_directory)
        self._default_session_name = self._normalize_session_name(default_session_name)
        self._last_message = "Ready."
        self._history_signature: tuple[tuple[object, ...], ...] = ()
        self._term_overview_signature: tuple[tuple[object, ...], ...] = ()
        self._prepared_term_number: int | None = None
        self._last_progress_theme: str | None = None

        self._staging_root: Path | None = None
        self._staging_data_path: Path | None = None
        self._session_save_root: Path | None = None
        self._session_format: str | None = None

        self.directory_tag = f"{tag_prefix}.directory"
        self.session_name_tag = f"{tag_prefix}.session_name"
        self.format_tag = f"{tag_prefix}.format"
        self.preview_tag = f"{tag_prefix}.preview"
        self.start_session_tag = f"{tag_prefix}.start_session"
        self.stop_session_tag = f"{tag_prefix}.stop_session"
        self.save_tag = f"{tag_prefix}.save"
        self.discard_tag = f"{tag_prefix}.discard"
        self.status_tag = f"{tag_prefix}.status"
        self.overview_tag = f"{tag_prefix}.overview"
        self.rows_tag = f"{tag_prefix}.rows"
        self.path_tag = f"{tag_prefix}.path"
        self.pending_tag = f"{tag_prefix}.pending"
        self.session_output_header_tag = f"{tag_prefix}.session_output_header"

        self.term_header_tag = f"{tag_prefix}.term_header"
        self.term_number_tag = f"{tag_prefix}.term_number"
        self.term_name_tag = f"{tag_prefix}.term_name"
        self.use_duration_tag = f"{tag_prefix}.use_duration"
        self.term_duration_tag = f"{tag_prefix}.term_duration"
        self.start_term_tag = f"{tag_prefix}.start_term"
        self.finish_protocol_tag = f"{tag_prefix}.finish_protocol"

        self.current_tag = f"{tag_prefix}.current"
        self.elapsed_tag = f"{tag_prefix}.elapsed"
        self.action_count_tag = f"{tag_prefix}.action_count"
        self.action_distribution_tag = f"{tag_prefix}.action_distribution"
        self.progress_tag = f"{tag_prefix}.progress"
        self.drop_tag = f"{tag_prefix}.drop"

        self.rating_header_tag = f"{tag_prefix}.rating_header"
        self.rating_summary_tag = f"{tag_prefix}.rating_summary"
        self.rating_buttons_tag = f"{tag_prefix}.rating_buttons"
        self.term_overview_header_tag = f"{tag_prefix}.term_overview_header"
        self.term_overview_tag = f"{tag_prefix}.term_overview"
        self.detailed_history_header_tag = f"{tag_prefix}.detailed_history_header"
        self.latest_history_tag = f"{tag_prefix}.latest_history"
        self.history_tag = f"{tag_prefix}.history"
        self.discard_modal_tag = f"{tag_prefix}.discard_modal"
        self.keyboard_handler_tag = f"{tag_prefix}.keyboard_handlers"
        self.waiting_theme_tag = f"{tag_prefix}.progress_theme.waiting"
        self.running_theme_tag = f"{tag_prefix}.progress_theme.running"

    def build(self) -> None:
        dpg.add_text("FATIGUE EVALUATION")
        with dpg.group(horizontal=True):
            dpg.add_button(label="Start Session", tag=self.start_session_tag, callback=self._on_start_session, width=145)
            dpg.add_button(label="Stop Session", tag=self.stop_session_tag, callback=self._on_stop_session, width=145)
            dpg.add_button(label="Save Session", tag=self.save_tag, callback=self._on_save, width=145)
            dpg.add_button(label="Discard Session", tag=self.discard_tag, callback=self._on_discard, width=145)
        dpg.add_text("", tag=self.status_tag)
        dpg.add_text("", tag=self.overview_tag)

        dpg.add_separator()
        dpg.add_text("CURRENT TERM")
        dpg.add_text("", tag=self.current_tag)
        dpg.add_progress_bar(default_value=0.0, tag=self.progress_tag, width=-1, overlay="NO STIMULUS")
        dpg.add_text("", tag=self.elapsed_tag)
        dpg.add_text("", tag=self.action_count_tag)
        dpg.add_text("", tag=self.action_distribution_tag)
        dpg.add_button(label="Drop Term (-1)", tag=self.drop_tag, callback=self._on_drop, width=145)

        with dpg.collapsing_header(label="Next Term", tag=self.term_header_tag, default_open=True):
            dpg.add_text("Term: 1", tag=self.term_number_tag)
            dpg.add_input_text(label="Term name", tag=self.term_name_tag, default_value=DEFAULT_TERM_NAME, width=360)
            dpg.add_checkbox(label="Use planned duration", tag=self.use_duration_tag, default_value=True, callback=self._on_duration_mode_changed)
            dpg.add_input_float(label="Duration (s)", tag=self.term_duration_tag, default_value=DEFAULT_TERM_DURATION_S, width=150, min_value=0.01, min_clamped=True, format="%.2f")
            with dpg.group(horizontal=True):
                dpg.add_button(label="Start Term", tag=self.start_term_tag, callback=self._on_start_term, width=145)
                dpg.add_button(label="Finish Protocol", tag=self.finish_protocol_tag, callback=self._on_finish_protocol, width=145)

        with dpg.collapsing_header(label="Previous Term Evaluation", tag=self.rating_header_tag, default_open=False):
            dpg.add_text("No valid completed term awaiting rating.", tag=self.rating_summary_tag, wrap=720)
            with dpg.child_window(tag=self.rating_buttons_tag, width=-1, height=235, border=True):
                for score, description in CR10_REFERENCE:
                    with dpg.group(horizontal=True):
                        dpg.add_button(label=str(score), user_data=score, callback=self._on_cr10, width=45)
                        dpg.add_text(description)

        with dpg.collapsing_header(label="Term History Overview", tag=self.term_overview_header_tag, default_open=True):
            with dpg.child_window(tag=self.term_overview_tag, width=-1, height=190, horizontal_scrollbar=True):
                pass

        with dpg.collapsing_header(label="Session Output / Advanced", tag=self.session_output_header_tag, default_open=False):
            dpg.add_input_text(label="Save root", tag=self.directory_tag, default_value=self._default_directory, width=500)
            dpg.add_input_text(label="Session name", tag=self.session_name_tag, default_value=self._default_session_name, width=500)
            dpg.add_combo(("HDF5", "CSV"), label="Format", tag=self.format_tag, default_value="HDF5", width=160)
            dpg.add_text("", tag=self.preview_tag, wrap=720)
            dpg.add_text("", tag=self.rows_tag)
            dpg.add_text("", tag=self.path_tag, wrap=720)
            dpg.add_text("", tag=self.pending_tag, wrap=720)

        with dpg.collapsing_header(label="Detailed Event History", tag=self.detailed_history_header_tag, default_open=False):
            dpg.add_text("Latest Event: -", tag=self.latest_history_tag)
            with dpg.child_window(tag=self.history_tag, width=-1, height=170, horizontal_scrollbar=True):
                pass

        with dpg.window(label="Confirm Discard", tag=self.discard_modal_tag, modal=True, show=False, no_resize=True, width=430, height=145):
            dpg.add_text("Delete this pending recording from staging? This cannot be undone.")
            with dpg.group(horizontal=True):
                dpg.add_button(label="Confirm Discard", callback=self._confirm_discard, width=150)
                dpg.add_button(label="Cancel", callback=lambda *_: dpg.configure_item(self.discard_modal_tag, show=False), width=100)

        self._build_progress_themes()
        self._build_keyboard_handlers()
        self._prepare_term_editor(force=True)
        self.refresh(force_history=True)

    def refresh(self, *, force_history: bool = False) -> None:
        if not dpg.does_item_exist(self.status_tag):
            return

        if self.session_state is SessionState.RECORDING:
            update_message = self.evaluation.update(capture_host_boundary())
            if update_message:
                self._last_message = update_message
                self._prepare_term_editor(force=True)
                self._set_rating_open(True)
                force_history = True

        dpg.set_value(
            self.status_tag,
            self._last_message,
        )
        dpg.set_value(self.rows_tag, f"Rows written: {self.recorder.rows_written}")
        dpg.set_value(self.preview_tag, self._output_preview_text())

        if self.session_state is SessionState.RECORDING:
            output_text = (
                "Staging output: -"
                if self._staging_data_path is None
                else f"Staging output: {self._staging_data_path}"
            )
        elif self.session_state is SessionState.PENDING_SAVE:
            output_text = (
                "Pending staging: -"
                if self._staging_root is None
                else f"Pending staging: {self._staging_root}"
            )
        else:
            output_text = "Staging output: -"
        dpg.set_value(self.path_tag, output_text)

        pending_text = ""
        if self.session_state is SessionState.PENDING_SAVE:
            term_count = len([segment for segment in self.evaluation.segments if segment.kind == "term"])
            pending_text = (
                f"Pending confirmation: {self.recorder.rows_written} rows, "
                f"{term_count} term attempt(s). Choose Save Session or Discard Session."
            )
        dpg.set_value(self.pending_tag, pending_text)

        self._refresh_overview()
        self._refresh_participant_console()
        self._refresh_rating_summary()
        self._refresh_term_history_overview()
        self._refresh_control_states()
        self._refresh_history(force=force_history)

    def shutdown_preserving_staging(self) -> None:
        if self.session_state is SessionState.RECORDING:
            try:
                self._stop_recording_to_pending()
                print(f"Pending fatigue-evaluation recording preserved in staging: {self._staging_root}")
            except Exception as exc:
                print(f"Failed to finalize pending fatigue-evaluation recording during shutdown: {exc}")

    # ------------------------------------------------------------------
    # Session lifecycle / staged save
    # ------------------------------------------------------------------

    def _on_start_session(self, *_args) -> None:
        if self.session_state is not SessionState.IDLE:
            self._last_message = "Resolve the current session before starting another."
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
            self._last_message = self.evaluation.start_session(capture_host_boundary())
            self._prepared_term_number = None
            self._prepare_term_editor(force=True)
        except Exception as exc:
            if self.recorder.is_recording:
                self.recorder.stop()
            if staging_root is not None and staging_root.exists():
                shutil.rmtree(staging_root, ignore_errors=True)
            self._clear_staging_state()
            self.session_state = SessionState.IDLE
            self._last_message = f"Start failed: {exc}"
        self.refresh(force_history=True)

    def _on_stop_session(self, *_args) -> None:
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
        if self.evaluation.state not in {
            FatigueEvaluationState.IDLE,
            FatigueEvaluationState.STOPPED,
        }:
            self.evaluation.stop_session(capture_host_boundary())
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
            final_sidecar = save_root / f"{session_name}.fatigue_evaluation.json"
            if final_data.exists() or final_sidecar.exists():
                raise FileExistsError(
                    f"Final HDF5 fatigue-evaluation session already exists: {final_data} or {final_sidecar}"
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
                raise FileExistsError(
                    f"Final CSV fatigue-evaluation session directory already exists: {final_output}"
                )
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
            self.evaluation = FatigueEvaluationController()
            self._history_signature = ()
            self._prepared_term_number = None
            self._prepare_term_editor(force=True)
            self._last_message = "Pending recording discarded."
        except Exception as exc:
            self._last_message = f"Discard failed: {exc}"
        self.refresh(force_history=True)

    # ------------------------------------------------------------------
    # Term / Q / T / CR10 controls
    # ------------------------------------------------------------------

    def _on_start_term(self, *_args) -> None:
        if self.session_state is not SessionState.RECORDING:
            self._last_message = "Start the recording session before starting a term."
            self.refresh()
            return
        if self.evaluation.state not in {
            FatigueEvaluationState.READY,
            FatigueEvaluationState.EVALUATING,
        }:
            self._last_message = "The experiment is not waiting for a term start."
            self.refresh()
            return
        try:
            spec = self._term_spec_from_window()
            self._last_message = self.evaluation.start_term(spec, capture_host_boundary())
            self._set_rating_open(False)
        except Exception as exc:
            self._last_message = f"Start term failed: {exc}"
        self.refresh(force_history=True)

    def _on_finish_protocol(self, *_args) -> None:
        if self.session_state is SessionState.RECORDING:
            self._last_message = self.evaluation.finish_protocol()
        self.refresh(force_history=True)

    def _on_q(self, *_args) -> None:
        if self.session_state is not SessionState.RECORDING:
            return
        if self._keyboard_shortcut_blocked():
            return
        if self.evaluation.state is FatigueEvaluationState.RUNNING:
            self._last_message = self.evaluation.record_action(capture_host_boundary())
            self.refresh(force_history=True)

    def _on_t(self, *_args) -> None:
        if self.session_state is not SessionState.RECORDING:
            return
        if self._keyboard_shortcut_blocked():
            return
        if self.evaluation.state is FatigueEvaluationState.RUNNING:
            self._last_message = self.evaluation.end_term_manual(capture_host_boundary())
            self._prepare_term_editor(force=True)
            self._set_rating_open(True)
            self.refresh(force_history=True)

    def _on_drop(self, *_args) -> None:
        if self.session_state is SessionState.RECORDING:
            previous_state = self.evaluation.state
            self._last_message = self.evaluation.drop(capture_host_boundary())
            if previous_state in {
                FatigueEvaluationState.RUNNING,
                FatigueEvaluationState.EVALUATING,
            }:
                self._prepare_term_editor(force=True)
                self._set_rating_open(False)
        self.refresh(force_history=True)

    def _on_cr10(self, _sender, _app_data, score) -> None:
        if self.session_state is SessionState.RECORDING:
            self._last_message = self.evaluation.set_cr10(int(score))
        self.refresh(force_history=True)

    def _on_duration_mode_changed(self, *_args) -> None:
        enabled = bool(dpg.get_value(self.use_duration_tag))
        dpg.configure_item(self.term_duration_tag, enabled=enabled)

    def _term_spec_from_window(self) -> FatigueEvaluationTermSpec:
        name = str(dpg.get_value(self.term_name_tag)).strip()
        use_duration = bool(dpg.get_value(self.use_duration_tag))
        duration = float(dpg.get_value(self.term_duration_tag)) if use_duration else None
        spec = FatigueEvaluationTermSpec(name=name, duration_s=duration)
        validated, error = self.evaluation.validate_term_spec(spec)
        if error is not None:
            raise ValueError(error)
        assert validated is not None
        return validated

    def _prepare_term_editor(self, *, force: bool = False) -> None:
        if not dpg.does_item_exist(self.term_number_tag):
            return
        term_number = self.evaluation.pending_term_number
        if not force and self._prepared_term_number == term_number:
            return

        source = self.evaluation.last_completed_term
        if source is None:
            # If the most recent attempt was dropped, inherit from that attempt.
            source = next(
                (
                    segment
                    for segment in reversed(self.evaluation.segments)
                    if segment.kind == "term" and segment.term_number == term_number
                ),
                None,
            )

        if source is None:
            name = DEFAULT_TERM_NAME
            duration = DEFAULT_TERM_DURATION_S
            use_duration = True
        else:
            name = source.label
            duration = source.planned_duration_s
            use_duration = duration is not None

        dpg.set_value(self.term_number_tag, f"Term: {term_number}")
        dpg.set_value(self.term_name_tag, name)
        dpg.set_value(self.use_duration_tag, use_duration)
        if duration is not None:
            dpg.set_value(self.term_duration_tag, float(duration))
        dpg.configure_item(self.term_duration_tag, enabled=use_duration)
        self._prepared_term_number = term_number

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------

    def _refresh_overview(self) -> None:
        terms = [segment for segment in self.evaluation.segments if segment.kind == "term"]
        completed = [segment for segment in terms if segment.status == "completed"]
        current = self.evaluation.current_term
        visible_terms = [*completed]
        if current is not None:
            visible_terms.append(current)
        total_actions = sum(segment.action_count for segment in visible_terms)
        current_text = "-" if current is None else f"TERM {current.term_number}"
        current_actions = 0 if current is None else current.action_count
        previous = next(
            (segment for segment in reversed(completed) if segment is not current),
            None,
        )
        previous_cr10 = "-" if previous is None or previous.cr10_score is None else str(previous.cr10_score)
        dpg.set_value(
            self.overview_tag,
            f"Terms completed: {len(completed)}    Current: {current_text}    "
            f"Total actions: {total_actions}    Current actions: {current_actions}    "
            f"CR10 previous: {previous_cr10}",
        )

    @staticmethod
    def _action_distribution_strip(
        term: FatigueEvaluationSegment,
        *,
        current_monotonic_ns: int | None = None,
        width: int = 36,
    ) -> str:
        if width < 4:
            width = 4
        if term.end is not None:
            span_s = term.duration_s()
        elif term.planned_duration_s is not None:
            span_s = term.planned_duration_s
        else:
            span_s = term.duration_s(current_monotonic_ns)
        if span_s <= 0:
            return "|" + " " * width + "|"
        bins = [0] * width
        for event in term.action_events:
            relative_s = (event.host_monotonic_ns - term.start_monotonic_ns) / 1e9
            fraction = min(0.999999, max(0.0, relative_s / span_s))
            bins[min(width - 1, int(fraction * width))] += 1
        body = "".join(" " if count == 0 else "*" if count == 1 else "#" for count in bins)
        return f"|{body}|"

    def _refresh_term_history_overview(self) -> None:
        terms = [
            segment
            for segment in self.evaluation.segments
            if segment.kind == "term" and segment.status == "completed"
        ]
        signature = tuple(
            (
                term.event_index,
                term.status,
                term.end_monotonic_ns,
                term.action_count,
                term.cr10_score,
            )
            for term in terms
        )
        if signature == self._term_overview_signature:
            return
        self._term_overview_signature = signature
        dpg.delete_item(self.term_overview_tag, children_only=True)
        if not terms:
            dpg.add_text("No term data yet.", parent=self.term_overview_tag)
            return
        dpg.add_text(
            "Term   Duration    Action distribution                         Count  CR10",
            parent=self.term_overview_tag,
        )
        now_ns = time.perf_counter_ns()
        for term in terms:
            duration = term.duration_s(now_ns if term.status == "running" else None)
            cr10 = "-" if term.cr10_score is None else str(term.cr10_score)
            strip = self._action_distribution_strip(
                term,
                current_monotonic_ns=now_ns if term.status == "running" else None,
            )
            dpg.add_text(
                f"T{term.term_number:<3}  {duration:>6.1f} s   {strip}   {term.action_count:>5}   {cr10:>4}",
                parent=self.term_overview_tag,
            )

    def _refresh_participant_console(self) -> None:
        now_ns = time.perf_counter_ns()
        state = self.evaluation.state
        term = self.evaluation.current_term
        if state is FatigueEvaluationState.RUNNING and term is not None:
            elapsed = self.evaluation.current_elapsed_s(now_ns)
            dpg.set_value(
                self.current_tag,
                f"TERM {term.term_number} | {term.label} | attempt {term.attempt}",
            )
            if term.planned_duration_s is None:
                dpg.set_value(self.elapsed_tag, f"Elapsed: {elapsed:.1f} s | Open-ended")
                dpg.set_value(self.progress_tag, 0.0)
                dpg.configure_item(self.progress_tag, overlay=f"OPEN-ENDED   {elapsed:.1f} s")
            else:
                fraction = self.evaluation.progress_fraction(now_ns)
                dpg.set_value(
                    self.elapsed_tag,
                    f"Elapsed: {elapsed:.1f} / {term.planned_duration_s:.1f} s",
                )
                dpg.set_value(self.progress_tag, fraction)
                dpg.configure_item(
                    self.progress_tag,
                    overlay=f"TERM {term.term_number}   {elapsed:.1f} / {term.planned_duration_s:.1f} s",
                )
            dpg.set_value(self.action_count_tag, f"Action events: {term.action_count}")
            dpg.set_value(
                self.action_distribution_tag,
                self._action_distribution_strip(term, current_monotonic_ns=now_ns),
            )
            self._bind_progress_theme(self.running_theme_tag)
            return

        self._bind_progress_theme(self.waiting_theme_tag)
        dpg.set_value(self.progress_tag, 0.0)
        dpg.set_value(self.action_count_tag, "Action events: -")
        dpg.set_value(self.action_distribution_tag, "")
        if state is FatigueEvaluationState.READY:
            dpg.set_value(self.current_tag, "NO STIMULUS / READY")
            dpg.set_value(self.elapsed_tag, f"Next: TERM {self.evaluation.pending_term_number}")
            dpg.configure_item(self.progress_tag, overlay="NO STIMULUS - READY")
        elif state is FatigueEvaluationState.EVALUATING:
            dpg.set_value(self.current_tag, "NO STIMULUS / INTER-TERM")
            dpg.set_value(self.elapsed_tag, f"Next: TERM {self.evaluation.pending_term_number}")
            dpg.configure_item(self.progress_tag, overlay="NO STIMULUS - INTER-TERM")
        elif state is FatigueEvaluationState.COMPLETE:
            dpg.set_value(self.current_tag, "PROTOCOL COMPLETE")
            dpg.set_value(self.elapsed_tag, "Stop Session when ready.")
            dpg.configure_item(self.progress_tag, overlay="PROTOCOL COMPLETE")
        else:
            dpg.set_value(self.current_tag, "NO STIMULUS")
            dpg.set_value(self.elapsed_tag, "Start Session to begin.")
            dpg.configure_item(self.progress_tag, overlay="NOT RUNNING")

    def _refresh_rating_summary(self) -> None:
        term = self.evaluation.last_completed_term
        if term is None:
            dpg.set_value(self.rating_summary_tag, "No valid completed term awaiting rating.")
            return
        score_text = "Not rated" if term.cr10_score is None else f"CR10 {term.cr10_score}: {term.cr10_label}"
        dpg.set_value(
            self.rating_summary_tag,
            f"Term {term.term_number} | {term.label} | duration {term.duration_s():.1f} s | "
            f"Q actions {term.action_count} | {score_text}",
        )

    def _refresh_control_states(self) -> None:
        idle = self.session_state is SessionState.IDLE
        recording = self.session_state is SessionState.RECORDING
        pending = self.session_state is SessionState.PENDING_SAVE
        waiting_for_term = recording and self.evaluation.state in {
            FatigueEvaluationState.READY,
            FatigueEvaluationState.EVALUATING,
        }
        running_term = recording and self.evaluation.state is FatigueEvaluationState.RUNNING
        rating_enabled = recording and self.evaluation.state is FatigueEvaluationState.EVALUATING and self.evaluation.last_completed_term is not None

        dpg.configure_item(self.directory_tag, enabled=idle)
        dpg.configure_item(self.format_tag, enabled=idle)
        dpg.configure_item(self.session_name_tag, enabled=idle or pending)
        dpg.configure_item(self.start_session_tag, enabled=idle)
        dpg.configure_item(self.stop_session_tag, enabled=recording)
        dpg.configure_item(self.save_tag, enabled=pending)
        dpg.configure_item(self.discard_tag, enabled=pending)

        dpg.configure_item(self.term_name_tag, enabled=waiting_for_term)
        dpg.configure_item(self.use_duration_tag, enabled=waiting_for_term)
        dpg.configure_item(
            self.term_duration_tag,
            enabled=waiting_for_term and bool(dpg.get_value(self.use_duration_tag)),
        )
        dpg.configure_item(self.start_term_tag, enabled=waiting_for_term)
        dpg.configure_item(self.finish_protocol_tag, enabled=waiting_for_term)
        dpg.configure_item(
            self.drop_tag,
            enabled=recording and self.evaluation.state in {
                FatigueEvaluationState.RUNNING,
                FatigueEvaluationState.EVALUATING,
            },
        )

        children = dpg.get_item_children(self.rating_buttons_tag, 1) or []
        for child in children:
            # Each direct child is a group; enable/disable its score button recursively.
            nested = dpg.get_item_children(child, 1) or []
            for item in nested:
                if "mvButton" in str(dpg.get_item_type(item)):
                    dpg.configure_item(item, enabled=rating_enabled)

        if running_term:
            self._set_term_open(False)
        elif waiting_for_term:
            self._set_term_open(True)

    def _refresh_history(self, *, force: bool = False) -> None:
        rows = self.evaluation.event_log_rows()
        signature = tuple(
            (
                row["event_index"],
                row["stimulus_code"],
                row["end_monotonic_ns"],
                row["status"],
                row["action_count"],
                row["cr10_score"],
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
            if row["kind"] == "term":
                identity = f"T{row['term_number']}/A{row['attempt']}"
                details = f"actions={row['action_count']} cr10={row['cr10_score']} end={row['end_method']}"
            else:
                identity = "wait"
                details = ""
            dpg.add_text(
                f"#{row['event_index']} {identity} code={row['stimulus_code']:>2} "
                f"{row['label']} | {start:.3f} -> {end} s | {row['status']} {details}",
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
    # Keyboard + progress themes
    # ------------------------------------------------------------------

    def _build_keyboard_handlers(self) -> None:
        if dpg.does_item_exist(self.keyboard_handler_tag):
            dpg.delete_item(self.keyboard_handler_tag)
        with dpg.handler_registry(tag=self.keyboard_handler_tag):
            dpg.add_key_press_handler(key=dpg.mvKey_Q, callback=self._on_q)
            dpg.add_key_press_handler(key=dpg.mvKey_T, callback=self._on_t)

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
        return any(
            token in item_type
            for token in ("mvInput", "mvDrag", "mvSlider", "mvCombo", "mvCheckbox", "mvRadioButton")
        )

    def _build_progress_themes(self) -> None:
        for tag, color in (
            (self.waiting_theme_tag, WAITING_COLOR),
            (self.running_theme_tag, RUNNING_COLOR),
        ):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
            with dpg.theme(tag=tag):
                with dpg.theme_component(dpg.mvProgressBar):
                    dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, color)
                    dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (10, 10, 10, 255))
        self._last_progress_theme = None

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
        sidecar = (
            staging_data / "fatigue_evaluation.json"
            if session_format == "csv"
            else self._require_staging_root() / "fatigue_evaluation.json"
        )
        metadata = self.evaluation.metadata_snapshot()
        metadata["recording_format"] = session_format
        metadata["session_status"] = "saved" if final_output is not None else "pending_save"
        metadata["recording_output"] = None if final_output is None else str(final_output)
        sidecar.write_text(json.dumps(metadata, indent=2, ensure_ascii=True), encoding="utf-8")
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
                f"  {save_root / (name + '.fatigue_evaluation.json')}"
            )
        return (
            "Output preview (CSV session directory):\n"
            f"  {save_root / name}/\n"
            "    metadata.json\n"
            "    fatigue_evaluation.json\n"
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

    def _set_term_open(self, is_open: bool) -> None:
        if dpg.does_item_exist(self.term_header_tag):
            try:
                dpg.set_value(self.term_header_tag, bool(is_open))
            except (RuntimeError, SystemError):
                pass

    def _set_rating_open(self, is_open: bool) -> None:
        if dpg.does_item_exist(self.rating_header_tag):
            try:
                dpg.set_value(self.rating_header_tag, bool(is_open))
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


# ======================================================================
# Acquisition + Plot + Save composition
# ======================================================================


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
    session_panel: IntegratedSaveFatigueEvaluationPanel | None = None
    try:
        plot_state = create_plot_window(provider)
        with dpg.window(
            label="Save + Fatigue Evaluation",
            tag="assembly.random_device_plot_save_fatigue_evaluation.window",
            width=790,
            height=930,
            pos=(950, 25),
        ):
            session_panel = IntegratedSaveFatigueEvaluationPanel(
                recorder,
                schemas,
                default_directory="captures",
                default_session_name="fatigue_evaluation_capture",
            )
            session_panel.build()

        device_failure_alert_tag = "assembly.random_device_plot_save_fatigue_evaluation.device_failure_alert"
        device_failure_text_tag = "assembly.random_device_plot_save_fatigue_evaluation.device_failure_text"
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
            title="Random Device Plot + Save + Fatigue Evaluation",
            width=1760,
            height=980,
            x_pos=40,
            y_pos=20,
        )
        dpg.setup_dearpygui()
        dpg.show_viewport()

        notified_failures: set[str] = set()
        while dpg.is_dearpygui_running():
            for pump in pumps:
                pump.drain(max_items=MAX_RECORDS_PER_PUMP_PER_FRAME)

            failures = group.failures()
            new_failures = tuple(
                worker_id for worker_id in failures if worker_id not in notified_failures
            )
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
