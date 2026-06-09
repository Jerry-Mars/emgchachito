"""Command registry used by the fundamental GUI shell.

The registry is intentionally independent from Dear PyGui. Feature modules can
register commands without importing any GUI framework details.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fundamental.app_shell import FundamentalApp


CommandHandler = Callable[["CommandContext"], str | None]


@dataclass(frozen=True)
class CommandContext:
    """Runtime context passed into command handlers."""

    app: "FundamentalApp"
    raw_input: str
    command_name: str
    args: str = ""

    def log(self, message: str) -> None:
        self.app.log(message)

    def open_window(self, tag: str) -> None:
        self.app.open_window(tag)

    def close_window(self, tag: str) -> None:
        self.app.close_window(tag)

    def execute(self, command_text: str) -> str | None:
        return self.app.execute_command(command_text)


@dataclass(frozen=True)
class CommandSpec:
    """Public command contract exposed to feature modules."""

    name: str
    description: str
    handler: CommandHandler
    aliases: tuple[str, ...] = field(default_factory=tuple)
    visible: bool = True

    def all_names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


class CommandRegistry:
    """Register, resolve, and execute named commands."""

    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec] = {}
        self._aliases: dict[str, str] = {}

    def register(self, spec: CommandSpec) -> None:
        name = self._normalize(spec.name)
        if not name:
            raise ValueError("Command name cannot be empty.")
        if name in self._commands:
            raise ValueError(f"Command already registered: {spec.name}")

        self._commands[name] = spec
        for alias in spec.aliases:
            alias_key = self._normalize(alias)
            if not alias_key:
                raise ValueError(f"Alias for {spec.name} cannot be empty.")
            if alias_key in self._commands or alias_key in self._aliases:
                raise ValueError(f"Command alias already registered: {alias}")
            self._aliases[alias_key] = name

    def unregister(self, name: str) -> None:
        key = self._normalize(name)
        canonical = self._aliases.pop(key, key)
        spec = self._commands.pop(canonical, None)
        if spec is None:
            return

        for alias in spec.aliases:
            self._aliases.pop(self._normalize(alias), None)

    def resolve(self, name: str) -> CommandSpec | None:
        key = self._normalize(name)
        canonical = self._aliases.get(key, key)
        return self._commands.get(canonical)

    def list_commands(self, query: str = "", include_hidden: bool = False) -> list[CommandSpec]:
        query_key = self._normalize(query)
        commands = [
            command
            for command in self._commands.values()
            if include_hidden or command.visible
        ]
        if query_key:
            commands = [
                command
                for command in commands
                if query_key in self._normalize(command.name)
                or query_key in self._normalize(command.description)
                or any(query_key in self._normalize(alias) for alias in command.aliases)
            ]
        return sorted(commands, key=lambda command: command.name)

    def execute(self, command_text: str, app: "FundamentalApp") -> str | None:
        raw_input = command_text.strip()
        if not raw_input:
            return "No command entered."

        command_name, _, args = raw_input.partition(" ")
        spec = self.resolve(command_name)
        if spec is None:
            return f"Unknown command: {command_name}"

        context = CommandContext(
            app=app,
            raw_input=raw_input,
            command_name=spec.name,
            args=args.strip(),
        )
        return spec.handler(context)

    @staticmethod
    def _normalize(value: str) -> str:
        return value.strip().casefold()
