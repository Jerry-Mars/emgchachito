"""Acquisition source selection and bounded multi-device configuration window."""

from __future__ import annotations

from typing import cast

import dearpygui.dearpygui as dpg

from fundamental.acquisition import AcquisitionController
from fundamental.app_shell import FundamentalApp
from fundamental.commands import CommandContext, CommandSpec
from fundamental.messages import AcquisitionState
from fundamental.sources.base import SourceName
from fundamental.sources.ble_w2 import (
    BLEW2Source,
    MAX_W2_DEVICES,
    W2DeviceConfig,
    W2_MODE_NAMES,
    W2_TRANSPORT_NAMES,
)
from fundamental.sources.bwt901 import (
    BWT901DeviceConfig,
    BWT901Source,
    MAX_BWT901_DEVICES,
)
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
W2_DEVICE_LIST_TAG = "fundamental.source_config.w2_devices"
W2_SERIAL_BAUD_INPUT_TAG = "fundamental.source_config.w2_serial_baud"
W2_SERIAL_TIMEOUT_INPUT_TAG = "fundamental.source_config.w2_serial_timeout"
W2_NOTIFY_UUID_INPUT_TAG = "fundamental.source_config.w2_notify_uuid"
W2_WRITE_UUID_INPUT_TAG = "fundamental.source_config.w2_write_uuid"
W2_MODE_INPUT_TAG = "fundamental.source_config.w2_mode"
W2_SAMPLE_RATE_INPUT_TAG = "fundamental.source_config.w2_sample_rate"
W2_SCAN_TIMEOUT_INPUT_TAG = "fundamental.source_config.w2_scan_timeout"
W2_INCLUDE_BWT_TAG = "fundamental.source_config.w2_include_bwt"
BWT_GROUP_TAG = "fundamental.source_config.bwt_group"
BWT_DEVICE_LIST_TAG = "fundamental.source_config.bwt_devices"
BWT_SCAN_TIMEOUT_INPUT_TAG = "fundamental.source_config.bwt_scan_timeout"
MYO_GROUP_TAG = "fundamental.source_config.myo_group"
MYO_ADDRESS_INPUT_TAG = "fundamental.source_config.myo_address"
MYO_NAME_FILTER_INPUT_TAG = "fundamental.source_config.myo_name_filter"
MYO_SCAN_TIMEOUT_INPUT_TAG = "fundamental.source_config.myo_scan_timeout"
MYO_CONNECT_TIMEOUT_INPUT_TAG = "fundamental.source_config.myo_connect_timeout"
MYO_ENABLE_EMG_TAG = "fundamental.source_config.myo_enable_emg"
MYO_ENABLE_IMU_TAG = "fundamental.source_config.myo_enable_imu"
SUMMARY_TEXT_TAG = "fundamental.source_config.summary"
INSPECTION_LIST_TAG = "fundamental.source_config.inspection"
HEALTH_LIST_TAG = "fundamental.source_config.health"

_w2_editor_row_count = 0
_bwt_editor_row_count = 0

