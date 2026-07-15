"""Acquisition source selection and configuration window."""

from __future__ import annotations

from typing import cast

import dearpygui.dearpygui as dpg

from fundamental.acquisition import AcquisitionController
from fundamental.app_shell import FundamentalApp
from fundamental.commands import CommandContext, CommandSpec
from fundamental.messages import AcquisitionState
from fundamental.sources.base import SourceName
from fundamental.sources.ble_w2 import BLEW2Source, W2_MODE_NAMES
from fundamental.sources.myo import MyoSource
from fundamental.sources.serial_ads1299 import SerialADS1299Source
from fundamental.window_manager import ManagedWindow


SOURCE_CONFIG_WINDOW_TAG = "fundamental.source_config.window"
SOURCE_SELECT_TAG = "fundamental.source_config.source"
SERIAL_GROUP_TAG = "fundamental.source_config.serial_group"
SERIAL_PORT_INPUT_TAG = "fundamental.source_config.serial_port"
SERIAL_BAUD_INPUT_TAG = "fundamental.source_config.serial_baud"
SERIAL_TIMEOUT_INPUT_TAG = "fundamental.source_config.serial_timeout"
W2_GROUP_TAG = "fundamental.source_config.w2_group"
W2_ADDRESS_INPUT_TAG = "fundamental.source_config.w2_address"
W2_NAME_FILTER_INPUT_TAG = "fundamental.source_config.w2_name_filter"
W2_NOTIFY_UUID_INPUT_TAG = "fundamental.source_config.w2_notify_uuid"
W2_WRITE_UUID_INPUT_TAG = "fundamental.source_config.w2_write_uuid"
W2_MODE_INPUT_TAG = "fundamental.source_config.w2_mode"
W2_SAMPLE_RATE_INPUT_TAG = "fundamental.source_config.w2_sample_rate"
W2_SCAN_TIMEOUT_INPUT_TAG = "fundamental.source_config.w2_scan_timeout"
MYO_GROUP_TAG = "fundamental.source_config.myo_group"
MYO_ADDRESS_INPUT_TAG = "fundamental.source_config.myo_address"
MYO_NAME_FILTER_INPUT_TAG = "fundamental.source_config.myo_name_filter"
MYO_SCAN_TIMEOUT_INPUT_TAG = "fundamental.source_config.myo_scan_timeout"
MYO_CONNECT_TIMEOUT_INPUT_TAG = "fundamental.source_config.myo_connect_timeout"
MYO_ENABLE_EMG_TAG = "fundamental.source_config.myo_enable_emg"
MYO_ENABLE_IMU_TAG = "fundamental.source_config.myo_enable_imu"
SUMMARY_TEXT_TAG = "fundamental.source_config.summary"
INSPECTION_LIST_TAG = "fundamental.source_config.inspection"

SOURCE_LABELS: dict[SourceName, str] = {
    SerialADS1299Source.name: SerialADS1299Source.display_name,
    BLEW2Source.name: BLEW2Source.display_name,
    MyoSource.name: MyoSource.display_name,
}
SOURCE_NAMES_BY_LABEL = {label: name for name, label in SOURCE_LABELS.items()}


def register(app: FundamentalApp, controller: AcquisitionController) -> None:
    app.window_manager.register(
        ManagedWindow(
            tag=SOURCE_CONFIG_WINDOW_TAG,
            title="Source Config",
            build=lambda: _build_window(app, controller),
        )
    )
    app.register_command(
        CommandSpec(
            name="source",
            description="Open acquisition source selection and configuration.",
            handler=lambda context: _open_window(context, controller),
            aliases=("device",),
        )
    )


def _open_window(context: CommandContext, controller: AcquisitionController) -> str | None:
    context.open_window(SOURCE_CONFIG_WINDOW_TAG)
    _sync_window(controller)
    return None


