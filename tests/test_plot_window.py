from __future__ import annotations

import unittest
from unittest.mock import patch

import dearpygui.dearpygui as dpg

import fundamental.plot_window as plot_window
from fundamental.app_shell import FundamentalApp
from fundamental.capture_store import CaptureStore
from fundamental.plot_window import PlotWindowState
from fundamental.sources.ble_w2 import BLEW2Source
from fundamental.sources.bwt901 import BWT901Source
from fundamental.sources.serial_ads1299 import ADS1299_STREAM_SPEC
from fundamental.streams import FieldSpec, StreamSpec


class PlotWindowStateTests(unittest.TestCase):
    def test_state_starts_without_a_preinitialized_schema(self) -> None:
        state = PlotWindowState()

        self.assertEqual(state.slots, [])
        self.assertIsNone(state.catalog_signature)

        store = CaptureStore(stream_specs=(ADS1299_STREAM_SPEC,))
        self.assertTrue(state.sync_catalog(store))
        self.assertEqual(
            [slot.series_id for slot in state.slots],
            [series.series_id for series in store.series_specs() if series.default_plot],
        )
        self.assertTrue(all(slot.signal_view == "Raw" for slot in state.slots))

    def test_catalog_change_detects_schema_metadata_with_the_same_series_id(self) -> None:
        first = StreamSpec(
            stream_id="sensor.signal",
            display_name="First",
            nominal_rate_hz=100.0,
            fields=(
                FieldSpec(
                    "value",
                    "EMG",
                    unit="code",
                    signal_kind="emg",
                    default_plot=True,
                ),
            ),
        )
        second = StreamSpec(
            stream_id="sensor.signal",
            display_name="Second",
            nominal_rate_hz=50.0,
            fields=(
                FieldSpec(
                    "value",
                    "Acceleration",
                    unit="g",
                    signal_kind="acceleration",
                    default_plot=True,
                ),
            ),
        )
        store = CaptureStore(stream_specs=(first,))
        state = PlotWindowState()

        self.assertTrue(state.sync_catalog(store))
        first_signature = state.catalog_signature
        store.configure_streams((second,), clear=True)

        self.assertTrue(state.sync_catalog(store))
        self.assertNotEqual(state.catalog_signature, first_signature)
        self.assertEqual(state.slots[0].series_id, "sensor.signal/value")
        self.assertEqual(state.slots[0].signal_view, "Raw")


class PlotWindowLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        dpg.create_context()

    def tearDown(self) -> None:
        dpg.destroy_context()

    def test_first_open_after_catalog_change_rebuilds_once(self) -> None:
        app = FundamentalApp()
        store = CaptureStore(stream_specs=(ADS1299_STREAM_SPEC,))
        plot_window.register(app, store)

        # Reproduce a closed Plot observing the initial catalog, followed by a
        # source change before the window's first lazy build.
        app._run_frame_callbacks()
        current_specs = (
            *BLEW2Source().stream_specs(),
            *BWT901Source().stream_specs(),
        )
        store.configure_streams(current_specs, clear=True)

        with patch(
            "fundamental.plot_window._rebuild_slot_list",
            wraps=plot_window._rebuild_slot_list,
        ) as rebuild:
            app.execute_command("plot")

        self.assertEqual(rebuild.call_count, 1)
        self.assertTrue(dpg.does_item_exist(plot_window.PLOT_WINDOW_TAG))
        self.assertEqual(
            dpg.get_value(plot_window.PLOT_COUNT_TAG),
            "5 / 16 slots",
        )


if __name__ == "__main__":
    unittest.main()