SOURCE_LABELS: dict[SourceName, str] = {
    SerialADS1299Source.name: SerialADS1299Source.display_name,
    BLEW2Source.name: BLEW2Source.display_name,
    BWT901Source.name: BWT901Source.display_name,
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
    app.register_frame_callback(lambda _frame_app: _refresh_health(controller))


def _open_window(context: CommandContext, controller: AcquisitionController) -> str | None:
    context.open_window(SOURCE_CONFIG_WINDOW_TAG)
    _sync_window(controller)
    return None


def _build_window(app: FundamentalApp, controller: AcquisitionController) -> None:
    with dpg.window(
        label="Source Config",
        tag=SOURCE_CONFIG_WINDOW_TAG,
        show=False,
        width=820,
        height=820,
        pos=(160, 80),
    ):
        dpg.add_combo(
            tag=SOURCE_SELECT_TAG,
            label="Primary Source",
            items=list(SOURCE_NAMES_BY_LABEL),
            default_value=_source_label(controller.source_name),
            width=280,
            callback=lambda *_: _on_source_selection_changed(controller),
        )
        dpg.add_spacer(height=8)

        with dpg.group(tag=SERIAL_GROUP_TAG):
            dpg.add_text("Serial ADS1299")
            dpg.add_input_text(tag=SERIAL_PORT_INPUT_TAG, label="Port", width=260)
            dpg.add_input_int(
                tag=SERIAL_BAUD_INPUT_TAG,
                label="Baud",
                width=260,
                min_value=1,
                min_clamped=True,
            )
            dpg.add_input_float(
                tag=SERIAL_TIMEOUT_INPUT_TAG,
                label="Timeout (s)",
                width=260,
                step=0.01,
            )

        with dpg.group(tag=W2_GROUP_TAG):
            dpg.add_text(f"W2 Devices (maximum {MAX_W2_DEVICES})")
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Add W2",
                    width=110,
                    callback=lambda *_: _add_w2_device(app),
                )
                dpg.add_checkbox(
                    tag=W2_INCLUDE_BWT_TAG,
                    label="Acquire BWT901 simultaneously",
                    default_value=True,
                    callback=lambda *_: _refresh_source_groups(controller),
                )
            with dpg.child_window(
                tag=W2_DEVICE_LIST_TAG,
                width=-1,
                height=190,
                horizontal_scrollbar=True,
            ):
                pass
            with dpg.group(horizontal=True):
                dpg.add_combo(
                    tag=W2_MODE_INPUT_TAG,
                    label="Mode",
                    items=list(W2_MODE_NAMES),
                    width=180,
                )
                dpg.add_input_float(
                    tag=W2_SAMPLE_RATE_INPUT_TAG,
                    label="Sample Rate (Hz)",
                    width=180,
                    step=10.0,
                )
            with dpg.group(horizontal=True):
                dpg.add_input_int(
                    tag=W2_SERIAL_BAUD_INPUT_TAG,
                    label="Serial Baud",
                    width=180,
                    min_value=1,
                    min_clamped=True,
                )
                dpg.add_input_float(
                    tag=W2_SERIAL_TIMEOUT_INPUT_TAG,
                    label="Serial Timeout (s)",
                    width=180,
                    step=0.01,
                )
                dpg.add_input_float(
                    tag=W2_SCAN_TIMEOUT_INPUT_TAG,
                    label="BLE Scan Timeout (s)",
                    width=180,
                    step=0.5,
                )
            dpg.add_input_text(tag=W2_NOTIFY_UUID_INPUT_TAG, label="BLE Notify UUID", width=430)
            dpg.add_input_text(tag=W2_WRITE_UUID_INPUT_TAG, label="BLE Write UUID", width=430)

        with dpg.group(tag=BWT_GROUP_TAG):
            dpg.add_separator()
            dpg.add_text(f"BWT901BLE Devices (maximum {MAX_BWT901_DEVICES})")
            dpg.add_button(
                label="Add BWT901",
                width=120,
                callback=lambda *_: _add_bwt_device(app),
            )
            with dpg.child_window(
                tag=BWT_DEVICE_LIST_TAG,
                width=-1,
                height=120,
                horizontal_scrollbar=True,
            ):
                pass
            dpg.add_input_float(
                tag=BWT_SCAN_TIMEOUT_INPUT_TAG,
                label="BLE Scan Timeout (s)",
                width=220,
                step=0.5,
            )

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
        dpg.add_text("", tag=SUMMARY_TEXT_TAG)
        dpg.add_text("Device Health")
        with dpg.child_window(tag=HEALTH_LIST_TAG, width=-1, height=100):
            pass
        dpg.add_text("Data Inspection")
        with dpg.child_window(
            tag=INSPECTION_LIST_TAG,
            width=-1,
            height=130,
            horizontal_scrollbar=True,
        ):
            pass

    _sync_window(controller)


