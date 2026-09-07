from __future__ import annotations

import unittest

from assembly.experiment.fatigue_evaluation import (
    FatigueEvaluationController,
    FatigueEvaluationState,
    FatigueEvaluationTermSpec,
)
from assembly.experiment.miil import MIILBoundary


def boundary(seconds: float) -> MIILBoundary:
    ns = int(round(seconds * 1_000_000_000.0))
    return MIILBoundary(
        host_monotonic_ns=ns,
        host_unix_ns=1_700_000_000_000_000_000 + ns,
    )


class FatigueEvaluationControllerTests(unittest.TestCase):
    def test_session_starts_in_no_stimulus_ready_state(self) -> None:
        controller = FatigueEvaluationController()
        controller.start_session(boundary(0.0))
        self.assertIs(controller.state, FatigueEvaluationState.READY)
        self.assertEqual(controller.current_code, 0)
        self.assertEqual(controller.pending_term_number, 1)

    def test_q_records_point_events_without_splitting_term(self) -> None:
        controller = FatigueEvaluationController()
        controller.start_session(boundary(0.0))
        controller.start_term(FatigueEvaluationTermSpec("Knee Extension", 30.0), boundary(1.0))
        event_index = controller.current_term.event_index  # type: ignore[union-attr]

        controller.record_action(boundary(2.0))
        controller.record_action(boundary(3.0))

        term = controller.current_term
        self.assertIsNotNone(term)
        self.assertEqual(term.event_index, event_index)  # type: ignore[union-attr]
        self.assertEqual(term.action_count, 2)  # type: ignore[union-attr]
        self.assertEqual(len([segment for segment in controller.segments if segment.kind == "term"]), 1)

    def test_t_ends_timed_term_early(self) -> None:
        controller = FatigueEvaluationController()
        controller.start_session(boundary(0.0))
        controller.start_term(FatigueEvaluationTermSpec("Timed", 30.0), boundary(1.0))

        controller.end_term_manual(boundary(8.0))

        self.assertIs(controller.state, FatigueEvaluationState.EVALUATING)
        term = controller.last_completed_term
        self.assertIsNotNone(term)
        self.assertEqual(term.end_method, "manual_t")  # type: ignore[union-attr]
        self.assertAlmostEqual(term.duration_s(), 7.0)  # type: ignore[union-attr]
        self.assertEqual(controller.current_code, 0)

    def test_t_ends_open_ended_term(self) -> None:
        controller = FatigueEvaluationController()
        controller.start_session(boundary(0.0))
        controller.start_term(FatigueEvaluationTermSpec("Open Ended", None), boundary(1.0))
        controller.record_action(boundary(2.0))

        controller.end_term_manual(boundary(6.5))

        term = controller.last_completed_term
        self.assertIsNotNone(term)
        self.assertEqual(term.end_method, "manual_t")  # type: ignore[union-attr]
        self.assertIsNone(term.planned_duration_s)  # type: ignore[union-attr]
        self.assertEqual(term.action_count, 1)  # type: ignore[union-attr]

    def test_t_after_planned_end_uses_exact_timer_boundary(self) -> None:
        controller = FatigueEvaluationController()
        controller.start_session(boundary(0.0))
        controller.start_term(FatigueEvaluationTermSpec("Timed", 5.0), boundary(1.0))

        controller.end_term_manual(boundary(7.0))

        term = controller.last_completed_term
        self.assertEqual(term.end_method, "timer")  # type: ignore[union-attr]
        self.assertEqual(term.end_monotonic_ns, boundary(6.0).host_monotonic_ns)  # type: ignore[union-attr]

    def test_cr10_rating_is_attached_to_completed_term(self) -> None:
        controller = FatigueEvaluationController()
        controller.start_session(boundary(0.0))
        controller.start_term(FatigueEvaluationTermSpec("Term A", 5.0), boundary(1.0))
        controller.end_term_manual(boundary(4.0))

        controller.set_cr10(6)

        term = controller.last_completed_term
        self.assertEqual(term.cr10_score, 6)  # type: ignore[union-attr]
        self.assertIn("Very hard", term.cr10_label)  # type: ignore[union-attr]

    def test_next_term_can_change_name_and_duration(self) -> None:
        controller = FatigueEvaluationController()
        controller.start_session(boundary(0.0))
        controller.start_term(FatigueEvaluationTermSpec("Term A", 10.0), boundary(1.0))
        controller.end_term_manual(boundary(4.0))
        self.assertEqual(controller.pending_term_number, 2)

        controller.start_term(FatigueEvaluationTermSpec("Term B", None), boundary(5.0))

        term = controller.current_term
        self.assertEqual(term.term_number, 2)  # type: ignore[union-attr]
        self.assertEqual(term.label, "Term B")  # type: ignore[union-attr]
        self.assertIsNone(term.planned_duration_s)  # type: ignore[union-attr]

    def test_drop_running_term_retries_same_term_number(self) -> None:
        controller = FatigueEvaluationController()
        controller.start_session(boundary(0.0))
        controller.start_term(FatigueEvaluationTermSpec("Term A", 10.0), boundary(1.0))
        controller.record_action(boundary(2.0))
        controller.drop(boundary(3.0))

        self.assertIs(controller.state, FatigueEvaluationState.EVALUATING)
        self.assertEqual(controller.pending_term_number, 1)
        dropped = [segment for segment in controller.segments if segment.status == "dropped"]
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0].effective_code, -1)
        self.assertEqual(dropped[0].action_count, 1)

        controller.start_term(FatigueEvaluationTermSpec("Term A Retry", 8.0), boundary(4.0))
        self.assertEqual(controller.current_term.term_number, 1)  # type: ignore[union-attr]
        self.assertEqual(controller.current_term.attempt, 2)  # type: ignore[union-attr]

    def test_drop_during_evaluation_invalidates_previous_term_and_rating(self) -> None:
        controller = FatigueEvaluationController()
        controller.start_session(boundary(0.0))
        controller.start_term(FatigueEvaluationTermSpec("Term A", 10.0), boundary(1.0))
        controller.end_term_manual(boundary(4.0))
        controller.set_cr10(5)

        controller.drop(boundary(5.0))

        self.assertEqual(controller.pending_term_number, 1)
        term = next(segment for segment in controller.segments if segment.kind == "term")
        self.assertEqual(term.status, "dropped")
        self.assertIsNone(term.cr10_score)

    def test_finish_protocol_from_evaluation(self) -> None:
        controller = FatigueEvaluationController()
        controller.start_session(boundary(0.0))
        controller.start_term(FatigueEvaluationTermSpec("Term A", 5.0), boundary(1.0))
        controller.end_term_manual(boundary(3.0))
        controller.set_cr10(4)

        controller.finish_protocol()

        self.assertIs(controller.state, FatigueEvaluationState.COMPLETE)
        self.assertEqual(controller.current_code, 0)

    def test_metadata_contains_q_events_end_method_and_cr10(self) -> None:
        controller = FatigueEvaluationController()
        controller.start_session(boundary(0.0))
        controller.start_term(FatigueEvaluationTermSpec("Term A", None), boundary(1.0))
        controller.record_action(boundary(2.0))
        controller.record_action(boundary(3.0))
        controller.end_term_manual(boundary(4.0))
        controller.set_cr10(7)
        controller.finish_protocol()
        controller.stop_session(boundary(5.0))

        metadata = controller.metadata_snapshot()
        self.assertEqual(metadata["paradigm"], "fatigue_evaluation")
        term_rows = [row for row in metadata["segments"] if row["kind"] == "term"]
        self.assertEqual(len(term_rows), 1)
        self.assertEqual(term_rows[0]["action_count"], 2)
        self.assertEqual(term_rows[0]["end_method"], "manual_t")
        self.assertEqual(term_rows[0]["cr10_score"], 7)
        self.assertEqual(len(term_rows[0]["action_events"]), 2)


if __name__ == "__main__":
    unittest.main()
