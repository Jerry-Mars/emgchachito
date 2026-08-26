from __future__ import annotations

import queue
import unittest

from assembly.acquisition.BLE.myo_ingest import (
    MYO_EMG_STREAM_ID,
    MYO_IMU_STREAM_ID,
    MYO_STREAM_SCHEMAS,
    MyoRecordIngestor,
)
from assembly.acquisition.runtime.queue_pump import QueuePump
from assembly.acquisition.runtime.stream_store import (
    RealtimeStreamStore,
    StreamSample,
    StreamSchema,
)
from assembly.plot.models import SeriesSpec, SeriesWindow
from assembly.plot.plot_window import _relative_display_time
from assembly.plot.realtime_provider import BufferedPlotProvider
from assembly.plot.simulated_provider import SimulatedPlotProvider


NS = 1_000_000_000


class RealtimeStreamStoreTests(unittest.TestCase):
    def test_schema_allows_unknown_nominal_rate(self) -> None:
        schema = StreamSchema("imu", ("value",), None)
        self.assertIsNone(schema.nominal_rate_hz)

        with self.assertRaises(ValueError):
            StreamSchema("", ("value",), 1.0)
        with self.assertRaises(ValueError):
            StreamSchema("stream", ("value", "value"), 1.0)
        with self.assertRaises(ValueError):
            StreamSchema("stream", ("value",), 0.0)

    def test_store_owns_runtime_index_and_batch_preserves_observation_time(self) -> None:
        schema = StreamSchema("emg", ("value",), 200.0)
        store = RealtimeStreamStore((schema,))

        rows = store.append_batch(
            "emg",
            (
                StreamSample(10 * NS, 100 * NS, (1.0,)),
                StreamSample(10 * NS, 100 * NS, (2.0,)),
            ),
        )
        third = store.append(
            "emg",
            host_monotonic_ns=10 * NS + 5_000_000,
            host_unix_ns=100 * NS + 5_000_000,
            values=(3.0,),
        )

        self.assertEqual([row.runtime_index for row in rows], [0, 1])
        self.assertEqual(third.runtime_index, 2)
        self.assertEqual(rows[0].host_monotonic_ns, rows[1].host_monotonic_ns)

    def test_window_is_host_time_query_using_shared_store_reference(self) -> None:
        regular = StreamSchema("regular", ("value",), 10.0)
        irregular = StreamSchema("irregular", ("value",), None)
        store = RealtimeStreamStore((regular, irregular), retention_seconds=10.0)

        store.append(
            "regular",
            host_monotonic_ns=1 * NS,
            host_unix_ns=101 * NS,
            values=(1.0,),
        )
        store.append(
            "regular",
            host_monotonic_ns=2 * NS,
            host_unix_ns=102 * NS,
            values=(2.0,),
        )
        store.append(
            "irregular",
            host_monotonic_ns=3 * NS,
            host_unix_ns=103 * NS,
            values=(3.0,),
        )

        snapshot = store.window("regular", 1.5)

        self.assertEqual(snapshot.reference_monotonic_ns, 3 * NS)
        self.assertEqual([row.values[0] for row in snapshot.rows], [2.0])

    def test_tail_samples_is_explicit_count_query(self) -> None:
        schema = StreamSchema("stream", ("value",), None)
        store = RealtimeStreamStore((schema,))
        for index in range(5):
            store.append(
                "stream",
                host_monotonic_ns=index * NS,
                host_unix_ns=(100 + index) * NS,
                values=(float(index),),
            )

        snapshot = store.tail_samples("stream", 2)

        self.assertEqual([row.runtime_index for row in snapshot.rows], [3, 4])
        self.assertEqual([row.values[0] for row in snapshot.rows], [3.0, 4.0])

    def test_retention_is_time_based_across_streams(self) -> None:
        first = StreamSchema("first", ("value",), 1000.0)
        second = StreamSchema("second", ("value",), None)
        store = RealtimeStreamStore((first, second), retention_seconds=2.0)

        store.append(
            "first",
            host_monotonic_ns=1 * NS,
            host_unix_ns=101 * NS,
            values=(1.0,),
        )
        store.append(
            "second",
            host_monotonic_ns=4 * NS,
            host_unix_ns=104 * NS,
            values=(4.0,),
        )

        self.assertEqual(store.tail_samples("first", 10).rows, ())
        self.assertEqual(store.row_count, 2)  # lifetime ingestion count

    def test_max_rows_is_safety_boundary(self) -> None:
        schema = StreamSchema("stream", ("value",), None)
        store = RealtimeStreamStore(
            (schema,),
            retention_seconds=100.0,
            max_rows_per_stream=2,
        )
        for index in range(3):
            store.append(
                "stream",
                host_monotonic_ns=index,
                host_unix_ns=index,
                values=(float(index),),
            )

        self.assertEqual(
            [row.runtime_index for row in store.tail_samples("stream", 10).rows],
            [1, 2],
        )

    def test_store_rejects_backward_host_observation_time(self) -> None:
        schema = StreamSchema("stream", ("value",), None)
        store = RealtimeStreamStore((schema,))
        store.append(
            "stream",
            host_monotonic_ns=10,
            host_unix_ns=10,
            values=(1.0,),
        )

        with self.assertRaises(ValueError):
            store.append(
                "stream",
                host_monotonic_ns=9,
                host_unix_ns=11,
                values=(2.0,),
            )