def _apply_from_window(app: FundamentalApp, controller: AcquisitionController) -> None:
    source_name = _selected_source_name(controller)
    if controller.state != AcquisitionState.STOPPED:
        app.log("Stop acquisition before changing source configuration.")
        _sync_window(controller)
        return

    if source_name == SerialADS1299Source.name:
        error = controller.update_serial_config(
            port=str(dpg.get_value(SERIAL_PORT_INPUT_TAG)).strip(),
            baud_rate=int(dpg.get_value(SERIAL_BAUD_INPUT_TAG)),
            timeout_s=float(dpg.get_value(SERIAL_TIMEOUT_INPUT_TAG)),
        )
        active = (SerialADS1299Source.name,)
    elif source_name == BLEW2Source.name:
        error = controller.update_w2_config(
            devices=_w2_devices_from_window(),
            notify_uuid=str(dpg.get_value(W2_NOTIFY_UUID_INPUT_TAG)).strip(),
            write_uuid=str(dpg.get_value(W2_WRITE_UUID_INPUT_TAG)).strip(),
            mode=str(dpg.get_value(W2_MODE_INPUT_TAG)).strip(),
            sample_rate_hz=float(dpg.get_value(W2_SAMPLE_RATE_INPUT_TAG)),
            scan_timeout_s=float(dpg.get_value(W2_SCAN_TIMEOUT_INPUT_TAG)),
            serial_baud_rate=int(dpg.get_value(W2_SERIAL_BAUD_INPUT_TAG)),
            serial_timeout_s=float(dpg.get_value(W2_SERIAL_TIMEOUT_INPUT_TAG)),
        )
        include_bwt = bool(dpg.get_value(W2_INCLUDE_BWT_TAG))
        if error is None and include_bwt:
            error = _apply_bwt_config(controller)
        active = (
            (BLEW2Source.name, BWT901Source.name)
            if include_bwt
            else (BLEW2Source.name,)
        )
    elif source_name == BWT901Source.name:
        error = _apply_bwt_config(controller)
        active = (BWT901Source.name,)
    else:
        error = controller.update_myo_config(
            address=str(dpg.get_value(MYO_ADDRESS_INPUT_TAG)).strip(),
            device_name_filter=str(dpg.get_value(MYO_NAME_FILTER_INPUT_TAG)).strip(),
            scan_timeout_s=float(dpg.get_value(MYO_SCAN_TIMEOUT_INPUT_TAG)),
            connect_timeout_s=float(dpg.get_value(MYO_CONNECT_TIMEOUT_INPUT_TAG)),
            enable_emg=bool(dpg.get_value(MYO_ENABLE_EMG_TAG)),
            enable_imu=bool(dpg.get_value(MYO_ENABLE_IMU_TAG)),
        )
        active = (MyoSource.name,)

    if error is None:
        error = controller.select_sources(active)
    if error:
        app.log(error)
        _sync_window(controller)
        return
    app.log(f"Acquisition sources updated: {controller.source_display_text()}.")
    _sync_window(controller)


def _apply_bwt_config(controller: AcquisitionController) -> str | None:
    return controller.update_bwt901_config(
        devices=_bwt_devices_from_window(),
        scan_timeout_s=float(dpg.get_value(BWT_SCAN_TIMEOUT_INPUT_TAG)),
    )


def _sync_window(controller: AcquisitionController) -> None:
    if not dpg.does_item_exist(SOURCE_CONFIG_WINDOW_TAG):
        return
    dpg.set_value(SOURCE_SELECT_TAG, _source_label(controller.source_name))
    serial_config = controller.config
    dpg.set_value(SERIAL_PORT_INPUT_TAG, serial_config.port)
    dpg.set_value(SERIAL_BAUD_INPUT_TAG, serial_config.baud_rate)
    dpg.set_value(SERIAL_TIMEOUT_INPUT_TAG, serial_config.timeout_s)

    w2 = controller.w2_config
    dpg.set_value(W2_NOTIFY_UUID_INPUT_TAG, w2.notify_uuid)
    dpg.set_value(W2_WRITE_UUID_INPUT_TAG, w2.write_uuid)
    dpg.set_value(W2_MODE_INPUT_TAG, w2.mode)
    dpg.set_value(W2_SAMPLE_RATE_INPUT_TAG, w2.sample_rate_hz)
    dpg.set_value(W2_SCAN_TIMEOUT_INPUT_TAG, w2.scan_timeout_s)
    dpg.set_value(W2_SERIAL_BAUD_INPUT_TAG, w2.serial_baud_rate)
    dpg.set_value(W2_SERIAL_TIMEOUT_INPUT_TAG, w2.serial_timeout_s)
    dpg.set_value(W2_INCLUDE_BWT_TAG, BWT901Source.name in controller.active_source_names)
    _set_w2_devices(w2.effective_devices())

    bwt = controller.bwt901_config
    dpg.set_value(BWT_SCAN_TIMEOUT_INPUT_TAG, bwt.scan_timeout_s)
    _set_bwt_devices(bwt.devices)

    myo = controller.myo_config
    dpg.set_value(MYO_ADDRESS_INPUT_TAG, myo.address)
    dpg.set_value(MYO_NAME_FILTER_INPUT_TAG, myo.device_name_filter)
    dpg.set_value(MYO_SCAN_TIMEOUT_INPUT_TAG, myo.scan_timeout_s)
    dpg.set_value(MYO_CONNECT_TIMEOUT_INPUT_TAG, myo.connect_timeout_s)
    dpg.set_value(MYO_ENABLE_EMG_TAG, myo.enable_emg)
    dpg.set_value(MYO_ENABLE_IMU_TAG, myo.enable_imu)

    dpg.set_value(SUMMARY_TEXT_TAG, f"Active: {controller.source_display_text()}")
    _refresh_source_groups(controller)
    _refresh_health(controller)


