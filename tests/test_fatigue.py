from __future__ import annotations

import unittest

from assembly.experiment.fatigue import (
    FatigueController,
    FatigueIntervalSpec,
    FatigueState,
)
from assembly.experiment.miil import MIILBoundary


def boundary(seconds: float) -> MIILBoundary:
    ns = int(round(seconds * 1_000_000_000.0))
    return MIILBoundary(host_monotonic_ns=ns, host_unix_ns=1_700_000_000_000_000_000 + ns)


class FatigueControllerTests(unittest.TestCase):
    def test_default_plan_is_ten_cr10_stages(self) -> None:
        controller = FatigueController()
        self.assertEqual(len(controller.plan), 10)
        self.assertEqual([item.code for item in controller.plan], list(range(1, 11)))
        self.assertTrue(all(item.action == f"cr10_{item.code}" for item in controller.plan))
        self.assertTrue(all(item.duration_s == 30.0 for item in controller.plan))

    def make_controller(self) -> FatigueController:
        return FatigueController(
            (
                FatigueIntervalSpec("rest", "Rest", 1, 2.0),
                FatigueIntervalSpec("extension", "Extension", 2, 3.0),
                FatigueIntervalSpec("rest", "Rest", 1, 1.0),
            )
        )

    def test_session_starts_waiting_and_q_starts_first_interval(self) -> None:
        controller = self.make_controller()
        controller.start(boundary(0.0))
        self.assertIs(controller.state, FatigueState.WAITING)
        self.assertEqual(controller.current_code, 0)
        self.assertEqual(controller.current_interval_index, 0)

        controller.start_next(boundary(1.0))
        self.assertIs(controller.state, FatigueState.RUNNING)
        self.assertEqual(controller.current_code, 1)
        self.assertEqual(controller.current_segment.attempt, 1)  # type: ignore[union-attr]

    def test_interval_auto_ends_at_planned_boundary_then_waits(self) -> None:
        controller = self.make_controller()
        controller.start(boundary(0.0))
        controller.start_next(boundary(1.0))

        self.assertIsNone(controller.update(boundary(2.9)))
        message = controller.update(boundary(3.5))
        self.assertIsNotNone(message)
        self.assertIs(controller.state, FatigueState.WAITING)
        self.assertEqual(controller.current_code, 0)
        self.assertEqual(controller.current_interval_index, 1)

        first_attempt = next(
            segment for segment in controller.segments if segment.interval_index == 0
        )
        self.assertEqual(first_attempt.end_monotonic_ns, boundary(3.0).host_monotonic_ns)
        self.assertEqual(first_attempt.status, "completed")

    def test_drop_running_attempt_rewinds_same_interval(self) -> None:
        controller = self.make_controller()
        controller.start(boundary(0.0))
        controller.start_next(boundary(1.0))

        controller.drop(boundary(1.8))
        self.assertIs(controller.state, FatigueState.WAITING)
        self.assertEqual(controller.current_interval_index, 0)

        dropped = [segment for segment in controller.segments if segment.status == "dropped"]
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0].effective_code, -1)
        self.assertEqual(dropped[0].attempt, 1)

        controller.start_next(boundary(2.0))
        self.assertEqual(controller.current_segment.attempt, 2)  # type: ignore[union-attr]

    def test_drop_during_wait_invalidates_previous_completed_attempt(self) -> None:
        controller = self.make_controller()
        controller.start(boundary(0.0))
        controller.start_next(boundary(1.0))
        controller.update(boundary(3.2))
        self.assertEqual(controller.current_interval_index, 1)

        controller.drop(boundary(3.5))
        self.assertIs(controller.state, FatigueState.WAITING)
        self.assertEqual(controller.current_interval_index, 0)

        interval_attempts = [
            segment for segment in controller.segments if segment.interval_index == 0
        ]
        self.assertEqual(len(interval_attempts), 1)
        self.assertEqual(interval_attempts[0].effective_code, -1)
        self.assertEqual(interval_attempts[0].status, "dropped")

    def test_completed_protocol_can_rewind_last_interval(self) -> None:
        controller = FatigueController((FatigueIntervalSpec("mvc", "MVC", 3, 1.0),))
        controller.start(boundary(0.0))
        controller.start_next(boundary(1.0))
        controller.update(boundary(2.2))
        self.assertIs(controller.state, FatigueState.COMPLETE)

        controller.drop(boundary(2.5))
        self.assertIs(controller.state, FatigueState.WAITING)
        self.assertEqual(controller.current_interval_index, 0)

    def test_repeated_rest_intervals_may_share_action_and_code(self) -> None:
        controller = FatigueController()
        error = controller.configure_plan(
            (
                FatigueIntervalSpec("rest", "Rest", 1, 10.0),
                FatigueIntervalSpec("extension", "Extension", 2, 30.0),
                FatigueIntervalSpec("rest", "Rest", 1, 15.0),
            )
        )
        self.assertIsNone(error)
        self.assertEqual([item.code for item in controller.plan], [1, 2, 1])

    def test_same_code_cannot_mean_two_different_actions(self) -> None:
        controller = FatigueController()
        error = controller.configure_plan(
            (
                FatigueIntervalSpec("rest", "Rest", 1, 10.0),
                FatigueIntervalSpec("extension", "Extension", 1, 30.0),
            )
        )
        self.assertIsNotNone(error)

    def test_metadata_preserves_plan_attempts_and_drop(self) -> None:
        controller = self.make_controller()
        controller.start(boundary(0.0))
        controller.start_next(boundary(1.0))
        controller.drop(boundary(1.5))
        controller.start_next(boundary(2.0))
        controller.update(boundary(4.1))
        controller.stop(boundary(4.5))

        metadata = controller.metadata_snapshot()
        self.assertEqual(metadata["paradigm"], "fatigue")
        self.assertEqual(len(metadata["plan"]), 3)
        attempts = [row for row in metadata["segments"] if row["interval_number"] == 1]
        self.assertEqual([row["attempt"] for row in attempts], [1, 2])
        self.assertEqual(attempts[0]["stimulus_code"], -1)
        self.assertEqual(attempts[1]["stimulus_code"], 1)


if __name__ == "__main__":
    unittest.main()