def _build_window(app: FundamentalApp, controller: AcquisitionController) -> None:
    with dpg.window(
        label="Source Config",
        tag=SOURCE_CONFIG_WINDOW_TAG,
        show=False,
        width=680,
        height=590,
        pos=(160, 120),
    ):
        dpg.add_combo(
            tag=SOURCE_SELECT_TAG,
            label="Source",
            items=list(SOURCE_NAMES_BY_LABEL),
            default_value=_source_label(controller.source_name),
            width=260,
            callback=lambda *_: _refresh_source_groups(controller),
        )
        dpg.add_spacer(height=8)

        with dpg.group(tag=SERIAL_GROUP_TAG):
            dpg.add_text("Serial ADS1299")
            dpg.add_input_text(tag=SERIAL_PORT_INPUT_TAG, label="Port", width=260)
            dpg.add_input_int(tag=SERIAL_BAUD_INPUT_TAG, label="Baud", width=260, min_value=1, min_clamped=True)
            dpg.add_input_float(tag=SERIAL_TIMEOUT_INPUT_TAG, label="Timeout (s)", width=260, step=0.01)

        with dpg.group(tag=W2_GROUP_TAG):
            dpg.add_text("BLE W2")
            dpg.add_input_text(tag=W2_ADDRESS_INPUT_TAG, label="Address", width=340)
            dpg.add_input_text(tag=W2_NAME_FILTER_INPUT_TAG, label="Name Filter", width=340)
            dpg.add_input_text(tag=W2_NOTIFY_UUID_INPUT_TAG, label="Notify UUID", width=430)
            dpg.add_input_text(tag=W2_WRITE_UUID_INPUT_TAG, label="Write UUID", width=430)
            dpg.add_combo(tag=W2_MODE_INPUT_TAG, label="Mode", items=list(W2_MODE_NAMES), width=220)
            dpg.add_input_float(tag=W2_SAMPLE_RATE_INPUT_TAG, label="Sample Rate (Hz)", width=220, step=10.0)
            dpg.add_input_float(tag=W2_SCAN_TIMEOUT_INPUT_TAG, label="Scan Timeout (s)", width=220, step=0.5)

        with dpg.group(tag=MYO_GROUP_TAG):
            dpg.add_text("Myo Armband BLE")
            dpg.add_input_text(tag=MYO_ADDRESS_INPUT_TAG, label="Address", width=340)
            dpg.add_input_text(tag=MYO_NAME_FILTER_INPUT_TAG, label="Name Filter", width=340)
            dpg.add_input_float(
                tag=MYO_SCAN_TIMEOUT_INPUT_TAG,
                label="Scan Timeout (s)",
                width=220,
                step=0.5,
            )
            dpg.add_input_float(
                tag=MYO_CONNECT_TIMEOUT_INPUT_TAG,
                label="Connect Timeout (s)",
                width=220,
                step=0.5,
            )
            with dpg.group(horizontal=True):
                dpg.add_checkbox(label="EMG", tag=MYO_ENABLE_EMG_TAG, default_value=True)
                dpg.add_checkbox(label="IMU", tag=MYO_ENABLE_IMU_TAG, default_value=True)

        dpg.add_spacer(height=8)
        dpg.add_button(
            label="Apply",
            width=120,
            callback=lambda *_: _apply_from_window(app, controller),
        )
        dpg.add_spacer(height=8)
        dpg.add_text("", tag=SUMMARY_TEXT_TAG)
        dpg.add_spacer(height=8)
        dpg.add_text("Data Inspection")
        with dpg.child_window(tag=INSPECTION_LIST_TAG, width=-1, height=150, horizontal_scrollbar=True):
            pass

    _sync_window(controller)


def _apply_from_window(app: FundamentalApp, controller: AcquisitionController) -> None:
    source_name = _selected_source_name(controller)
    if source_name != controller.source_name and controller.state != AcquisitionState.STOPPED:
        app.log(controller.select_source(source_name) or "Source unchanged.")
        _sync_window(controller)
        return

    if source_name == SerialADS1299Source.name:
        error = controller.update_serial_config(
            port=str(dpg.get_value(SERIAL_PORT_INPUT_TAG)).strip(),
            baud_rate=int(dpg.get_value(SERIAL_BAUD_INPUT_TAG)),
            timeout_s=float(dpg.get_value(SERIAL_TIMEOUT_INPUT_TAG)),
        )
    elif source_name == BLEW2Source.name:
        error = controller.update_w2_config(
            address=str(dpg.get_value(W2_ADDRESS_INPUT_TAG)).strip(),
            device_name_filter=str(dpg.get_value(W2_NAME_FILTER_INPUT_TAG)).strip(),
            notify_uuid=str(dpg.get_value(W2_NOTIFY_UUID_INPUT_TAG)).strip(),
            write_uuid=str(dpg.get_value(W2_WRITE_UUID_INPUT_TAG)).strip(),
            mode=str(dpg.get_value(W2_MODE_INPUT_TAG)).strip(),
            sample_rate_hz=float(dpg.get_value(W2_SAMPLE_RATE_INPUT_TAG)),
            scan_timeout_s=float(dpg.get_value(W2_SCAN_TIMEOUT_INPUT_TAG)),
        )
    else:
        error = controller.update_myo_config(
            address=str(dpg.get_value(MYO_ADDRESS_INPUT_TAG)).strip(),
            device_name_filter=str(dpg.get_value(MYO_NAME_FILTER_INPUT_TAG)).strip(),
            scan_timeout_s=float(dpg.get_value(MYO_SCAN_TIMEOUT_INPUT_TAG)),
            connect_timeout_s=float(dpg.get_value(MYO_CONNECT_TIMEOUT_INPUT_TAG)),
            enable_emg=bool(dpg.get_value(MYO_ENABLE_EMG_TAG)),
            enable_imu=bool(dpg.get_value(MYO_ENABLE_IMU_TAG)),
        )

    if error:
        app.log(error)
        _sync_window(controller)
        return

    error = controller.select_source(source_name)
    if error:
        app.log(error)
    else:
        app.log(f"Acquisition source updated: {controller.source_display_text()}.")
    _sync_window(controller)


