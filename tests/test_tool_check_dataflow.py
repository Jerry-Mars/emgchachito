from __future__ import annotations

import unittest

from assembly.tool.Tool_Check_DataFlow import DataFlowMonitor, _format_gap


class DataFlowMonitorTests(unittest.TestCase):
    def test_one_observation_is_one_point_and_one_record(self) -> None:
        monitor = DataFlowMonitor(("w2.a",), retention_seconds=10.0)
        monitor.observe("w2.a", 1_000_000_000)

        state = monitor.state("w2.a")
        x, y = monitor.relative_points("w2.a", origin_monotonic_ns=0)

        self.assertEqual(state.total_records, 1)
        self.assertEqual(x, [1.0])
        self.assertEqual(y, [0.0])
        self.assertIsNone(state.last_gap_ns)
        self.assertIsNone(state.max_gap_ns)

    def test_last_and_max_gap_follow_arrival_timestamps(self) -> None:
        monitor = DataFlowMonitor(("source",), retention_seconds=10.0)
        monitor.observe("source", 1_000_000_000)
        monitor.observe("source", 1_010_000_000)
        monitor.observe("source", 1_110_000_000)
        monitor.observe("source", 1_130_000_000)

        state = monitor.state("source")
        self.assertEqual(state.total_records, 4)
        self.assertEqual(state.last_gap_ns, 20_000_000)
        self.assertEqual(state.max_gap_ns, 100_000_000)

    def test_rolling_window_prunes_points_but_keeps_lifetime_statistics(self) -> None:
        monitor = DataFlowMonitor(("source",), retention_seconds=2.0)
        monitor.observe("source", 0)
        monitor.observe("source", 1_000_000_000)
        monitor.observe("source", 2_000_000_000)
        monitor.observe("source", 4_000_000_000)
        monitor.prune(4_000_000_000)

        state = monitor.state("source")
        self.assertEqual(list(state.arrivals_ns), [2_000_000_000, 4_000_000_000])
        self.assertEqual(state.total_records, 4)
        self.assertEqual(state.max_gap_ns, 2_000_000_000)

    def test_sources_keep_independent_lanes_and_statistics(self) -> None:
        monitor = DataFlowMonitor(("a", "b"), retention_seconds=5.0)
        monitor.observe("a", 1_000_000_000)
        monitor.observe("a", 1_010_000_000)
        monitor.observe("b", 1_000_000_000)
        monitor.observe("b", 1_500_000_000)

        self.assertEqual(monitor.state("a").lane, 0.0)
        self.assertEqual(monitor.state("b").lane, 1.0)
        self.assertEqual(monitor.state("a").max_gap_ns, 10_000_000)
        self.assertEqual(monitor.state("b").max_gap_ns, 500_000_000)

    def test_equal_host_timestamps_are_allowed(self) -> None:
        monitor = DataFlowMonitor(("source",), retention_seconds=5.0)
        monitor.observe("source", 1_000_000_000)
        monitor.observe("source", 1_000_000_000)

        state = monitor.state("source")
        self.assertEqual(state.total_records, 2)
        self.assertEqual(state.last_gap_ns, 0)

    def test_backwards_time_is_rejected_per_source(self) -> None:
        monitor = DataFlowMonitor(("source",), retention_seconds=5.0)
        monitor.observe("source", 2_000_000_000)
        with self.assertRaises(ValueError):
            monitor.observe("source", 1_000_000_000)

    def test_gap_format_is_compact(self) -> None:
        self.assertEqual(_format_gap(None), "-")
        self.assertEqual(_format_gap(12_300_000), "12.3 ms")
        self.assertEqual(_format_gap(1_500_000_000), "1.50 s")


if __name__ == "__main__":
    unittest.main()
