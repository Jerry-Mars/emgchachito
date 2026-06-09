"""Small Dear PyGui window registry for command-opened tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import dearpygui.dearpygui as dpg


@dataclass(frozen=True)
class ManagedWindow:
    """A lazily built window exposed through the shell."""

    tag: str
    title: str
    build: Callable[[], None]
    protected: bool = False


class WindowManager:
    """Create, show, and hide registered windows from command handlers."""

    def __init__(self) -> None:
        self._windows: dict[str, ManagedWindow] = {}
        self._active_order: list[str] = []

    def register(self, window: ManagedWindow) -> None:
        if window.tag in self._windows:
            raise ValueError(f"Window already registered: {window.tag}")
        self._windows[window.tag] = window

    def show(self, tag: str) -> None:
        self._ensure_built(tag)
        dpg.configure_item(tag, show=True)
        self._mark_active(tag)
        try:
            dpg.focus_item(tag)
        except SystemError:
            pass

    def hide(self, tag: str) -> None:
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, show=False)
        self._remove_active(tag)

    def close_active(self) -> str | None:
        for tag in reversed(self._active_order):
            window = self._windows.get(tag)
            if window is None or window.protected:
                continue
            if dpg.does_item_exist(tag) and dpg.is_item_shown(tag):
                self.hide(tag)
                return tag
        return None

    def is_shown(self, tag: str) -> bool:
        return dpg.does_item_exist(tag) and dpg.is_item_shown(tag)

    def _ensure_built(self, tag: str) -> None:
        window = self._windows.get(tag)
        if window is None:
            raise KeyError(f"Window is not registered: {tag}")
        if dpg.does_item_exist(tag):
            return

        window.build()
        if not dpg.does_item_exist(tag):
            raise RuntimeError(f"Window builder did not create expected tag: {tag}")

    def _mark_active(self, tag: str) -> None:
        self._remove_active(tag)
        self._active_order.append(tag)

    def _remove_active(self, tag: str) -> None:
        self._active_order = [active_tag for active_tag in self._active_order if active_tag != tag]
