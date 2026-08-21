from __future__ import annotations

import unittest

from assembly.acquisition.runtime.stream_store import RealtimeStreamStore, StreamSchema
from assembly.plot.models import SeriesSpec
from assembly.plot.realtime_provider import BufferedPlotProvider
from assembly.plot.simulated_provider import SimulatedPlotProvider


class RealtimeStreamStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = StreamSchema("test.stream", ("left", "right"), 2.0)
        self.store = RealtimeStreamStore((self.schema,), retention_seconds=2.0)

    def test_schema_rejects_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            StreamSchema("", ("value",), 1.0)
        with self.assertRaises(ValueError):
            StreamSchema("stream", ("value", "value"), 1.0)
        with self.assertRaises(ValueError):
            StreamSchema("stream", ("value",), 0.0)

    def test_append_and_window_preserve_order_and_values(self) -> None:
        for sample_index in range(6):
            self.store.append(
                "test.stream",
                sample_index=sample_index,
                host_monotonic_ns=sample_index * 10,
                host_unix_ns=sample_index * 20,
                values=(sample_index, sample_index + 0.5),
            )

        snapshot = self.store.window("test.stream", 1.0)

        self.assertEqual(self.store.row_count, 6)
        self.assertEqual([row.sample_index for row in snapshot.rows], [3, 4, 5])
        self.assertEqual(snapshot.rows[-1].values, (5.0, 5.5))

    def test_append_rejects_non_increasing_indices(self) -> None:
        values = (1.0, 2.0)
        self.store.append(
            "test.stream",
            sample_index=1,
            host_monotonic_ns=1,
            host_unix_ns=1,
            values=values,
        )

        with self.assertRaises(ValueError):
            self.store.append(
                "test.stream",
                sample_index=1,
                host_monotonic_ns=2,
                host_unix_ns=2,
                values=values,
            )


class AssemblyProviderTests(unittest.TestCase):
    def test_buffered_provider_maps_fields_and_nominal_time(self) -> None:
        schema = StreamSchema("test.stream", ("left", "right"), 10.0)
        store = RealtimeStreamStore((schema,))
        spec = SeriesSpec(
            series_id="test.stream/right",
            stream_id="test.stream",
            field_key="right",
            label="Right",
            unit="code",
            signal_kind="emg",
            default_plot=True,
            fixed_range=None,
        )
        provider = BufferedPlotProvider(store, (spec,))
        store.append(
            "test.stream",
            sample_index=20,
            host_monotonic_ns=1,
            host_unix_ns=2,
            values=(3.0, 4.0),
        )

        window = provider.get_series_window(spec.series_id, 1.0)

        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(window.time_s, [2.0])
        self.assertEqual(window.values, [4.0])
        self.assertEqual(provider.latest_series_values(), ((spec, 4.0),))

    def test_buffered_provider_rejects_unknown_fields(self) -> None:
        schema = StreamSchema("test.stream", ("value",), 10.0)
        store = RealtimeStreamStore((schema,))
        spec = SeriesSpec(
            series_id="test.stream/missing",
            stream_id="test.stream",
            field_key="missing",
            label="Missing",
            unit="code",
            signal_kind="generic",
            default_plot=False,
            fixed_range=None,
        )

        with self.assertRaises(ValueError):
            BufferedPlotProvider(store, (spec,))

    def test_simulated_provider_uses_injected_clock(self) -> None:
        now = [5.0]
        provider = SimulatedPlotProvider(clock=lambda: now[0])
        now[0] = 5.125

        window = provider.get_series_window("sim.emg/left_uv", 0.01)

        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(provider.row_count, 139)
        self.assertEqual(provider.stream_count, 2)
        self.assertEqual(len(window.time_s), 11)
        self.assertEqual(len(window.values), 11)
        self.assertEqual(provider.series_spec("missing"), None)


if __name__ == "__main__":
    unittest.main()
