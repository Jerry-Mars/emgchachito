from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import assembly.live_myo_plot as live_myo_plot
from assembly.acquisition.runtime.stream_store import RealtimeStreamStore
from assembly.plot.realtime_provider import BufferedPlotProvider


class LiveMyoPlotTests(unittest.TestCase):
    def test_plot_series_match_runtime_stream_schemas(self) -> None:
        store = RealtimeStreamStore(live_myo_plot.MYO_STREAM_SCHEMAS)
        provider = BufferedPlotProvider(store, live_myo_plot.MYO_PLOT_SERIES)

        self.assertEqual(len(provider.series_specs()), 18)
        self.assertEqual(provider.stream_count, 2)

    def test_find_myo_rejects_placeholder_without_scanning(self) -> None:
        find_device = AsyncMock()

        with patch.object(
            live_myo_plot.BleakScanner,
            "find_device_by_address",
            find_device,
        ):
            with self.assertRaisesRegex(RuntimeError, "Fill in MYO_ADDRESS"):
                asyncio.run(live_myo_plot.find_myo())

        find_device.assert_not_awaited()

    def test_find_myo_returns_device_from_configured_address(self) -> None:
        device = object()
        find_device = AsyncMock(return_value=device)

        with (
            patch.object(live_myo_plot, "MYO_ADDRESS", "AA:BB:CC:DD:EE:FF"),
            patch.object(
                live_myo_plot.BleakScanner,
                "find_device_by_address",
                find_device,
            ),
        ):
            result = asyncio.run(live_myo_plot.find_myo())

        self.assertIs(result, device)
        find_device.assert_awaited_once_with(
            "AA:BB:CC:DD:EE:FF",
            timeout=live_myo_plot.SCAN_TIMEOUT_S,
        )


if __name__ == "__main__":
    unittest.main()
