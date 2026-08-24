"""Minimal live Myo -> realtime store -> existing Plot integration."""

from __future__ import annotations

import asyncio
import queue

import dearpygui.dearpygui as dpg
from bleak import BleakScanner

from assembly.acquisition.BLE.myo_ingest import (
    MYO_EMG_STREAM_ID,
    MYO_IMU_STREAM_ID,
    MYO_STREAM_SCHEMAS,
    MyoRecordIngestor,
)
from assembly.acquisition.BLE.myo_worker import (
    MyoRecord,
    MyoWorker,
    start_myo,
)
from assembly.plot.models import SeriesSpec
from assembly.plot.plot_window import create_plot_window
from assembly.plot.realtime_provider import BufferedPlotProvider
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore


# ======================================================================
# HARDWARE CONFIGURATION
# ======================================================================

# TODO(HARDWARE):
# Fill in the BLE address of the physical Myo.
MYO_ADDRESS = "TODO: YOUR_MYO_BLE_ADDRESS"

SCAN_TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 20.0
STARTUP_TIMEOUT_S = 30.0


# ======================================================================
# RUNTIME CONFIGURATION
# ======================================================================

# Plot supports at most 30 seconds currently, so retaining 35 seconds
# gives a small safety margin.
RETENTION_SECONDS = 35.0

# Queue capacity is intentionally finite.
#
# MyoWorker already defines queue-full as an acquisition failure instead
# of silently dropping data.
RECORD_QUEUE_SIZE = 4096

# A GUI frame normally receives only a few Myo notifications.
# This high limit allows catching up after a temporarily slow frame.
MAX_RECORDS_PER_FRAME = 2048


# ======================================================================
# PLOT METADATA
# ======================================================================

MYO_PLOT_SERIES: tuple[SeriesSpec, ...] = (
    *tuple(
        SeriesSpec(
            series_id=f"{MYO_EMG_STREAM_ID}/ch{channel}",
            stream_id=MYO_EMG_STREAM_ID,
            field_key=f"emg_ch{channel}_code",
            label=f"EMG CH {channel}",
            unit="code",
            signal_kind="emg",
            default_plot=True,
            fixed_range=(-128.0, 127.0),
        )
        for channel in range(1, 9)
    ),

    SeriesSpec(
        series_id=f"{MYO_IMU_STREAM_ID}/quat_w",
        stream_id=MYO_IMU_STREAM_ID,
        field_key="quat_w",
        label="Quaternion W",
        unit="",
        signal_kind="quaternion",
        default_plot=False,
        fixed_range=(-1.0, 1.0),
    ),
    SeriesSpec(
        series_id=f"{MYO_IMU_STREAM_ID}/quat_x",
        stream_id=MYO_IMU_STREAM_ID,
        field_key="quat_x",
        label="Quaternion X",
        unit="",
        signal_kind="quaternion",
        default_plot=False,
        fixed_range=(-1.0, 1.0),
    ),
    SeriesSpec(
        series_id=f"{MYO_IMU_STREAM_ID}/quat_y",
        stream_id=MYO_IMU_STREAM_ID,
        field_key="quat_y",
        label="Quaternion Y",
        unit="",
        signal_kind="quaternion",
        default_plot=False,
        fixed_range=(-1.0, 1.0),
    ),
    SeriesSpec(
        series_id=f"{MYO_IMU_STREAM_ID}/quat_z",
        stream_id=MYO_IMU_STREAM_ID,
        field_key="quat_z",
        label="Quaternion Z",
        unit="",
        signal_kind="quaternion",
        default_plot=False,
        fixed_range=(-1.0, 1.0),
    ),

    *tuple(
        SeriesSpec(
            series_id=f"{MYO_IMU_STREAM_ID}/accel_{axis}_g",
            stream_id=MYO_IMU_STREAM_ID,
            field_key=f"accel_{axis}_g",
            label=f"Acceleration {axis.upper()}",
            unit="g",
            signal_kind="acceleration",
            default_plot=False,
            fixed_range=(-16.0, 16.0),
        )
        for axis in ("x", "y", "z")
    ),

    *tuple(
        SeriesSpec(
            series_id=f"{MYO_IMU_STREAM_ID}/gyro_{axis}_dps",
            stream_id=MYO_IMU_STREAM_ID,
            field_key=f"gyro_{axis}_dps",
            label=f"Gyroscope {axis.upper()}",
            unit="deg/s",
            signal_kind="angular_velocity",
            default_plot=False,
            fixed_range=(-2048.0, 2048.0),
        )
        for axis in ("x", "y", "z")
    ),
)