def _refresh_source_groups(controller: AcquisitionController) -> None:
    selected = _selected_source_name(controller)
    show_w2 = selected == BLEW2Source.name
    show_bwt = selected == BWT901Source.name or (
        show_w2
        and dpg.does_item_exist(W2_INCLUDE_BWT_TAG)
        and bool(dpg.get_value(W2_INCLUDE_BWT_TAG))
    )
    _configure_if_exists(SERIAL_GROUP_TAG, show=selected == SerialADS1299Source.name)
    _configure_if_exists(W2_GROUP_TAG, show=show_w2)
    _configure_if_exists(BWT_GROUP_TAG, show=show_bwt)
    _configure_if_exists(MYO_GROUP_TAG, show=selected == MyoSource.name)
    _refresh_inspection(controller)


def _on_source_selection_changed(controller: AcquisitionController) -> None:
    if (
        _selected_source_name(controller) == BLEW2Source.name
        and controller.source_name != BLEW2Source.name
        and dpg.does_item_exist(W2_INCLUDE_BWT_TAG)
    ):
        dpg.set_value(W2_INCLUDE_BWT_TAG, True)
    _refresh_source_groups(controller)


def _w2_field_tag(index: int, field_name: str) -> str:
    return f"fundamental.source_config.w2.{index}.{field_name}"


def _w2_devices_from_window() -> tuple[W2DeviceConfig, ...]:
    devices: list[W2DeviceConfig] = []
    for index in range(_w2_editor_row_count):
        id_tag = _w2_field_tag(index, "device_id")
        if not dpg.does_item_exist(id_tag):
            continue
        devices.append(
            W2DeviceConfig(
                device_id=str(dpg.get_value(id_tag)).strip(),
                transport=cast(str, dpg.get_value(_w2_field_tag(index, "transport"))),
                port=str(dpg.get_value(_w2_field_tag(index, "port"))).strip(),
                address=str(dpg.get_value(_w2_field_tag(index, "address"))).strip(),
                device_name_filter=str(
                    dpg.get_value(_w2_field_tag(index, "name_filter"))
                ).strip(),
            ).normalized()
        )
    return tuple(devices)


def _set_w2_devices(devices: tuple[W2DeviceConfig, ...]) -> None:
    global _w2_editor_row_count
    if not dpg.does_item_exist(W2_DEVICE_LIST_TAG):
        return
    normalized = tuple(device.normalized() for device in devices)
    dpg.delete_item(W2_DEVICE_LIST_TAG, children_only=True)
    _w2_editor_row_count = len(normalized)
    for index, device in enumerate(normalized):
        with dpg.group(horizontal=True, parent=W2_DEVICE_LIST_TAG):
            dpg.add_input_text(
                tag=_w2_field_tag(index, "device_id"),
                label="ID",
                default_value=device.device_id,
                width=90,
            )
            dpg.add_combo(
                tag=_w2_field_tag(index, "transport"),
                label="Interface",
                items=list(W2_TRANSPORT_NAMES),
                default_value=device.transport,
                width=90,
            )
            dpg.add_input_text(
                tag=_w2_field_tag(index, "port"),
                label="Port",
                default_value=device.port,
                width=80,
            )
            dpg.add_input_text(
                tag=_w2_field_tag(index, "address"),
                label="BLE Address",
                default_value=device.address,
                width=145,
            )
            dpg.add_input_text(
                tag=_w2_field_tag(index, "name_filter"),
                label="Name",
                default_value=device.device_name_filter,
                width=100,
            )
            dpg.add_button(
                label="Remove",
                user_data=index,
                callback=lambda _s, _a, row: _remove_w2_device(int(row)),
            )


