from __future__ import annotations

import unittest

import dearpygui.dearpygui as dpg

from fundamental import acquisition_window
from fundamental.acquisition import AcquisitionController
from fundamental.app_shell import FundamentalApp
from fundamental.guided_sequence import GuidedSequenceController
from fundamental.messages import AcquisitionState
from fundamental.recording_session import RecordingSession
from fundamental.stimulus_model import StimulusController


class AcquisitionWindowGuidedTests(unittest.TestCase):
    def setUp(self) -> None:
        dpg.create_context()
        self.app = FundamentalApp()
        self.session = RecordingSession(
            AcquisitionController(),
            StimulusController(),
        )
        self.session.set_paradigm("miil")
        self.session.set_guided_sequence_enabled(True)
        acquisition_window.register(self.app, self.session)
        self.app.open_window(acquisition_window.ACQUISITION_WINDOW_TAG)

    def tearDown(self) -> None:
        dpg.destroy_context()

    def test_unapplied_guided_plan_is_visible_and_blocks_start(self) -> None:
        acquisition_window._refresh_status(self.session)

        self.assertIn(
            "Apply Guided Sequence",
            dpg.get_value(acquisition_window.STATUS_TEXT_TAG),
        )
        self.assertFalse(
            dpg.get_item_configuration(acquisition_window.START_BUTTON_TAG)[
                "enabled"
            ]
        )

        self.assertIn("Applied", self.session.apply_guided_sequence([1, 2], 1))
        acquisition_window._refresh_status(self.session)
        self.assertTrue(
            dpg.get_item_configuration(acquisition_window.START_BUTTON_TAG)[
                "enabled"
            ]
        )

    def test_completed_capture_blocks_resume_but_stop_allows_a_new_start(self) -> None:
        self.assertIn("Applied", self.session.apply_guided_sequence([1], 1))
        runner = GuidedSequenceController()
        runner.apply_plan([1], 1, [1])
        runner.start()
        runner.advance()
        runner.advance()
        self.session._capture_guided_sequence = runner
        self.session.acquisition.state = AcquisitionState.PAUSED
        acquisition_window._refresh_status(self.session)
        self.assertEqual(self.session.acquisition.state, AcquisitionState.PAUSED)
        self.assertFalse(
            dpg.get_item_configuration(acquisition_window.START_BUTTON_TAG)[
                "enabled"
            ]
        )

        self.session.acquisition.state = AcquisitionState.STOPPED
        acquisition_window._refresh_status(self.session)
        self.assertTrue(
            dpg.get_item_configuration(acquisition_window.START_BUTTON_TAG)[
                "enabled"
            ]
        )


if __name__ == "__main__":
    unittest.main()
