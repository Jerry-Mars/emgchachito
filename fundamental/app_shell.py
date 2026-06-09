"""Dear PyGui shell for the fundamental command-driven framework."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import dearpygui.dearpygui as dpg

from fundamental.commands import CommandContext, CommandRegistry, CommandSpec
from fundamental.window_manager import ManagedWindow, WindowManager


LOG_WINDOW_TAG = "fundamental.log.window"
LOG_LINES_TAG = "fundamental.log.lines"
PALETTE_WINDOW_TAG = "fundamental.command_palette.window"
PALETTE_INPUT_TAG = "fundamental.command_palette.input"
PALETTE_LIST_TAG = "fundamental.command_palette.list"


FrameCallback = Callable[["FundamentalApp"], None]
ShutdownCallback = Callable[["FundamentalApp"], None]


class FundamentalApp:
    """Minimal app shell that exposes commands as the top-level control API."""

    def __init__(self) -> None:
        self.commands = CommandRegistry()
        self.window_manager = WindowManager()
        self._frame_callbacks: list[FrameCallback] = []
        self._shutdown_callbacks: list[ShutdownCallback] = []
        self._services: dict[str, Any] = {}
        self._log_entries: list[str] = []
        self._ui_ready = False
        self._context_created = False
        self._register_core_commands()

    def register_command(self, spec: CommandSpec) -> None:
        self.commands.register(spec)

    def register_frame_callback(self, callback: FrameCallback) -> None:
        self._frame_callbacks.append(callback)

    def register_shutdown_callback(self, callback: ShutdownCallback) -> None:
        self._shutdown_callbacks.append(callback)

    def register_service(self, name: str, service: Any) -> None:
        if name in self._services:
            raise ValueError(f"Service already registered: {name}")
        self._services[name] = service

    def get_service(self, name: str) -> Any:
        return self._services[name]

    def execute_command(self, command_text: str) -> str | None:
        result = self.commands.execute(command_text, self)
        if result:
            self.log(result)
        return result

    def open_window(self, tag: str) -> None:
        self.window_manager.show(tag)
        self.log(f"Opened window: {tag}")

    def close_window(self, tag: str) -> None:
        self.window_manager.hide(tag)
        self.log(f"Closed window: {tag}")

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        self._log_entries.append(entry)
        if self._ui_ready and dpg.does_item_exist(LOG_LINES_TAG):
            self._append_log_entry(entry)

    def clear_log(self) -> None:
        self._log_entries.clear()
        if self._ui_ready and dpg.does_item_exist(LOG_LINES_TAG):
            dpg.delete_item(LOG_LINES_TAG, children_only=True)

    def run(self) -> None:
        dpg.create_context()
        self._context_created = True
        self._configure_docking()
        self._register_core_windows()
        self._build_handlers()

        self.window_manager.show(LOG_WINDOW_TAG)
        dpg.create_viewport(title="EMG Fundamental", width=960, height=600, x_pos=80, y_pos=80)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        self._ui_ready = True
        self._flush_log_entries()
        self.log("Fundamental shell ready.")
        self.log("Use Ctrl+Shift+P to open the command palette.")

        try:
            while dpg.is_dearpygui_running():
                self._run_frame_callbacks()
                dpg.render_dearpygui_frame()
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        for callback in list(self._shutdown_callbacks):
            callback(self)

        if self._context_created:
            dpg.destroy_context()
            self._context_created = False
            self._ui_ready = False

    def show_command_palette(self) -> None:
        self.window_manager.show(PALETTE_WINDOW_TAG)
        dpg.set_value(PALETTE_INPUT_TAG, "")
        self._refresh_command_list("")
        try:
            dpg.focus_item(PALETTE_INPUT_TAG)
        except SystemError:
            pass

    def _register_core_commands(self) -> None:
        self.register_command(
            CommandSpec(
                name="help",
                description="List registered commands.",
                handler=self._help_command,
                visible=False,
            )
        )
        self.register_command(
            CommandSpec(
                name="palette.open",
                description="Open the command palette.",
                handler=self._open_palette_command,
                aliases=("command",),
                visible=False,
            )
        )
        self.register_command(
            CommandSpec(
                name="log.clear",
                description="Clear the log output.",
                handler=self._clear_log_command,
                visible=False,
            )
        )
        self.register_command(
            CommandSpec(
                name="window.close",
                description="Close the active command-opened window.",
                handler=self._close_window_command,
                visible=False,
            )
        )
        self.register_command(
            CommandSpec(
                name="quit",
                description="Exit the fundamental app.",
                handler=self._quit_command,
                visible=False,
            )
        )

    def _register_core_windows(self) -> None:
        self.window_manager.register(
            ManagedWindow(
                tag=LOG_WINDOW_TAG,
                title="Log Output",
                build=self._build_log_window,
                protected=True,
            )
        )
        self.window_manager.register(
            ManagedWindow(
                tag=PALETTE_WINDOW_TAG,
                title="Command Palette",
                build=self._build_command_palette,
            )
        )

    def _build_log_window(self) -> None:
        with dpg.window(
            label="Log Output",
            tag=LOG_WINDOW_TAG,
            width=680,
            height=220,
            pos=(20, 20),
            no_close=True,
        ):
            with dpg.child_window(tag=LOG_LINES_TAG, width=-1, height=-1, horizontal_scrollbar=True):
                pass

    def _build_command_palette(self) -> None:
        with dpg.window(
            label="Command Palette",
            tag=PALETTE_WINDOW_TAG,
            show=False,
            width=560,
            height=300,
            pos=(220, 100),
        ):
            dpg.add_input_text(
                tag=PALETTE_INPUT_TAG,
                hint="Command",
                width=-1,
                callback=self._handle_palette_input_change,
            )
            dpg.add_spacer(height=6)
            with dpg.child_window(tag=PALETTE_LIST_TAG, width=-1, height=-1):
                pass

    def _build_handlers(self) -> None:
        with dpg.handler_registry(tag="fundamental.handlers"):
            dpg.add_key_press_handler(key=dpg.mvKey_P, callback=self._handle_palette_shortcut)
            dpg.add_key_press_handler(key=dpg.mvKey_Escape, callback=self._handle_escape)
            dpg.add_key_press_handler(key=dpg.mvKey_Return, callback=self._handle_enter)

    def _configure_docking(self) -> None:
        try:
            dpg.configure_app(docking=True, docking_space=True)
        except TypeError:
            try:
                dpg.configure_app(docking=True)
            except Exception as exc:
                self.log(f"Docking unavailable: {exc}")
        except Exception as exc:
            self.log(f"Docking unavailable: {exc}")

    def _run_frame_callbacks(self) -> None:
        for callback in list(self._frame_callbacks):
            callback(self)

    def _flush_log_entries(self) -> None:
        if not dpg.does_item_exist(LOG_LINES_TAG):
            return
        dpg.delete_item(LOG_LINES_TAG, children_only=True)
        for entry in self._log_entries:
            self._append_log_entry(entry)

    def _append_log_entry(self, entry: str) -> None:
        dpg.add_text(entry, parent=LOG_LINES_TAG)
        try:
            dpg.set_y_scroll(LOG_LINES_TAG, dpg.get_y_scroll_max(LOG_LINES_TAG))
        except SystemError:
            pass

    def _handle_palette_shortcut(self, *_args) -> None:
        if self._is_ctrl_down() and self._is_shift_down():
            self.show_command_palette()

    def _handle_escape(self, *_args) -> None:
        if self.window_manager.is_shown(PALETTE_WINDOW_TAG):
            self.window_manager.hide(PALETTE_WINDOW_TAG)
            return

        closed_tag = self.window_manager.close_active()
        if closed_tag:
            self.log(f"Closed window: {closed_tag}")

    def _handle_enter(self, *_args) -> None:
        if self.window_manager.is_shown(PALETTE_WINDOW_TAG):
            self._execute_palette_input()

    def _handle_palette_input_change(self, _sender, value, _user_data=None) -> None:
        self._refresh_command_list(str(value))

    def _refresh_command_list(self, query: str) -> None:
        dpg.delete_item(PALETTE_LIST_TAG, children_only=True)
        commands = self.commands.list_commands(query)
        if not commands:
            dpg.add_text("No commands", parent=PALETTE_LIST_TAG)
            return

        for command in commands:
            with dpg.group(parent=PALETTE_LIST_TAG):
                dpg.add_button(
                    label=command.name,
                    width=-1,
                    callback=self._execute_command_button,
                    user_data=command.name,
                )
                dpg.add_text(command.description)
                dpg.add_spacer(height=4)

    def _execute_command_button(self, _sender, _app_data, user_data) -> None:
        self.execute_command(str(user_data))
        self.window_manager.hide(PALETTE_WINDOW_TAG)

    def _execute_palette_input(self) -> None:
        command_text = str(dpg.get_value(PALETTE_INPUT_TAG)).strip()
        if not command_text:
            return
        self.execute_command(command_text)
        self.window_manager.hide(PALETTE_WINDOW_TAG)

    def _help_command(self, context: CommandContext) -> str | None:
        context.log("Registered commands:")
        for command in self.commands.list_commands():
            context.log(f"  {command.name} - {command.description}")
        return None

    def _open_palette_command(self, _context: CommandContext) -> str | None:
        self.show_command_palette()
        return None

    def _clear_log_command(self, _context: CommandContext) -> str | None:
        self.clear_log()
        return "Log cleared."

    def _close_window_command(self, _context: CommandContext) -> str | None:
        closed_tag = self.window_manager.close_active()
        if closed_tag is None:
            return "No command-opened window to close."
        return f"Closed window: {closed_tag}"

    def _quit_command(self, _context: CommandContext) -> str | None:
        dpg.stop_dearpygui()
        return "Quit requested."

    def _is_ctrl_down(self) -> bool:
        return self._is_any_key_down(("mvKey_Control", "mvKey_LControl", "mvKey_RControl"))

    def _is_shift_down(self) -> bool:
        return self._is_any_key_down(("mvKey_Shift", "mvKey_LShift", "mvKey_RShift"))

    @staticmethod
    def _is_any_key_down(names: tuple[str, ...]) -> bool:
        for name in names:
            key = getattr(dpg, name, None)
            if key is not None and dpg.is_key_down(key):
                return True
        return False