def _add_w2_device(app: FundamentalApp) -> None:
    devices = list(_w2_devices_from_window())
    if len(devices) >= MAX_W2_DEVICES:
        app.log(f"At most {MAX_W2_DEVICES} W2 devices can be configured.")
        return
    used = {device.device_id.casefold() for device in devices}
    index = 1
    while f"w2_{index}".casefold() in used:
        index += 1
    device_id = f"w2_{index}"
    devices.append(W2DeviceConfig(device_id=device_id, port=""))
    _set_w2_devices(tuple(devices))
    app.log(f"Added {device_id}; select its interface and connection target.")


def _remove_w2_device(index: int) -> None:
    devices = list(_w2_devices_from_window())
    if 0 <= index < len(devices):
        del devices[index]
        _set_w2_devices(tuple(devices))


def _bwt_field_tag(index: int, field_name: str) -> str:
    return f"fundamental.source_config.bwt.{index}.{field_name}"


def _bwt_devices_from_window() -> tuple[BWT901DeviceConfig, ...]:
    devices: list[BWT901DeviceConfig] = []
    for index in range(_bwt_editor_row_count):
        id_tag = _bwt_field_tag(index, "device_id")
        if not dpg.does_item_exist(id_tag):
            continue
        devices.append(
            BWT901DeviceConfig(
                device_id=str(dpg.get_value(id_tag)).strip(),
                address=str(dpg.get_value(_bwt_field_tag(index, "address"))).strip(),
                name_filter=str(dpg.get_value(_bwt_field_tag(index, "name_filter"))).strip(),
            ).normalized()
        )
    return tuple(devices)


def _set_bwt_devices(devices: tuple[BWT901DeviceConfig, ...]) -> None:
    global _bwt_editor_row_count
    if not dpg.does_item_exist(BWT_DEVICE_LIST_TAG):
        return
    normalized = tuple(device.normalized() for device in devices)
    dpg.delete_item(BWT_DEVICE_LIST_TAG, children_only=True)
    _bwt_editor_row_count = len(normalized)
    for index, device in enumerate(normalized):
        with dpg.group(horizontal=True, parent=BWT_DEVICE_LIST_TAG):
            dpg.add_input_text(
                tag=_bwt_field_tag(index, "device_id"),
                label="ID",
                default_value=device.device_id,
                width=100,
            )
            dpg.add_input_text(
                tag=_bwt_field_tag(index, "address"),
                label="BLE Address",
                default_value=device.address,
                width=180,
            )
            dpg.add_input_text(
                tag=_bwt_field_tag(index, "name_filter"),
                label="Name",
                default_value=device.name_filter,
                width=100,
            )
            dpg.add_button(
                label="Remove",
                user_data=index,
                callback=lambda _s, _a, row: _remove_bwt_device(int(row)),
            )


def _add_bwt_device(app: FundamentalApp) -> None:
    devices = list(_bwt_devices_from_window())
    if len(devices) >= MAX_BWT901_DEVICES:
        app.log(f"At most {MAX_BWT901_DEVICES} BWT901 devices can be configured.")
        return
    used = {device.device_id.casefold() for device in devices}
    index = 1
    while f"imu_{index}".casefold() in used:
        index += 1
    device_id = f"imu_{index}"
    devices.append(BWT901DeviceConfig(device_id=device_id, address=""))
    _set_bwt_devices(tuple(devices))
    app.log(f"Added {device_id}; enter its BLE address before applying.")


def _remove_bwt_device(index: int) -> None:
    devices = list(_bwt_devices_from_window())
    if 0 <= index < len(devices):
        del devices[index]
        _set_bwt_devices(tuple(devices))


def _refresh_health(controller: AcquisitionController) -> None:
    if not dpg.does_item_exist(HEALTH_LIST_TAG):
        return
    dpg.delete_item(HEALTH_LIST_TAG, children_only=True)
    lines = controller.health_lines() or ("No device status yet.",)
    for line in lines:
        dpg.add_text(line, parent=HEALTH_LIST_TAG)


def _refresh_inspection(controller: AcquisitionController) -> None:
    if not dpg.does_item_exist(INSPECTION_LIST_TAG):
        return
    selected = _selected_source_name(controller)
    names: tuple[SourceName, ...] = (selected,)
    if (
        selected == BLEW2Source.name
        and dpg.does_item_exist(W2_INCLUDE_BWT_TAG)
        and bool(dpg.get_value(W2_INCLUDE_BWT_TAG))
    ):
        names = (BLEW2Source.name, BWT901Source.name)
    dpg.delete_item(INSPECTION_LIST_TAG, children_only=True)
    for name in names:
        for line in controller.configured_source(name).inspect_data():
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
