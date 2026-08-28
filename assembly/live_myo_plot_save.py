"""Myo acquisition -> realtime Plot + independent HDF5 SavePanel.

This is a composition harness for the Myo-specific multi-stream shape:

    MyoWorker -> raw queue -> MyoRecordIngestor -> StreamStoreTap
                                                   |-> RealtimeStreamStore -> Plot
                                                   `-> H5StreamRecorder
                                                       (only while recording)

The Myo EMG and IMU streams remain independent normalized streams.  No alignment,
resampling, merging, or derived timing is introduced here.  Existing
``live_myo_plot.py`` remains a standalone Plot tester.
"""

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
from assembly.acquisition.BLE.myo_worker import MyoRecord, MyoWorker, start_myo
from assembly.acquisition.runtime.queue_pump import QueuePump
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore
from assembly.plot.models import SeriesSpec
from assembly.plot.plot_window import create_plot_window
from assembly.plot.realtime_provider import BufferedPlotProvider
from assembly.save.recorder import H5StreamRecorder
from assembly.save.save_panel import SavePanel
from assembly.save.store_tap import StreamStoreTap


# ======================================================================
# HARDWARE CONFIGURATION
# ======================================================================

# Fill in the BLE address of the physical Myo on the hardware machine.
MYO_ADDRESS = "TODO: YOUR_MYO_BLE_ADDRESS"

SCAN_TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 20.0
STARTUP_TIMEOUT_S = 30.0
SHUTDOWN_TIMEOUT_S = 5.0


# ======================================================================
# RUNTIME CONFIGURATION
# ======================================================================

RETENTION_SECONDS = 35.0
RECORD_QUEUE_SIZE = 4096
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
        raise RuntimeError("Fill in MYO_ADDRESS before running the live example.")

    device = await BleakScanner.find_device_by_address(
        MYO_ADDRESS,
        timeout=SCAN_TIMEOUT_S,
    )
    if device is None:
        raise RuntimeError(f"Could not find Myo at BLE address {MYO_ADDRESS!r}.")
    return device


def print_worker_info(worker: MyoWorker) -> None:
    print("\n[Myo]")
    print("started_streaming :", worker.started_streaming)
    print("thread_alive      :", worker.is_alive())
    print("error             :", repr(worker.error))
    print("device_info       :", worker.device_info)


def main() -> None:
    device = asyncio.run(find_myo())

    print("[BLE]")
    print("name   :", device.name)
    print("address:", device.address)

    records: queue.Queue[MyoRecord] = queue.Queue(maxsize=RECORD_QUEUE_SIZE)

    store = RealtimeStreamStore(
        MYO_STREAM_SCHEMAS,
        retention_seconds=RETENTION_SECONDS,
    )
    recorder = H5StreamRecorder()
    tapped_store = StreamStoreTap(store, recorder)

    # MyoRecordIngestor already owns the Myo-specific normalization semantics.
    # StreamStoreTap merely preserves the store surface it needs and mirrors the
    # resulting committed StreamRows while recording is active.
    ingestor = MyoRecordIngestor(tapped_store)  # type: ignore[arg-type]
    queue_pump = QueuePump(records, ingestor.ingest)

    provider = BufferedPlotProvider(
        store,
        MYO_PLOT_SERIES,
    )

    worker: MyoWorker | None = None
    dpg.create_context()
    close_error: BaseException | None = None

    try:
        worker = start_myo(
            device,
            records=records,
            connect_timeout_s=CONNECT_TIMEOUT_S,
            startup_timeout_s=STARTUP_TIMEOUT_S,
        )
        print_worker_info(worker)

        plot_state = create_plot_window(provider)

        with dpg.window(
            label="Save",
            tag="assembly.myo_plot_save.save_window",
            width=520,
            height=300,
            pos=(1220, 80),
        ):
            dpg.add_text(
                "Myo acquisition and Plot run continuously; Save Start/Stop only controls recording."
            )
            dpg.add_separator()
            save_panel = SavePanel(
                recorder,
                MYO_STREAM_SCHEMAS,
                tag_prefix="assembly.myo_plot_save.save",
                default_directory="captures",
                default_filename="myo_capture.h5",
            )
            save_panel.build()

        dpg.create_viewport(
            title="Myo Plot + Save",
            width=1760,
            height=920,
            x_pos=40,
            y_pos=40,
        )
        dpg.setup_dearpygui()
        dpg.show_viewport()

        while dpg.is_dearpygui_running():
            queue_pump.drain(max_items=MAX_RECORDS_PER_FRAME)

            if worker.stopped_event.is_set():
                break

            plot_state.refresh(provider)
            save_panel.refresh()
            dpg.render_dearpygui_frame()

    finally:
        # Stop the producer first, then drain records already emitted by the Myo.
        # If recording is active, keep Recorder open until that final drain ends.
        if worker is not None:
            try:
                worker.close(timeout_s=SHUTDOWN_TIMEOUT_S)
            except BaseException as exc:
                close_error = exc

        try:
            queue_pump.drain(max_items=RECORD_QUEUE_SIZE)
        finally:
            recorder.stop()
            dpg.destroy_context()

        if worker is not None:
            print_worker_info(worker)

        if close_error is not None:
            raise close_error


if __name__ == "__main__":
    main()
