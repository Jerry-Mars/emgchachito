"""Shared data contracts for acquisition, plotting, and saving."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Mapping

DEFAULT_SERIAL_PORT = "COM5"
DEFAULT_BAUD_RATE = 921600
DEFAULT_SERIAL_TIMEOUT = 0.05
DEFAULT_PLOT_WINDOW_SECONDS = 5.0
DEFAULT_PLOT_BUFFER_SIZE = 20000
DEFAULT_MAX_FRAMES_PER_BATCH = 64


class AcquisitionState(Enum):
    """Minimal acquisition lifecycle."""

    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


@dataclass
class SerialConfig:
    """Runtime serial configuration."""

    port: str = DEFAULT_SERIAL_PORT
    baud_rate: int = DEFAULT_BAUD_RATE
    timeout_s: float = DEFAULT_SERIAL_TIMEOUT

    def normalized(self) -> "SerialConfig":
        return SerialConfig(
            port=self.port.strip(),
            baud_rate=max(1, int(self.baud_rate)),
            timeout_s=max(0.001, float(self.timeout_s)),
        )

    def display_text(self) -> str:
        return f"{self.port or '-'} @ {self.baud_rate}, timeout {self.timeout_s:.3f}s"


@dataclass(frozen=True)
class WorkerEvent:
    """Status or failure event emitted by a worker."""

    kind: Literal["log", "error", "metadata"]
    message: str = ""
    data: Mapping[str, Any] | None = None
