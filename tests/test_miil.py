from __future__ import annotations

import unittest

from assembly.experiment.miil import (
    IDLE_STIMULUS_CODE,
    INVALID_STIMULUS_CODE,
    MIILAction,
    MIILBoundary,
    MIILController,
    MIILState,
)

NS = 1_000_000_000
UNIX_ORIGIN = 1_800_000_000_000_000_000


def at(seconds: float) -> MIILBoundary:
    offset = int(seconds * NS)
    return MIILBoundary(offset, UNIX_ORIGIN + offset)


class MIILControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actions = (
            MIILAction("rest", "Rest", 1),
            MIILAction("flex", "Flexion", 2),
            MIILAction("extend", "Extension", 3),
        )

    def test_manual_timeline_preserves_main_miil_semantics(self) -> None:
        miil = MIILController(self.actions)

        self.assertEqual(miil.start(at(0.0)), "MIIL started with no_stimulus.")
        miil.select_action(1, at(2.0))
        miil.select_action(2, at(6.0))
        miil.drop_current(at(9.0))
        miil.select_no_stimulus(at(10.0))
        miil.select_action(3, at(12.0))
        miil.stop(at(18.0))

        self.assertEqual(miil.state, MIILState.STOPPED)
        self.assertEqual(
            [interval.event_index for interval in miil.intervals],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            [interval.effective_code for interval in miil.intervals],
            [0, 1, -1, 0, 3],
        )
        self.assertEqual(
            [interval.status for interval in miil.intervals],
            ["completed", "completed", "dropped", "completed", "stopped"],
        )
        self.assertEqual(miil.intervals[2].original_code, 2)
        self.assertEqual(miil.intervals[2].drop_pressed_at_monotonic_ns, 9 * NS)

    def test_code_at_uses_half_open_host_monotonic_intervals(self) -> None:
        miil = MIILController(self.actions)
        miil.start(at(0.0))
        miil.select_action(2, at(2.0))
        miil.select_action(3, at(6.0))
        miil.stop(at(10.0))

        self.assertEqual(miil.code_at(1 * NS), IDLE_STIMULUS_CODE)
        self.assertEqual(miil.code_at(2 * NS), 2)
        self.assertEqual(miil.code_at(5 * NS), 2)
        self.assertEqual(miil.code_at(6 * NS), 3)
        self.assertEqual(miil.code_at(10 * NS), IDLE_STIMULUS_CODE)

    def test_drop_invalidates_whole_current_interval(self) -> None:
        miil = MIILController(self.actions)
        miil.start(at(0.0))
        miil.select_action(2, at(2.0))
        miil.drop_current(at(5.0))
        miil.select_no_stimulus(at(8.0))

        self.assertEqual(miil.code_at(3 * NS), INVALID_STIMULUS_CODE)
        self.assertEqual(miil.code_at(7 * NS), INVALID_STIMULUS_CODE)
        self.assertEqual(miil.code_at(8 * NS), IDLE_STIMULUS_CODE)

    def test_codebook_is_frozen_for_active_capture(self) -> None:
        miil = MIILController(self.actions)
        miil.start(at(0.0))

        error = miil.configure_actions((MIILAction("other", "Other", 9),))

        self.assertEqual(error, "Stop MIIL before changing its actions.")
        self.assertEqual([action.code for action in miil.actions], [1, 2, 3])

    def test_boundary_cannot_move_backwards(self) -> None:
        miil = MIILController(self.actions)
        miil.start(at(5.0))
        miil.select_action(1, at(6.0))

        with self.assertRaisesRegex(ValueError, "cannot move backwards"):
            miil.select_action(2, at(5.5))

    def test_metadata_is_acquisition_independent_and_declares_clock_alignment(self) -> None:
        miil = MIILController(self.actions)
        miil.start(at(0.0))
        miil.select_action(1, at(1.0))
        miil.stop(at(2.0))

        metadata = miil.metadata_snapshot()

        self.assertEqual(metadata["paradigm"], "miil")
        self.assertEqual(metadata["boundary_method"], "shared_host_monotonic_clock")
        self.assertNotIn("row_counts", repr(metadata))
        self.assertEqual(metadata["intervals"][1]["stimulus_code"], 1)

    def test_pause_resume_preserves_current_instruction(self) -> None:
        miil = MIILController(self.actions)
        miil.start(at(0.0))
        miil.select_action(2, at(2.0))
        miil.pause(at(4.0))

        self.assertEqual(miil.state, MIILState.PAUSED)
        self.assertEqual(miil.current_code, 2)
        self.assertEqual(miil.current_elapsed_s(), 2.0)

        miil.resume(at(6.0))
        self.assertEqual(miil.state, MIILState.RUNNING)
        self.assertEqual(miil.current_code, 2)


if __name__ == "__main__":
    unittest.main()
