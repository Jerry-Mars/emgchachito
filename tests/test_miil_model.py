from __future__ import annotations

import unittest

from fundamental.miil_model import (
    CapturePosition,
    MIILAction,
    MIILController,
)
from fundamental.stimulus_model import StimulusState


def position(time_s: float, **row_counts: int) -> CapturePosition:
    return CapturePosition(time_s, row_counts)


class MIILConfigurationTests(unittest.TestCase):
    def test_action_configuration_requires_positive_unique_codes(self) -> None:
        controller = MIILController()

        self.assertIn(
            "positive integer",
            controller.apply_actions([MIILAction("rest", "Rest", 0)]) or "",
        )
        self.assertIn(
            "duplicated",
            controller.apply_actions(
                [MIILAction("rest", "Rest", 1), MIILAction("flex", "Flex", 1)]
            )
            or "",
        )
        self.assertIn(
            "duplicated",
            controller.apply_actions(
                [MIILAction("rest", "Rest", 1), MIILAction("REST", "Rest 2", 2)]
            )
            or "",
        )
        self.assertIn(
            "reserved",
            controller.apply_actions([MIILAction("no_stimulus", "Reserved", 1)])
            or "",
        )
        self.assertIsNone(
            controller.apply_actions(
                [MIILAction("rest", "Rest", 1), MIILAction("flex", "Flex", 2)]
            )
        )

    def test_configuration_is_frozen_while_running_or_paused(self) -> None:
        controller = MIILController()
        controller.start(position(0.0, emg=0))

        self.assertIn(
            "Stop MIIL",
            controller.apply_actions([MIILAction("new", "New", 7)]) or "",
        )

    def test_next_codebook_can_be_applied_without_erasing_stopped_capture(self) -> None:
        controller = MIILController()
        controller.start(position(0.0, emg=0))
        controller.select_action(1, position(1.0, emg=10))
        controller.stop(position(2.0, emg=20))

        self.assertIsNone(controller.apply_actions([MIILAction("new", "New", 7)]))
        self.assertEqual(controller.sample_code("emg", 15, 1.5), 1)
        self.assertEqual(
            controller.metadata_snapshot()["codebook"],
            [{"action": "rest", "label": "Rest", "stimulus_code": 1},
             {"action": "knee_flexion", "label": "Knee Flexion", "stimulus_code": 2},
             {"action": "knee_extension", "label": "Knee Extension", "stimulus_code": 3}],
        )

        controller.start(position(0.0, emg=0))
        self.assertIn("New", controller.select_action(7, position(0.5, emg=5)))
        controller.pause(position(0.5, emg=5))
        self.assertIn(
            "Stop MIIL",
            controller.apply_actions([MIILAction("new", "New", 7)]) or "",
        )

    def test_capture_position_is_validated_and_copied(self) -> None:
        rows = {"emg": 2}
        captured = CapturePosition(1.0, rows)
        rows["emg"] = 99
        self.assertEqual(captured.row_counts["emg"], 2)
        with self.assertRaises(ValueError):
            CapturePosition(-0.1, {})
        with self.assertRaises(ValueError):
            CapturePosition(0.0, {"emg": -1})


class MIILTimelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = MIILController()
        self.controller.apply_actions(
            [
                MIILAction("rest", "Rest", 1),
                MIILAction("knee_flexion", "Knee Flexion", 2),
            ]
        )

    def test_start_opens_no_stimulus_and_switches_manual_intervals(self) -> None:
        self.assertEqual(
            self.controller.start(position(0.0, emg=0, imu=0)),
            "MIIL started with no_stimulus.",
        )
        self.controller.select_action(1, position(0.4, emg=4, imu=2))
        self.controller.select_action(2, position(2.0, emg=20, imu=10))
        self.controller.stop(position(3.0, emg=30, imu=15))

        self.assertEqual(self.controller.state, StimulusState.STOPPED)
        self.assertEqual(
            [interval.effective_code for interval in self.controller.intervals],
            [0, 1, 2],
        )
        self.assertEqual(self.controller.sample_code("emg", 3, 100.0), 0)
        self.assertEqual(self.controller.sample_code("emg", 4, 100.0), 1)
        self.assertEqual(self.controller.sample_code("emg", 19, 100.0), 1)
        self.assertEqual(self.controller.sample_code("emg", 20, 0.0), 2)
        self.assertEqual(self.controller.sample_code("imu", 9, 100.0), 1)
        self.assertEqual(self.controller.sample_code("imu", 10, 0.0), 2)

    def test_repeated_current_action_and_no_stimulus_are_ignored(self) -> None:
        self.controller.start(position(0.0, emg=0))
        self.assertIn("already active", self.controller.select_no_stimulus(position(0.1, emg=1)))
        self.controller.select_action(1, position(0.2, emg=2))
        self.assertIn("already active", self.controller.select_action(1, position(0.3, emg=3)))

        self.assertEqual(len(self.controller.intervals), 2)
        self.assertIsNone(self.controller.current_interval.end_time_s)  # type: ignore[union-attr]

    def test_drop_retroactively_invalidates_whole_interval_until_next_selection(self) -> None:
        self.controller.start(position(0.0, emg=0))
        self.controller.select_action(2, position(1.0, emg=10))
        self.assertEqual(self.controller.sample_code("emg", 10, 1.1), 2)
        message = self.controller.drop_current(position(2.0, emg=20))

        self.assertIn("from its beginning", message)
        dropped = self.controller.current_interval
        assert dropped is not None
        self.assertEqual(dropped.original_code, 2)
        self.assertEqual(dropped.effective_code, -1)
        self.assertEqual(dropped.drop_pressed_at_s, 2.0)
        self.assertEqual(self.controller.sample_code("emg", 10, 1.1), -1)
        self.assertEqual(self.controller.sample_code("emg", 19, 1.9), -1)

        # Selecting the same original action after Drop starts a new valid interval.
        self.controller.select_action(2, position(3.0, emg=30))
        self.assertEqual(len(self.controller.intervals), 3)
        self.assertEqual(self.controller.sample_code("emg", 29, 2.9), -1)
        self.assertEqual(self.controller.sample_code("emg", 30, 3.0), 2)

    def test_drop_is_ignored_for_no_stimulus_and_when_already_dropped(self) -> None:
        self.controller.start(position(0.0, emg=0))
        self.assertIn("ignored", self.controller.drop_current(position(0.5, emg=5)))
        self.assertEqual(self.controller.current_code, 0)

        self.controller.select_action(1, position(1.0, emg=10))
        self.controller.drop_current(position(1.5, emg=15))
        self.assertIn("already active", self.controller.drop_current(position(1.7, emg=17)))
        self.assertEqual(len(self.controller.intervals), 2)

    def test_pause_preserves_interval_and_freezes_elapsed_time(self) -> None:
        self.controller.start(position(0.0, emg=0))
        self.controller.select_action(1, position(1.0, emg=10))
        self.controller.pause(position(3.5, emg=35))

        self.assertEqual(self.controller.state, StimulusState.PAUSED)
        self.assertEqual(self.controller.current_code, 1)
        self.assertEqual(self.controller.current_elapsed_s(99.0), 2.5)
        self.controller.resume(position(3.5, emg=35))
        self.assertEqual(self.controller.current_code, 1)
        self.assertEqual(len(self.controller.intervals), 2)

    def test_missing_stream_cursor_falls_back_to_shared_time(self) -> None:
        self.controller.start(position(0.0, emg=0))
        self.controller.select_action(1, position(1.0, emg=10))
        self.controller.stop(position(2.0, emg=20))

        self.assertEqual(self.controller.sample_code("late_imu", 999, 0.5), 0)
        self.assertEqual(self.controller.sample_code("late_imu", 0, 1.5), 1)

    def test_non_monotonic_boundaries_are_clamped(self) -> None:
        self.controller.start(position(1.0, emg=10))
        self.controller.select_action(1, position(0.5, emg=5))

        first = self.controller.intervals[0]
        second = self.controller.intervals[1]
        self.assertEqual(first.end_time_s, 1.0)
        self.assertEqual(first.end.row_counts["emg"], 10)  # type: ignore[union-attr]
        self.assertEqual(second.start_time_s, 1.0)


class MIILAuditTests(unittest.TestCase):
    def test_log_and_metadata_preserve_original_drop_information(self) -> None:
        controller = MIILController()
        controller.start(position(0.0, emg=0, imu=0))
        controller.select_action(2, position(1.0, emg=10, imu=5))
        controller.drop_current(position(2.0, emg=20, imu=10))
        controller.stop(position(3.0, emg=30, imu=15))

        row = controller.event_log_rows()[1]
        self.assertEqual(row["stimulus_code"], -1)
        self.assertEqual(row["original_code"], 2)
        self.assertEqual(row["drop_pressed_at_s"], 2.0)
        self.assertEqual(row["duration_s"], 2.0)

        metadata = controller.metadata_snapshot()
        self.assertEqual(metadata["paradigm"], "miil")
        self.assertEqual(metadata["code_semantics"]["-1"], "drop_stimulus")  # type: ignore[index]
        interval = metadata["intervals"][1]  # type: ignore[index]
        self.assertEqual(interval["start_row_counts"], {"emg": 10, "imu": 5})
        self.assertEqual(interval["end_row_counts"], {"emg": 30, "imu": 15})
        recommendations = metadata["offline_processing_recommendations"]  # type: ignore[index]
        self.assertTrue(recommendations["windows_must_not_cross_interval_boundaries"])


if __name__ == "__main__":
    unittest.main()