async def find_myo():
    if MYO_ADDRESS.startswith("TODO"):
        raise RuntimeError(
            "Fill in MYO_ADDRESS before running the live example."
        )

    device = await BleakScanner.find_device_by_address(
        MYO_ADDRESS,
        timeout=SCAN_TIMEOUT_S,
    )

    if device is None:
        raise RuntimeError(
            f"Could not find Myo at BLE address {MYO_ADDRESS!r}."
        )

    return device


def print_worker_info(worker: MyoWorker) -> None:
    print("\n[Myo]")
    print("started_streaming :", worker.started_streaming)
    print("thread_alive      :", worker.is_alive())
    print("error             :", repr(worker.error))
    print("device_info       :", worker.device_info)


def main() -> None:
    # ------------------------------------------------------------------
    # Resolve hardware before starting the GUI.
    # ------------------------------------------------------------------

    device = asyncio.run(find_myo())

    print("[BLE]")
    print("name   :", device.name)
    print("address:", device.address)

    records: queue.Queue[MyoRecord] = queue.Queue(
        maxsize=RECORD_QUEUE_SIZE
    )

    # ------------------------------------------------------------------
    # Runtime state.
    # ------------------------------------------------------------------

    store = RealtimeStreamStore(
        MYO_STREAM_SCHEMAS,
        retention_seconds=RETENTION_SECONDS,
    )

    ingestor = MyoRecordIngestor(
        records,
        store,
    )

    provider = BufferedPlotProvider(
        store,
        MYO_PLOT_SERIES,
    )

    # ------------------------------------------------------------------
    # Device worker.
    # ------------------------------------------------------------------

    worker: MyoWorker | None = None
    dpg.create_context()

    try:
        worker = start_myo(
            device,
            records=records,
            connect_timeout_s=CONNECT_TIMEOUT_S,
            startup_timeout_s=STARTUP_TIMEOUT_S,
        )

        print_worker_info(worker)

        # --------------------------------------------------------------
        # Existing Plot remains unchanged.
        # --------------------------------------------------------------

        plot_state = create_plot_window(provider)

        dpg.create_viewport(
            title="Live Myo Plot",
            width=1280,
            height=900,
            x_pos=80,
            y_pos=80,
        )

        dpg.setup_dearpygui()
        dpg.show_viewport()

        while dpg.is_dearpygui_running():
            # ----------------------------------------------------------
            # Producer:
            #
            # MyoWorker thread
            #      ↓
            # queue.Queue[MyoRecord]
            #
            # Consumer:
            #
            # current GUI/main thread
            #
            # No additional acquisition thread is needed for this MVP.
            # ----------------------------------------------------------

            ingestor.drain(
                max_records=MAX_RECORDS_PER_FRAME
            )

            # A background hardware failure should terminate this
            # integration instead of leaving a frozen Plot running.
            if worker.stopped_event.is_set():
                break

            plot_state.refresh(provider)
            dpg.render_dearpygui_frame()

    finally:
        close_error: BaseException | None = None

        if worker is not None:
            try:
                worker.close(timeout_s=5.0)
            except BaseException as exc:
                close_error = exc

        dpg.destroy_context()

        if worker is not None:
            print_worker_info(worker)

        if close_error is not None:
            raise close_error


if __name__ == "__main__":
    main()
