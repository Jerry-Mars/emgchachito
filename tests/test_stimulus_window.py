from __future__ import annotations

import unittest

import dearpygui.dearpygui as dpg

from fundamental.acquisition import AcquisitionController
from fundamental.app_shell import FundamentalApp
from fundamental.guided_sequence import GuidedSequenceController, GuidedSequenceState
from fundamental.messages import AcquisitionState
from fundamental.miil_model import MIIL_PARADIGM_ID, CapturePosition
from fundamental.recording_session import RecordingSession
from fundamental.stimulus_model import StimulusController
from fundamental import stimulus_window as window


class StimulusWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        dpg.create_context()
        self.app = FundamentalApp()
        self.acquisition = AcquisitionController()
        self.session = RecordingSession(self.acquisition, StimulusController())
        self.assertIsNone(self.session.set_paradigm(MIIL_PARADIGM_ID))
        window.register(self.app, self.session)
        self.app.open_window(window.STIMULUS_WINDOW_TAG)

    def tearDown(self) -> None:
        dpg.destroy_context()

    def test_guided_builder_expands_and_applies_an_order(self) -> None:
        self.assertFalse(self.session.guided_sequence_enabled)
        self.assertFalse(
            dpg.get_item_configuration(window.MIIL_GUIDED_CONFIG_GROUP_TAG)["show"]
        )

        dpg.set_value(window.MIIL_GUIDED_ENABLE_TAG, True)
        window._on_guided_enabled_changed(self.app, self.session)
        self.assertTrue(self.session.guided_sequence_enabled)
        self.assertTrue(
            dpg.get_item_configuration(window.MIIL_GUIDED_CONFIG_GROUP_TAG)["show"]
        )

        window._add_guided_step(self.session, 1)
        window._add_guided_step(self.session, 3)
        window._add_guided_step(self.session, 2)
        window._move_guided_step(self.session, 2, -1)
        window._remove_guided_step(self.session, 1)
        self.assertEqual(window._guided_steps_from_window(), [1, 3])

        dpg.set_value(window.MIIL_GUIDED_REPEAT_TAG, 5)
        window._mark_guided_dirty(self.session)
        message = window._apply_guided_sequence(self.app, self.session)
        self.assertIn("10 total step", message)
        plan = self.session.guided_sequence.plan
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.pattern, (1, 3))
        self.assertEqual(plan.repeat_count, 5)
        self.assertEqual(
            dpg.get_value(window.MIIL_GUIDED_PREVIEW_TAG),
            "Rest [1] -> Knee Extension [3]   x 5 group(s) = 10 planned step(s)",
        )
        self.assertFalse(
            dpg.get_item_configuration(window.MIIL_MANUAL_CONTROLS_GROUP_TAG)[
                "show"
            ]
        )
        self.assertEqual(
            dpg.get_value(window.MIIL_GUIDED_CONFIG_STATUS_TAG),
            "SEQUENCE APPLIED - READY",
        )

    def test_clear_keeps_guided_mode_but_removes_the_plan(self) -> None:
        self._enable_and_apply([1, 3], repeat_count=2)

        message = window._clear_guided_sequence(self.session)

        self.assertIn("cleared", message.casefold())
        self.assertTrue(self.session.guided_sequence_enabled)
        self.assertIsNone(self.session.guided_sequence.plan)
        self.assertEqual(window._guided_steps_from_window(), [])
        self.assertEqual(dpg.get_value(window.MIIL_GUIDED_REPEAT_TAG), 1)
        self.assertTrue(self.session.guided_sequence_configuration_dirty)

    def test_visible_enter_advances_but_hidden_window_only_consumes_it(self) -> None:
        runner = self._arm_guided_runtime([1, 3], repeat_count=2)

        consumed = window._handle_guided_enter(self.app, self.session)

        self.assertTrue(consumed)
        self.assertEqual(runner.state, GuidedSequenceState.ACTIVE)
        self.assertEqual(runner.active_step.flat_index, 0)  # type: ignore[union-attr]
        self.assertEqual(
            dpg.get_value(window.MIIL_GUIDED_POSITION_TAG),
            "Group 1/2 | Step 1/2 | Attempt 1",
        )
        self.assertIn("Knee Extension [CODE 3]", dpg.get_value(window.MIIL_GUIDED_NEXT_TAG))
        self.assertEqual(dpg.get_item_label(window.SAVE_BUTTON_TAG), "Pause & Save")

        self.app.window_manager.hide(window.STIMULUS_WINDOW_TAG)
        consumed = window._handle_guided_enter(self.app, self.session)

        self.assertTrue(consumed)
        self.assertEqual(runner.state, GuidedSequenceState.ACTIVE)
        self.assertEqual(runner.active_step.flat_index, 0)  # type: ignore[union-attr]

    def test_drop_shows_retry_and_enter_retries_the_same_step(self) -> None:
        runner = self._arm_guided_runtime([1, 3])
        window._handle_guided_enter(self.app, self.session)

        window._run_miil_drop(self.app, self.session)

        self.assertEqual(runner.state, GuidedSequenceState.RETRY_PENDING)
        self.assertEqual(runner.completed_step_count, 0)
        self.assertEqual(self.session.miil.current_code, -1)
        self.assertIn("RETRY REQUIRED", dpg.get_value(window.MIIL_GUIDED_POSITION_TAG))
        self.assertIn("retry", dpg.get_value(window.MIIL_GUIDED_KEY_HINT_TAG).casefold())

        window._handle_guided_enter(self.app, self.session)

        self.assertEqual(runner.state, GuidedSequenceState.ACTIVE)
        self.assertEqual(runner.active_step.flat_index, 0)  # type: ignore[union-attr]
        self.assertEqual(runner.attempts[-1].step_attempt_number, 2)
        self.assertEqual(self.session.miil.current_code, 1)

    def test_manual_mode_and_timed_paradigm_visibility_are_preserved(self) -> None:
        self.assertTrue(
            dpg.get_item_configuration(window.MIIL_MANUAL_CONTROLS_GROUP_TAG)[
                "show"
            ]
        )
        self.assertFalse(
            dpg.get_item_configuration(window.MIIL_GUIDED_DETAILS_GROUP_TAG)["show"]
        )

        self.assertIsNone(self.session.set_paradigm("timed_schedule"))
        window._refresh_window(self.session)

        self.assertTrue(
            dpg.get_item_configuration(window.TIMED_GROUP_TAG)["show"]
        )
        self.assertFalse(
            dpg.get_item_configuration(window.MIIL_GROUP_TAG)["show"]
        )

    def test_disabling_guided_after_stop_immediately_restores_manual_ui(self) -> None:
        runner = self._arm_guided_runtime([1, 3])
        window._handle_guided_enter(self.app, self.session)
        self.session.stop()

        dpg.set_value(window.MIIL_GUIDED_ENABLE_TAG, False)
        window._on_guided_enabled_changed(self.app, self.session)

        self.assertIs(self.session.capture_guided_sequence, runner)
        self.assertFalse(self.session.guided_sequence_enabled)
        self.assertTrue(
            dpg.get_item_configuration(window.MIIL_MANUAL_CONTROLS_GROUP_TAG)[
                "show"
            ]
        )
        self.assertFalse(
            dpg.get_item_configuration(window.MIIL_GUIDED_DETAILS_GROUP_TAG)["show"]
        )
        self.assertEqual(
            dpg.get_value(window.MIIL_OPERATOR_MODE_TAG),
            "MODE: MANUAL INSTRUCTION",
        )

    def _enable_and_apply(
        self,
        pattern: list[int],
        repeat_count: int = 1,
    ) -> None:
        dpg.set_value(window.MIIL_GUIDED_ENABLE_TAG, True)
        window._on_guided_enabled_changed(self.app, self.session)
        window._set_guided_editor_steps(pattern, self.session, force=True)
        dpg.set_value(window.MIIL_GUIDED_REPEAT_TAG, repeat_count)
        window._mark_guided_dirty(self.session)
        message = window._apply_guided_sequence(self.app, self.session)
        self.assertIn("Applied Guided Sequence", message)

    def _arm_guided_runtime(
        self,
        pattern: list[int],
        repeat_count: int = 1,
    ) -> GuidedSequenceController:
        self._enable_and_apply(pattern, repeat_count)
        runner = GuidedSequenceController()
        self.assertIsNone(
            runner.apply_plan(
                pattern,
                repeat_count,
                (action.code for action in self.session.miil.actions),
            )
        )
        self.assertTrue(runner.start(0.0).accepted)
        self.session._capture_guided_sequence = runner
        self.session._capture_paradigm = MIIL_PARADIGM_ID
        self.session.miil.start(CapturePosition(0.0, {}))
        self.acquisition.state = AcquisitionState.RUNNING
        window._refresh_window(self.session)
        return runner


if __name__ == "__main__":
    unittest.main()
