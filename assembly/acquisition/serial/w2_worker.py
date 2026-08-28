"""Standalone RunE W2 serial worker using the existing protocol parser."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

from DeviceInterface.w2_protocol import (
    W2CommandBuilder,
    W2Packet,
    W2RawPacket,
    W2RmsPacket,
    W2StreamParser,
)

try:
    import serial as _serial
except ImportError:  # pragma: no cover - depends on local runtime
    _serial = None


W2ModeName = Literal["emg_raw", "emg_rms", "eeg_raw"]
W2Record = dict[str, object]

DEFAULT_W2_SAMPLE_RATE_HZ = 1000.0
DEFAULT_W2_SERIAL_BAUD_RATE = 256000
DEFAULT_W2_SERIAL_TIMEOUT_S = 0.05


@dataclass(frozen=True, slots=True)
class W2SerialConfig:
    """Configuration for one physical RunE W2 connected over one serial port."""

    device_id: str
    port: str
    mode: W2ModeName = "emg_raw"
    nominal_rate_hz: float = DEFAULT_W2_SAMPLE_RATE_HZ
    baud_rate: int = DEFAULT_W2_SERIAL_BAUD_RATE
    timeout_s: float = DEFAULT_W2_SERIAL_TIMEOUT_S

    def __post_init__(self) -> None:
        device_id = self.device_id.strip()
        port = self.port.strip()
        if not device_id:
            raise ValueError("W2 device_id must not be empty.")
        if not port:
            raise ValueError("W2 serial port must not be empty.")
        if self.mode not in W2CommandBuilder.MODE_BY_NAME or self.mode == "stop":
            raise ValueError(f"Unsupported W2 mode: {self.mode!r}.")
        if self.nominal_rate_hz <= 0:
            raise ValueError("W2 nominal_rate_hz must be positive.")
        if self.baud_rate <= 0:
            raise ValueError("W2 baud_rate must be positive.")
        if self.timeout_s <= 0:
            raise ValueError("W2 timeout_s must be positive.")
        object.__setattr__(self, "device_id", device_id)
        object.__setattr__(self, "port", port)


def _packet_samples(packet: W2Packet, expected_mode: W2ModeName) -> tuple[float, ...]:
    if isinstance(packet, W2RmsPacket):
        if expected_mode != "emg_rms":
            raise ValueError(
                f"W2 returned RMS data while configured for {expected_mode!r}."
            )
        return (float(packet.rms),)

    if not isinstance(packet, W2RawPacket):  # pragma: no cover - closed union safeguard
        raise TypeError(f"Unsupported W2 packet type: {type(packet).__name__}.")

    expected_code = W2CommandBuilder.MODE_BY_NAME[expected_mode]
    if packet.mode != expected_code:
        raise ValueError(
            f"W2 returned mode 0x{packet.mode:02X} while configured for "
            f"{expected_mode!r}."
        )
    return tuple(float(value) for value in packet.values)


class SerialW2Worker(threading.Thread):
    """Own one W2 serial connection and publish decoded packet observations."""

    def __init__(
        self,
        config: W2SerialConfig,
        records: queue.Queue[W2Record] | None = None,
        *,
        serial_backend: Any = None,
    ) -> None:
        super().__init__(name=f"SerialW2Worker-{config.device_id}", daemon=True)
        self.config = config
        self.records = records if records is not None else queue.Queue()
        self.serial_backend = _serial if serial_backend is None else serial_backend
        self.stop_event = threading.Event()
        self.startup_event = threading.Event()
        self.stopped_event = threading.Event()
        self.error: BaseException | None = None
        self.started_collecting = False
        self.parser = W2StreamParser()
        self.packet_count = 0
        self.read_count = 0

    def request_stop(self) -> None:
        """Request cooperative shutdown without waiting for serial cleanup."""

        self.stop_event.set()

    def close(self, timeout_s: float = 5.0) -> None:
        """Single-worker convenience API: request stop, wait, surface failure."""

        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive.")
        if threading.current_thread() is self:
            raise RuntimeError("A SerialW2Worker cannot join itself.")
        self.request_stop()
        if self.ident is not None:
            self.join(timeout_s)
        if self.is_alive():
            raise TimeoutError(
                f"W2 worker {self.config.device_id!r} did not stop before timeout_s."
            )
        if self.error is not None:
            raise RuntimeError(f"W2 worker {self.config.device_id!r} failed.") from self.error

    def run(self) -> None:
        failure: BaseException | None = None
        handle = None
        collecting = False

        try:
            backend = self.serial_backend
            if backend is None:
                raise RuntimeError("pyserial is not installed; W2 serial acquisition is unavailable.")

            handle = backend.Serial(
                self.config.port,
                self.config.baud_rate,
                bytesize=backend.EIGHTBITS,
                parity=backend.PARITY_NONE,
                stopbits=backend.STOPBITS_ONE,
                timeout=self.config.timeout_s,
            )
            handle.reset_input_buffer()

            if self.stop_event.is_set():
                return

            handle.write(W2CommandBuilder.start_for_mode(self.config.mode))
            handle.flush()
            collecting = True
            self.started_collecting = True
            self.startup_event.set()

            while not self.stop_event.is_set():
                chunk = bytes(handle.read(512))
                if not chunk:
                    continue

                self.read_count += 1
                host_monotonic_ns = time.perf_counter_ns()
                host_unix_ns = time.time_ns()
                packets = self.parser.feed(chunk)

                for packet in packets:
                    record: W2Record = {
                        "packet_index": self.packet_count,
                        "mode": self.config.mode,
                        "host_monotonic_ns": host_monotonic_ns,
                        "host_unix_ns": host_unix_ns,
                        "samples": _packet_samples(packet, self.config.mode),
                    }
                    try:
                        self.records.put_nowait(record)
                    except queue.Full as exc:
                        raise RuntimeError(
                            f"W2 record queue is full for {self.config.device_id!r}; "
                            "acquisition stopped."
                        ) from exc
                    self.packet_count += 1

        except BaseException as exc:
            failure = exc
        finally:
            self.stop_event.set()
            cleanup_errors: list[BaseException] = []

            if handle is not None and collecting:
                try:
                    handle.write(W2CommandBuilder.stop_collect())
                    handle.flush()
                except BaseException as exc:
                    exc.add_note(f"W2 cleanup stop command: {self.config.device_id}")
                    cleanup_errors.append(exc)

            if handle is not None:
                try:
                    handle.close()
                except BaseException as exc:
                    exc.add_note(f"W2 cleanup serial close: {self.config.device_id}")
                    cleanup_errors.append(exc)

            self.started_collecting = False

            if cleanup_errors:
                cleanup_failure: BaseException = (
                    cleanup_errors[0]
                    if len(cleanup_errors) == 1
                    else BaseExceptionGroup("W2 cleanup failed.", cleanup_errors)
                )
                failure = (
                    BaseExceptionGroup(
                        "W2 acquisition and cleanup both failed.",
                        [failure, cleanup_failure],
                    )
                    if failure is not None
                    else cleanup_failure
                )

            self.error = failure
            self.startup_event.set()
            self.stopped_event.set()


__all__ = [
    "DEFAULT_W2_SAMPLE_RATE_HZ",
    "DEFAULT_W2_SERIAL_BAUD_RATE",
    "DEFAULT_W2_SERIAL_TIMEOUT_S",
    "SerialW2Worker",
    "W2ModeName",
    "W2Record",
    "W2SerialConfig",
]
