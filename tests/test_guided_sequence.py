from __future__ import annotations

import unittest

from fundamental.guided_sequence import (
    GuidedAttemptStatus,
    GuidedSequenceController,
    GuidedSequenceState,
)


class GuidedSequenceConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = GuidedSequenceController()

    def test_apply_plan_validates_pattern_repeat_and_codebook(self) -> None:
        self.assertIn("cannot be empty", self.controller.apply_plan([], 1, [1, 2]))
        self.assertIn("positive integers", self.controller.apply_plan([0], 1, [1]))
        self.assertIn("positive integer", self.controller.apply_plan([1], 0, [1]))
        self.assertIn("unique", self.controller.apply_plan([1], 1, [1, 1]))
        self.assertIn("unconfigured", self.controller.apply_plan([2], 1, [1]))

    def test_apply_plan_rejects_ambiguous_adjacent_intervals(self) -> None:
        error = self.controller.apply_plan([1, 1, 2], 1, [1, 2])
        self.assertIn("Adjacent", error)

        error = self.controller.apply_plan([1, 2, 1], 2, [1, 2])
        self.assertIn("group boundary", error)

        self.assertIsNone(self.controller.apply_plan([1, 2, 1], 1, [1, 2]))

    def test_valid_plan_exposes_group_step_addressing_without_expansion(self) -> None:
        self.assertIsNone(self.controller.apply_plan([1, 3, 2], 4, [1, 2, 3]))
        plan = self.controller.plan
        assert plan is not None
        self.assertEqual(plan.total_steps, 12)
        self.assertEqual(plan.step(7).group_number, 3)
        self.assertEqual(plan.step(7).step_number, 2)
        self.assertEqual(plan.step(7).code, 3)
        self.assertEqual(self.controller.state, GuidedSequenceState.READY)

    def test_cannot_change_or_clear_plan_while_active(self) -> None:
        self.controller.apply_plan([1, 2], 1, [1, 2])
        self.controller.start()
        self.assertIn("Stop", self.controller.apply_plan([2, 1], 1, [1, 2]))
        self.assertIn("Stop", self.controller.clear_plan())


class GuidedSequenceRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = GuidedSequenceController()
        error = self.controller.apply_plan([1, 2], 2, [1, 2, 3])
        self.assertIsNone(error)

    def test_start_waits_at_no_stimulus_and_enter_walks_repeated_plan(self) -> None:
        start = self.controller.start(at_s=0.0)
        self.assertTrue(start.accepted)
        self.assertFalse(start.has_miil_effect)
        self.assertEqual(self.controller.state, GuidedSequenceState.WAITING_FIRST)
        self.assertEqual(self.controller.completed_step_count, 0)

        expected = [
            (1, 1, 1),
            (2, 1, 2),
            (1, 2, 1),
            (2, 2, 2),
        ]
        for event_time, (code, group, step_number) in enumerate(expected, start=1):
            command = self.controller.advance(float(event_time))
            self.assertTrue(command.accepted)
            self.assertEqual(command.select_action_code, code)
            active = self.controller.active_step
            assert active is not None
            self.assertEqual((active.group_number, active.step_number), (group, step_number))

        self.assertEqual(self.controller.completed_step_count, 3)
        finish = self.controller.advance(5.0)
        self.assertTrue(finish.accepted)
        self.assertTrue(finish.select_no_stimulus)
        self.assertTrue(finish.pause_acquisition)
        self.assertEqual(finish.state, GuidedSequenceState.COMPLETED)
        self.assertEqual(self.controller.completed_step_count, 4)
        self.assertEqual(self.controller.progress_fraction, 1.0)
        self.assertEqual(
            [attempt.status for attempt in self.controller.attempts],
            [GuidedAttemptStatus.COMPLETED] * 4,
        )

    def test_final_action_is_not_completed_until_an_extra_enter(self) -> None:
        controller = GuidedSequenceController()
        controller.apply_plan([3], 1, [3])
        controller.start()
        first = controller.advance()
        self.assertEqual(first.select_action_code, 3)
        self.assertEqual(controller.state, GuidedSequenceState.ACTIVE)
        self.assertFalse(first.pause_acquisition)

        finish = controller.advance()
        self.assertEqual(controller.state, GuidedSequenceState.COMPLETED)
        self.assertTrue(finish.pause_acquisition)

    def test_no_stimulus_finishes_step_and_buffers_before_next(self) -> None:
        self.controller.start()
        self.controller.advance()
        no_stimulus = self.controller.select_no_stimulus()
        self.assertTrue(no_stimulus.select_no_stimulus)
        self.assertFalse(no_stimulus.pause_acquisition)
        self.assertEqual(self.controller.state, GuidedSequenceState.BUFFER)
        self.assertEqual(self.controller.completed_step_count, 1)
        self.assertEqual(self.controller.next_step.code, 2)

        next_action = self.controller.advance()
        self.assertEqual(next_action.select_action_code, 2)
        self.assertEqual(self.controller.state, GuidedSequenceState.ACTIVE)

    def test_no_stimulus_after_final_action_completes_and_pauses(self) -> None:
        controller = GuidedSequenceController()
        controller.apply_plan([1], 1, [1])
        controller.start()
        controller.advance()
        finish = controller.select_no_stimulus()
        self.assertEqual(finish.state, GuidedSequenceState.COMPLETED)
        self.assertTrue(finish.select_no_stimulus)
        self.assertTrue(finish.pause_acquisition)
        self.assertEqual(controller.completed_step_count, 1)

    def test_no_and_drop_are_ignored_while_waiting_at_code_zero(self) -> None:
        self.controller.start()
        no_command = self.controller.select_no_stimulus()
        drop_command = self.controller.drop_current()
        self.assertFalse(no_command.accepted)
        self.assertFalse(drop_command.accepted)
        self.assertEqual(self.controller.state, GuidedSequenceState.WAITING_FIRST)

    def test_drop_retries_same_step_without_advancing_progress(self) -> None:
        self.controller.start(at_s=0.0)
        self.controller.advance(at_s=1.0)
        dropped = self.controller.drop_current(at_s=2.0)
        self.assertTrue(dropped.drop_current_interval)
        self.assertEqual(self.controller.state, GuidedSequenceState.RETRY_PENDING)
        self.assertEqual(self.controller.completed_step_count, 0)
        self.assertEqual(self.controller.next_step.code, 1)
        self.assertEqual(self.controller.attempts[0].status, GuidedAttemptStatus.DROPPED)

        retry = self.controller.advance(at_s=3.0)
        self.assertEqual(retry.select_action_code, 1)
        self.assertEqual(self.controller.active_step.flat_index, 0)
        self.assertEqual(self.controller.attempts[-1].step_attempt_number, 2)

        next_action = self.controller.advance(at_s=4.0)
        self.assertEqual(next_action.select_action_code, 2)
        self.assertEqual(self.controller.completed_step_count, 1)

    def test_attempt_can_be_linked_to_its_miil_interval(self) -> None:
        self.controller.start()
        self.controller.advance()

        self.assertIsNone(self.controller.bind_active_miil_event(4))
        self.assertEqual(self.controller.attempts[-1].miil_event_index, 4)
        self.assertIn("already", self.controller.bind_active_miil_event(5) or "")

    def test_drop_then_no_stimulus_keeps_retry_pending(self) -> None:
        self.controller.start()
        self.controller.advance()
        self.controller.drop_current()
        buffer_command = self.controller.select_no_stimulus()
        self.assertTrue(buffer_command.select_no_stimulus)
        self.assertEqual(self.controller.state, GuidedSequenceState.BUFFER)
        self.assertTrue(self.controller.retry_pending)

        retry = self.controller.advance()
        self.assertEqual(retry.select_action_code, 1)
        self.assertFalse(self.controller.retry_pending)

    def test_pause_and_resume_restore_exact_runtime_phase(self) -> None:
        self.controller.start()
        self.controller.advance()
        pause = self.controller.pause()
        self.assertTrue(pause.accepted)
        self.assertEqual(self.controller.state, GuidedSequenceState.PAUSED)
        self.assertFalse(self.controller.advance().accepted)

        resume = self.controller.resume()
        self.assertTrue(resume.accepted)
        self.assertEqual(self.controller.state, GuidedSequenceState.ACTIVE)
        self.assertEqual(self.controller.active_step.code, 1)

        self.controller.drop_current()
        self.controller.pause()
        self.controller.resume()
        self.assertEqual(self.controller.state, GuidedSequenceState.RETRY_PENDING)
        self.assertTrue(self.controller.retry_pending)

    def test_stop_and_abort_mark_an_active_attempt(self) -> None:
        self.controller.start(at_s=0.0)
        self.controller.advance(at_s=1.0)
        stopped = self.controller.stop(at_s=2.0)
        self.assertEqual(stopped.state, GuidedSequenceState.STOPPED)
        self.assertEqual(self.controller.attempts[-1].status, GuidedAttemptStatus.STOPPED)

        self.controller.reset_runtime()
        self.controller.start(at_s=0.0)
        self.controller.advance(at_s=1.0)
        aborted = self.controller.stop(aborted=True, at_s=2.0)
        self.assertEqual(aborted.state, GuidedSequenceState.ABORTED)
        self.assertEqual(self.controller.attempts[-1].status, GuidedAttemptStatus.ABORTED)

    def test_completed_runner_can_be_changed_to_aborted_by_effect_failure(self) -> None:
        controller = GuidedSequenceController()
        controller.apply_plan([1], 1, [1])
        controller.start(at_s=0.0)
        controller.advance(at_s=1.0)
        controller.advance(at_s=2.0)
        self.assertEqual(controller.state, GuidedSequenceState.COMPLETED)

        command = controller.stop(aborted=True, at_s=2.0)

        self.assertTrue(command.accepted)
        self.assertEqual(controller.state, GuidedSequenceState.ABORTED)

    def test_event_time_must_be_monotonic(self) -> None:
        self.controller.start(at_s=1.0)
        with self.assertRaisesRegex(ValueError, "backwards"):
            self.controller.advance(at_s=0.5)


