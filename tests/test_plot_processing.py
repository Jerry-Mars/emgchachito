from __future__ import annotations

import unittest

from fundamental.plot_processing import (
    AxisScaler,
    minmax_downsample,
    moving_average,
    moving_rms,
    process_signal,
    signal_stats,
)


class PlotProcessingTests(unittest.TestCase):
    def test_moving_average_uses_centered_window(self) -> None:
        self.assertEqual(moving_average([1.0, 3.0, 5.0], 3), [3.0, 3.0, 3.0])
        self.assertEqual(moving_average([1.0, 3.0, 5.0], 1), [1.0, 3.0, 5.0])

    def test_process_signal_views_keep_units_explicit(self) -> None:
        raw = process_signal([-2.0, 0.0, 2.0], "Raw")
        rectified = process_signal([-2.0, 0.0, 2.0], "Rectified")
        rms = process_signal([-2.0, 0.0, 2.0], "RMS")
        envelope = process_signal([-2.0, 0.0, 2.0], "Envelope")

        self.assertEqual(raw.values, [-2.0, 0.0, 2.0])
        self.assertTrue(raw.bipolar)
        self.assertEqual(raw.unit, "code")
        self.assertEqual(rectified.values, [2.0, 0.0, 2.0])
        self.assertFalse(rectified.bipolar)
        self.assertEqual(rms.unit, "code RMS")
        self.assertEqual(envelope.unit, "code")

    def test_minmax_downsample_keeps_extrema(self) -> None:
        x_values = [float(index) for index in range(10)]
        y_values = [0.0, 1.0, -4.0, 2.0, 0.0, 3.0, -1.0, 5.0, 0.0, -2.0]

        x_down, y_down = minmax_downsample(x_values, y_values, max_points=4)

        self.assertLessEqual(len(x_down), 4)
        self.assertIn(-4.0, y_down)
        self.assertIn(5.0, y_down)

    def test_axis_scaler_robust_mode_reports_outside_points(self) -> None:
        scaler = AxisScaler()

        low, high, outside = scaler.get_limits([0.1, 0.2, 0.3, 100.0], "Robust Scaling", bipolar=False)

        self.assertGreaterEqual(low, 0.0)
        self.assertGreater(high, low)
        self.assertGreaterEqual(outside, 0)

    def test_signal_stats(self) -> None:
        stats = signal_stats([-3.0, 4.0])

        self.assertEqual(stats.peak, 4.0)
        self.assertAlmostEqual(stats.rms, 3.5355339059)


if __name__ == "__main__":
    unittest.main()