class QueuePumpTests(unittest.TestCase):
    def test_queue_pump_only_owns_generic_queue_draining(self) -> None:
        source: queue.Queue[int] = queue.Queue()
        for value in (1, 2, 3):
            source.put(value)

        received: list[int] = []
        pump = QueuePump(source, received.append)

        self.assertEqual(pump.drain(max_items=2), 2)
        self.assertEqual(received, [1, 2])
        self.assertEqual(source.qsize(), 1)
        self.assertEqual(pump.drain(), 1)
        self.assertEqual(received, [1, 2, 3])


class MyoRecordIngestorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = RealtimeStreamStore(MYO_STREAM_SCHEMAS)
        self.ingestor = MyoRecordIngestor(self.store)

    def test_emg_worker_counter_does_not_define_runtime_index(self) -> None:
        self.ingestor.ingest(
            {
                "stream": "emg",
                "notification_index": 500,
                "host_monotonic_ns": 10 * NS,
                "host_unix_ns": 100 * NS,
                "samples": (
                    tuple(range(8)),
                    tuple(range(8, 16)),
                ),
            }
        )

        rows = self.store.tail_samples(MYO_EMG_STREAM_ID, 10).rows
        self.assertEqual([row.runtime_index for row in rows], [0, 1])
        self.assertEqual(rows[0].host_monotonic_ns, rows[1].host_monotonic_ns)
        self.assertEqual(rows[1].values[-1], 15.0)

    def test_imu_worker_counter_does_not_define_runtime_index(self) -> None:
        self.ingestor.ingest(
            {
                "stream": "imu",
                "sample_index": 123,
                "host_monotonic_ns": 20 * NS,
                "host_unix_ns": 120 * NS,
                "quaternion": (1.0, 0.0, 0.0, 0.0),
                "accelerometer_g": (0.1, 0.2, 0.3),
                "gyroscope_dps": (1.0, 2.0, 3.0),
            }
        )

        row = self.store.latest(MYO_IMU_STREAM_ID)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.runtime_index, 0)
        self.assertEqual(row.values[-1], 3.0)


class AssemblyProviderTests(unittest.TestCase):
    @staticmethod
    def _spec(stream_id: str, field_key: str = "value") -> SeriesSpec:
        return SeriesSpec(
            series_id=f"{stream_id}/{field_key}",
            stream_id=stream_id,
            field_key=field_key,
            label=field_key,
            unit="code",
            signal_kind="generic",
            default_plot=True,
            fixed_range=None,
        )

    def test_regular_stream_uses_nominal_spacing_anchored_to_host_time(self) -> None:
        schema = StreamSchema("regular", ("value",), 10.0)
        store = RealtimeStreamStore((schema,))
        spec = self._spec("regular")
        provider = BufferedPlotProvider(store, (spec,))

        store.append_batch(
            "regular",
            (
                StreamSample(10 * NS, 100 * NS, (1.0,)),
                StreamSample(10 * NS, 100 * NS, (2.0,)),
            ),
        )

        window = provider.get_series_window(spec.series_id, 1.0)

        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(window.values, [1.0, 2.0])
        self.assertAlmostEqual(window.time_s[0], 9.9)
        self.assertAlmostEqual(window.time_s[1], 10.0)
        self.assertAlmostEqual(window.reference_time_s or -1.0, 10.0)

    def test_unknown_rate_stream_uses_host_observation_time(self) -> None:
        schema = StreamSchema("irregular", ("value",), None)
        store = RealtimeStreamStore((schema,))
        spec = self._spec("irregular")
        provider = BufferedPlotProvider(store, (spec,))

        store.append(
            "irregular",
            host_monotonic_ns=10 * NS,
            host_unix_ns=100 * NS,
            values=(1.0,),
        )
        store.append(
            "irregular",
            host_monotonic_ns=10 * NS + 30_000_000,
            host_unix_ns=100 * NS + 30_000_000,
            values=(2.0,),
        )

        window = provider.get_series_window(spec.series_id, 1.0)

        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(window.time_s, [10.0, 10.03])
        self.assertAlmostEqual(window.reference_time_s or -1.0, 10.03)

    def test_multiple_streams_share_plot_reference(self) -> None:
        regular = StreamSchema("regular", ("value",), 10.0)
        irregular = StreamSchema("irregular", ("value",), None)
        store = RealtimeStreamStore((regular, irregular))
        regular_spec = self._spec("regular")
        irregular_spec = self._spec("irregular")
        provider = BufferedPlotProvider(store, (regular_spec, irregular_spec))

        store.append(
            "regular",
            host_monotonic_ns=10 * NS,
            host_unix_ns=100 * NS,
            values=(1.0,),
        )
        store.append(
            "irregular",
            host_monotonic_ns=10 * NS + 50_000_000,
            host_unix_ns=100 * NS + 50_000_000,
            values=(2.0,),
        )

        regular_window = provider.get_series_window(regular_spec.series_id, 1.0)
        irregular_window = provider.get_series_window(irregular_spec.series_id, 1.0)
        assert regular_window is not None and irregular_window is not None

        self.assertEqual(regular_window.reference_time_s, irregular_window.reference_time_s)
        self.assertAlmostEqual(_relative_display_time(regular_window)[-1], -0.05)
        self.assertAlmostEqual(_relative_display_time(irregular_window)[-1], 0.0)

    def test_buffered_provider_rejects_unknown_fields(self) -> None:
        schema = StreamSchema("test.stream", ("value",), 10.0)
        store = RealtimeStreamStore((schema,))
        spec = self._spec("test.stream", "missing")

        with self.assertRaises(ValueError):
            BufferedPlotProvider(store, (spec,))

    def test_plot_relative_time_falls_back_to_series_latest(self) -> None:
        spec = self._spec("test")
        window = SeriesWindow(spec, [1.0, 2.0], [3.0, 4.0])
        self.assertEqual(_relative_display_time(window), [-1.0, 0.0])

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
