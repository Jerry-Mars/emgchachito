"""Minimal command-driven GUI framework for acquisition prototypes.

The command layer is importable without Dear PyGui. GUI-bound classes are
loaded lazily so future non-UI tests and workers can use the public command
interfaces without requiring a display stack.
"""

from fundamental.commands import CommandContext, CommandRegistry, CommandSpec
from fundamental.messages import AcquisitionState, SerialConfig

__all__ = [
    "AcquisitionController",
    "AcquisitionState",
    "CommandContext",
    "CommandRegistry",
    "CommandSpec",
    "FundamentalApp",
    "ManagedWindow",
    "SerialConfig",
    "WindowManager",
]


def __getattr__(name: str):
    if name == "FundamentalApp":
        from fundamental.app_shell import FundamentalApp

        return FundamentalApp
    if name == "AcquisitionController":
        from fundamental.acquisition import AcquisitionController

        return AcquisitionController
    if name in {"ManagedWindow", "WindowManager"}:
        from fundamental.window_manager import ManagedWindow, WindowManager

        return {"ManagedWindow": ManagedWindow, "WindowManager": WindowManager}[name]
    raise AttributeError(f"module 'fundamental' has no attribute {name!r}")