def _sync_window(controller: AcquisitionController) -> None:
    if not dpg.does_item_exist(SOURCE_CONFIG_WINDOW_TAG):
        return

    dpg.set_value(SOURCE_SELECT_TAG, _source_label(controller.source_name))

    serial_config = controller.config
    dpg.set_value(SERIAL_PORT_INPUT_TAG, serial_config.port)
    dpg.set_value(SERIAL_BAUD_INPUT_TAG, serial_config.baud_rate)
    dpg.set_value(SERIAL_TIMEOUT_INPUT_TAG, serial_config.timeout_s)

    w2_config = controller.w2_config
    dpg.set_value(W2_ADDRESS_INPUT_TAG, w2_config.address)
    dpg.set_value(W2_NAME_FILTER_INPUT_TAG, w2_config.device_name_filter)
    dpg.set_value(W2_NOTIFY_UUID_INPUT_TAG, w2_config.notify_uuid)
    dpg.set_value(W2_WRITE_UUID_INPUT_TAG, w2_config.write_uuid)
    dpg.set_value(W2_MODE_INPUT_TAG, w2_config.mode)
    dpg.set_value(W2_SAMPLE_RATE_INPUT_TAG, w2_config.sample_rate_hz)
    dpg.set_value(W2_SCAN_TIMEOUT_INPUT_TAG, w2_config.scan_timeout_s)

    myo_config = controller.myo_config
    dpg.set_value(MYO_ADDRESS_INPUT_TAG, myo_config.address)
    dpg.set_value(MYO_NAME_FILTER_INPUT_TAG, myo_config.device_name_filter)
    dpg.set_value(MYO_SCAN_TIMEOUT_INPUT_TAG, myo_config.scan_timeout_s)
    dpg.set_value(MYO_CONNECT_TIMEOUT_INPUT_TAG, myo_config.connect_timeout_s)
    dpg.set_value(MYO_ENABLE_EMG_TAG, myo_config.enable_emg)
    dpg.set_value(MYO_ENABLE_IMU_TAG, myo_config.enable_imu)

    dpg.set_value(SUMMARY_TEXT_TAG, f"Active: {controller.source_display_text()}")
    _refresh_source_groups(controller)


def _refresh_source_groups(controller: AcquisitionController) -> None:
    selected = _selected_source_name(controller)
    _configure_if_exists(SERIAL_GROUP_TAG, show=selected == SerialADS1299Source.name)
    _configure_if_exists(W2_GROUP_TAG, show=selected == BLEW2Source.name)
    _configure_if_exists(MYO_GROUP_TAG, show=selected == MyoSource.name)
    _refresh_inspection(controller)


def _refresh_inspection(controller: AcquisitionController) -> None:
    if not dpg.does_item_exist(INSPECTION_LIST_TAG):
        return

    selected = _selected_source_name(controller)
    source = controller.configured_source(selected)
    dpg.delete_item(INSPECTION_LIST_TAG, children_only=True)
    for line in source.inspect_data():
        dpg.add_text(line, parent=INSPECTION_LIST_TAG)


def _selected_source_name(controller: AcquisitionController) -> SourceName:
    if not dpg.does_item_exist(SOURCE_SELECT_TAG):
        return controller.source_name
    label = str(dpg.get_value(SOURCE_SELECT_TAG)).strip()
    return cast(SourceName, SOURCE_NAMES_BY_LABEL.get(label, controller.source_name))


def _source_label(source_name: SourceName) -> str:
    return SOURCE_LABELS[source_name]


def _configure_if_exists(tag: str, **kwargs) -> None:
    if dpg.does_item_exist(tag):
        dpg.configure_item(tag, **kwargs)