class GuidedSequenceMetadataTests(unittest.TestCase):
    def test_metadata_contains_compact_plan_progress_and_attempts(self) -> None:
        controller = GuidedSequenceController()
        controller.apply_plan([1, 3, 2], 2, [1, 2, 3])
        controller.start(at_s=0.0)
        controller.advance(at_s=0.5)
        controller.drop_current(at_s=1.5)
        controller.advance(at_s=2.0)

        metadata = controller.metadata_snapshot()
        self.assertEqual(metadata["mode"], "guided_sequence")
        self.assertEqual(
            metadata["plan"],
            {
                "pattern_codes": [1, 3, 2],
                "repeat_count": 2,
                "steps_per_group": 3,
                "total_steps": 6,
            },
        )
        self.assertEqual(metadata["progress"]["active_flat_step_index"], 0)
        self.assertFalse(metadata["progress"]["retry_pending"])
        self.assertEqual(len(metadata["attempts"]), 2)
        self.assertEqual(metadata["attempts"][0]["status"], "dropped")
        self.assertEqual(metadata["attempts"][0]["ended_by"], "drop_stimulus")
        self.assertEqual(metadata["attempts"][1]["step_attempt_number"], 2)
        self.assertEqual(metadata["attempts"][1]["outcome"], "active_at_save")
        self.assertNotIn("expanded_steps", metadata)


if __name__ == "__main__":
    unittest.main()
