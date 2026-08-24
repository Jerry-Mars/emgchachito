"""Small, standalone Myo EMG/IMU worker built on pymyo's decoded callbacks.

The caller owns device discovery and supplies a ``BLEDevice``.  Each queue item
is one host-received notification; no device timestamp or nominal sample time is
invented here.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from contextlib import suppress
from operator import index as integer_index
from typing import Any, Awaitable

from bleak.backends.device import BLEDevice
from pymyo import Myo
from pymyo.types import ClassifierMode, EmgMode, ImuMode, SleepMode

MyoRecord = dict[str, object]


def _make_emg_record(
    samples: object,
    notification_index: int,
    monotonic_ns: int,
    unix_ns: int,
) -> MyoRecord:
    """Validate one decoded pymyo EMG notification and preserve its boundary."""

    try:
        rows = tuple(tuple(integer_index(value) for value in row) for row in samples)  # type: ignore[union-attr]
    except (TypeError, AttributeError) as exc:
        raise TypeError("EMG samples must be an iterable of integer iterables.") from exc
    if len(rows) != 2 or any(len(row) != 8 for row in rows):
        raise ValueError("A pymyo EMG notification must contain two 8-channel samples.")
    if any(not -128 <= value <= 127 for row in rows for value in row):
        raise ValueError("Raw Myo EMG values must fit signed int8.")
    return {
        "stream": "emg",
        "notification_index": notification_index,
        "host_monotonic_ns": monotonic_ns,
        "host_unix_ns": unix_ns,
        "samples": rows,
    }


def _make_imu_record(
    orientation: object,
    accelerometer: object,
    gyroscope: object,
    sample_index: int,
    monotonic_ns: int,
    unix_ns: int,
) -> MyoRecord:
    """Validate one decoded/scaled pymyo IMU sample."""

    try:
        quaternion = tuple(float(getattr(orientation, axis)) for axis in "wxyz")
        acceleration = tuple(float(value) for value in accelerometer)  # type: ignore[union-attr]
        angular_velocity = tuple(float(value) for value in gyroscope)  # type: ignore[union-attr]
    except (TypeError, AttributeError, ValueError) as exc:
        raise TypeError("IMU values must match pymyo's quaternion/vector callback.") from exc
    if len(acceleration) != 3 or len(angular_velocity) != 3:
        raise ValueError("Accelerometer and gyroscope vectors must each have three values.")
    return {
        "stream": "imu",
        "sample_index": sample_index,
        "host_monotonic_ns": monotonic_ns,
        "host_unix_ns": unix_ns,
        "quaternion": quaternion,
        "accelerometer_g": acceleration,
        "gyroscope_dps": angular_velocity,
    }


async def _read_device_info(myo: Any, device: BLEDevice) -> dict[str, object]:
    """Read the one-time metadata included in the agreed worker boundary."""

    name = await myo.name
    battery = await myo.battery
    firmware = await myo.firmware_version
    info = await myo.info
    return {
        "name": name,
        "battery_percent": battery,
        "firmware": str(firmware),
        "firmware_major": firmware.major,
        "firmware_minor": firmware.minor,
        "firmware_patch": firmware.patch,
        "hardware_revision": firmware.hardware_rev.name,
        "serial_number_hex": info.serial_number.hex(":"),
        "unlock_pose": info.unlock_pose.name,
        "classifier_type": info.active_classifier_type.name,
        "classifier_index": info.active_classifier_index,
        "has_custom_classifier": info.has_custom_classifier,
        "stream_indicating": info.stream_indicating,
        "sku": info.sku.name,
        "ble_identifier": device.address,
    }


async def _shutdown_myo(myo: Any) -> None:
    """Attempt every cleanup step, then report all failures together."""

    errors: list[Exception] = []
    steps = []
    if myo.is_connected:
        steps.extend(
            (
                (
                    "stop streams",
                    lambda: myo.set_mode(
                        emg_mode=EmgMode.NONE,
                        imu_mode=ImuMode.NONE,
                        classifier_mode=ClassifierMode.DISABLED,
                    ),
                ),
                ("restore normal sleep", lambda: myo.set_sleep_mode(SleepMode.NORMAL)),
            ),
        )
    steps.append(("disconnect", myo.disconnect))
    for label, step in steps:
        try:
            await step()
        except Exception as exc:  # cleanup must continue after an individual failure
            exc.add_note(f"Myo cleanup step: {label}")
            errors.append(exc)
    if errors:
        raise ExceptionGroup("Myo cleanup failed.", errors)


class MyoWorker(threading.Thread):
    """Own one Myo connection and publish decoded notifications to a queue."""

    def __init__(
        self,
        device: BLEDevice,
        records: queue.Queue[MyoRecord] | None = None,
        stop_event: threading.Event | None = None,
        *,
        connect_timeout_s: float = 20.0,
    ) -> None:
        if connect_timeout_s <= 0:
            raise ValueError("connect_timeout_s must be positive.")
        super().__init__(name="MyoWorker", daemon=True)
        self.device = device
        self.records = records if records is not None else queue.Queue()
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.connect_timeout_s = float(connect_timeout_s)
        self.startup_event = threading.Event()
        self.stopped_event = threading.Event()
        self.started_streaming = False
        self.device_info: dict[str, object] | None = None
        self.error: BaseException | None = None
        self._callback_error: Exception | None = None
        self._accepting_records = False
        self._emg_notification_index = 0
        self._imu_sample_index = 0

    def run(self) -> None:
        try:
            asyncio.run(self._run_async())
        except BaseException as exc:  # preserve failures for the controlling thread
            self.error = exc
            self.stop_event.set()
        finally:
            self.startup_event.set()
            self.stopped_event.set()

    def close(self, timeout_s: float = 5.0) -> None:
        """Request cooperative shutdown, wait for cleanup, and surface failure."""

        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive.")
        if threading.current_thread() is self:
            raise RuntimeError("A MyoWorker cannot join itself.")
        self.stop_event.set()
        if self.ident is not None:
            self.join(timeout_s)
        if self.is_alive():
            raise TimeoutError("MyoWorker did not stop before timeout_s.")
        if self.error is not None:
            raise RuntimeError("MyoWorker failed.") from self.error

    async def _run_async(self) -> None:
        myo = Myo(self.device, timeout=self.connect_timeout_s)
        myo.on_emg(self._on_emg)
        myo.on_imu(self._on_imu)

        failure: BaseException | None = None
        try:
            opened = await self._await_or_stop(self._open(myo))
            if self._callback_error is not None:
                raise self._callback_error
            if opened and not self.stop_event.is_set():
                self.started_streaming = True
                self.startup_event.set()
                while not self.stop_event.is_set():
                    if not myo.is_connected:
                        raise ConnectionError("Myo disconnected while streaming.")
                    await asyncio.sleep(0.05)
                if self._callback_error is not None:
                    raise self._callback_error
        except BaseException as exc:
            failure = exc

        self._accepting_records = False
        self.stop_event.set()
        try:
            await _shutdown_myo(myo)
        except BaseException as exc:
            failure = (
                BaseExceptionGroup("Myo session and cleanup both failed.", [failure, exc])
                if failure is not None
                else exc
            )
        if failure is not None:
            raise failure

    async def _open(self, myo: Myo) -> None:
        await myo.connect()
        await myo.set_mode(
            emg_mode=EmgMode.NONE,
            imu_mode=ImuMode.NONE,
            classifier_mode=ClassifierMode.DISABLED,
        )
        self.device_info = await _read_device_info(myo, self.device)
        await myo.set_sleep_mode(SleepMode.NEVER_SLEEP)
        self._accepting_records = True
        try:
            await myo.set_mode(
                emg_mode=EmgMode.EMG_RAW,
                imu_mode=ImuMode.DATA,
                classifier_mode=ClassifierMode.DISABLED,
            )
        except BaseException:
            self._accepting_records = False
            raise

    async def _await_or_stop(self, awaitable: Awaitable[Any]) -> bool:
        task = asyncio.ensure_future(awaitable)
        while not task.done():
            if self.stop_event.is_set():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                return False
            await asyncio.sleep(0.05)
        await task
        return True

    def _on_emg(self, samples: object) -> None:
        if self.stop_event.is_set() or not self._accepting_records:
            return
        try:
            record = _make_emg_record(
                samples,
                self._emg_notification_index,
                time.perf_counter_ns(),
                time.time_ns(),
            )
            self.records.put_nowait(record)
            self._emg_notification_index += 1
        except queue.Full:
            self._fail_callback(RuntimeError("Myo record queue is full; acquisition stopped."))
        except Exception as exc:
            self._fail_callback(exc)

    def _on_imu(self, orientation: object, accelerometer: object, gyroscope: object) -> None:
        if self.stop_event.is_set() or not self._accepting_records:
            return
        try:
            record = _make_imu_record(
                orientation,
                accelerometer,
                gyroscope,
                self._imu_sample_index,
                time.perf_counter_ns(),
                time.time_ns(),
            )
            self.records.put_nowait(record)
            self._imu_sample_index += 1
        except queue.Full:
            self._fail_callback(RuntimeError("Myo record queue is full; acquisition stopped."))
        except Exception as exc:
            self._fail_callback(exc)

    def _fail_callback(self, exc: Exception) -> None:
        if self._callback_error is None:
            self._callback_error = exc
            self.stop_event.set()


class MyoStartupTimeout(TimeoutError):
    """A startup timeout whose worker remains available for inspection or retrying close."""

    def __init__(self, message: str, worker: MyoWorker) -> None:
        super().__init__(message)
        self.worker = worker


def start_myo(
    device: BLEDevice,
    records: queue.Queue[MyoRecord] | None = None,
    stop_event: threading.Event | None = None,
    *,
    connect_timeout_s: float = 20.0,
    startup_timeout_s: float = 30.0,
) -> MyoWorker:
    """Start one worker and return only after streaming is ready."""

    if startup_timeout_s <= 0:
        raise ValueError("startup_timeout_s must be positive.")
    worker = MyoWorker(
        device,
        records,
        stop_event,
        connect_timeout_s=connect_timeout_s,
    )
    worker.start()
    if not worker.startup_event.wait(startup_timeout_s):
        worker.stop_event.set()
        worker.join(5.0)
        if worker.is_alive():
            raise MyoStartupTimeout(
                "Myo startup timed out and cleanup is still running.", worker
            ) from worker.error
        raise TimeoutError("Myo startup timed out.") from worker.error
    if worker.error is not None:
        worker.join()
        raise RuntimeError("Myo failed to start.") from worker.error
    if worker.stop_event.is_set() or not worker.started_streaming or not worker.is_alive():
        worker.join(5.0)
        if worker.error is not None:
            raise RuntimeError("Myo failed to start.") from worker.error
        if worker.is_alive():
            raise MyoStartupTimeout(
                "Myo stopped during startup and cleanup is still running.", worker
            )
        raise RuntimeError("Myo stopped before startup completed.")
    return worker
