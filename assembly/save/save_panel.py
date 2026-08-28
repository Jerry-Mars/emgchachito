"""Minimal DearPyGui controls for one H5StreamRecorder.

The panel owns only save presentation and recorder commands.  It does not start,
stop, pause, or inspect acquisition workers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import dearpygui.dearpygui as dpg

from assembly.acquisition.runtime.stream_store import StreamSchema
from assembly.save.recorder import H5StreamRecorder


class SavePanel:
    """Small Start/Stop recording panel with directory and file-name inputs."""

    def __init__(
        self,
        recorder: H5StreamRecorder,
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
        self.start_tag = f"{tag_prefix}.start"
        self.stop_tag = f"{tag_prefix}.stop"
        self.status_tag = f"{tag_prefix}.status"
        self.rows_tag = f"{tag_prefix}.rows"
        self.path_tag = f"{tag_prefix}.path"
        self._default_directory = str(default_directory)
        self._default_filename = default_filename
        self._last_message = "Ready."

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
        dpg.set_value(self.status_tag, f"Status: {state} | {self._last_message}")
        dpg.set_value(self.rows_tag, f"Rows written: {self.recorder.rows_written}")
        dpg.set_value(
            self.path_tag,
            "File: -" if self.recorder.path is None else f"File: {self.recorder.path}",
        )
        dpg.configure_item(self.start_tag, enabled=not self.recorder.is_recording)
        dpg.configure_item(self.stop_tag, enabled=self.recorder.is_recording)
        dpg.configure_item(self.directory_tag, enabled=not self.recorder.is_recording)
        dpg.configure_item(self.filename_tag, enabled=not self.recorder.is_recording)

    def _output_path(self) -> Path:
        directory = str(dpg.get_value(self.directory_tag)).strip()
        filename = str(dpg.get_value(self.filename_tag)).strip()
        if not directory:
            raise ValueError("Directory must not be empty.")
        if not filename:
            raise ValueError("File name must not be empty.")
        return Path(directory).expanduser() / filename

    def _on_start(self, *_args) -> None:
        try:
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
