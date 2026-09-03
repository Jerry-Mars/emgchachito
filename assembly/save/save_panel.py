"""Minimal DearPyGui controls for normalized stream recording.

The panel owns only save presentation and recorder commands.  It does not start,
stop, pause, or inspect acquisition workers.  When given a
SelectableStreamRecorder it also exposes the persistence backend choice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import dearpygui.dearpygui as dpg

from assembly.acquisition.runtime.stream_store import StreamSchema
from assembly.save.recorder import StreamRecorder
from assembly.save.selectable_recorder import SelectableStreamRecorder


class SavePanel:
    """Small Start/Stop recording panel with optional HDF5/CSV selection."""

    def __init__(
        self,
        recorder: StreamRecorder,
        schemas: Iterable[StreamSchema],
        *,
        tag_prefix: str = "assembly.save",
        default_directory: str | Path = "captures",
        default_filename: str = "capture.h5",
    ) -> None:
        self.recorder = recorder
        self.schemas = tuple(schemas)
        if not self.schemas:
            raise ValueError("SavePanel requires at least one stream schema.")

        self.prefix = tag_prefix
        self.directory_tag = f"{tag_prefix}.directory"
        self.filename_tag = f"{tag_prefix}.filename"
        self.format_tag = f"{tag_prefix}.format"
        self.start_tag = f"{tag_prefix}.start"
        self.stop_tag = f"{tag_prefix}.stop"
        self.status_tag = f"{tag_prefix}.status"
        self.rows_tag = f"{tag_prefix}.rows"
        self.path_tag = f"{tag_prefix}.path"
        self._default_directory = str(default_directory)
        self._default_filename = default_filename
        self._last_message = "Ready."

    @property
    def _supports_format_selection(self) -> bool:
        return isinstance(self.recorder, SelectableStreamRecorder)

    def build(self, *, parent: int | str | None = None) -> None:
        kwargs = {} if parent is None else {"parent": parent}
        with dpg.group(**kwargs):
            dpg.add_text("Save normalized data")
            dpg.add_input_text(
                label="Directory",
                tag=self.directory_tag,
                default_value=self._default_directory,
                width=500,
            )
            dpg.add_input_text(
                label="File name",
                tag=self.filename_tag,
                default_value=self._default_filename,
                width=500,
            )
            if self._supports_format_selection:
                assert isinstance(self.recorder, SelectableStreamRecorder)
                dpg.add_combo(
                    ("HDF5", "CSV"),
                    label="Format",
                    tag=self.format_tag,
                    default_value="HDF5" if self.recorder.format == "hdf5" else "CSV",
                    width=160,
                )
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Start Recording",
                    tag=self.start_tag,
                    callback=self._on_start,
                    width=140,
                )
                dpg.add_button(
                    label="Stop Recording",
                    tag=self.stop_tag,
                    callback=self._on_stop,
                    width=140,
                )
            dpg.add_text("", tag=self.status_tag)
            dpg.add_text("", tag=self.rows_tag)
            dpg.add_text("", tag=self.path_tag, wrap=700)
        self.refresh()

    def refresh(self) -> None:
        if not dpg.does_item_exist(self.status_tag):
            return
        state = self.recorder.state.value.upper()
        format_note = ""
        if self._supports_format_selection:
            assert isinstance(self.recorder, SelectableStreamRecorder)
            format_note = f" | Format: {self.recorder.format.upper()}"
        dpg.set_value(
            self.status_tag,
            f"Status: {state}{format_note} | {self._last_message}",
        )
        dpg.set_value(self.rows_tag, f"Rows written: {self.recorder.rows_written}")
        dpg.set_value(
            self.path_tag,
            "Output: -" if self.recorder.path is None else f"Output: {self.recorder.path}",
        )
        dpg.configure_item(self.start_tag, enabled=not self.recorder.is_recording)
        dpg.configure_item(self.stop_tag, enabled=self.recorder.is_recording)
        dpg.configure_item(self.directory_tag, enabled=not self.recorder.is_recording)
        dpg.configure_item(self.filename_tag, enabled=not self.recorder.is_recording)
        if self._supports_format_selection and dpg.does_item_exist(self.format_tag):
            dpg.configure_item(self.format_tag, enabled=not self.recorder.is_recording)

    def _output_path(self) -> Path:
        directory = str(dpg.get_value(self.directory_tag)).strip()
        filename = str(dpg.get_value(self.filename_tag)).strip()
        if not directory:
            raise ValueError("Directory must not be empty.")
        if not filename:
            raise ValueError("File name must not be empty.")
        return Path(directory).expanduser() / filename

    def _apply_selected_format(self) -> None:
        if not self._supports_format_selection:
            return
        assert isinstance(self.recorder, SelectableStreamRecorder)
        selected = str(dpg.get_value(self.format_tag)).strip().casefold()
        if selected == "hdf5":
            self.recorder.set_format("hdf5")
        elif selected == "csv":
            self.recorder.set_format("csv")
        else:
            raise ValueError(f"Unknown save format: {selected!r}.")

    def _on_start(self, *_args) -> None:
        try:
            self._apply_selected_format()
            path = self.recorder.start(self._output_path(), self.schemas)
        except Exception as exc:
            self._last_message = f"Start failed: {exc}"
        else:
            self._last_message = f"Recording started: {path.name}"
        self.refresh()

    def _on_stop(self, *_args) -> None:
        try:
            path = self.recorder.stop()
        except Exception as exc:
            self._last_message = f"Stop failed: {exc}"
        else:
            self._last_message = "Recording stopped." if path is None else f"Saved: {path.name}"
        self.refresh()
