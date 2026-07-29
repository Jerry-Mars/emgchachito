from __future__ import annotations

import unittest
from unittest.mock import patch

import dearpygui.dearpygui as dpg

from fundamental.app_shell import FundamentalApp


class ContextEnterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = FundamentalApp()
        self.calls: list[str] = []
        self.app.register_context_enter_handler(
            lambda _app: self.calls.append("advance") or True
        )

    def _press(self, key: int = dpg.mvKey_Return) -> None:
        with (
            patch.object(self.app.window_manager, "is_shown", return_value=False),
            patch.object(self.app, "_is_ctrl_down", return_value=False),
            patch.object(self.app, "_is_shift_down", return_value=False),
            patch.object(self.app, "_is_alt_down", return_value=False),
            patch.object(self.app, "_focused_item_consumes_enter", return_value=False),
        ):
            self.app._handle_enter_press(user_data=key)

    def test_press_release_latch_allows_exactly_one_action_per_key_press(self) -> None:
        self._press()
        self._press()
        self.assertEqual(self.calls, ["advance"])

        self.app._handle_enter_release(user_data=dpg.mvKey_Return)
        self._press()
        self.assertEqual(self.calls, ["advance", "advance"])

    def test_main_and_numpad_enter_are_supported(self) -> None:
        self._press(dpg.mvKey_Return)
        self.app._handle_enter_release(user_data=dpg.mvKey_Return)
        self._press(dpg.mvKey_NumPadEnter)
        self.assertEqual(self.calls, ["advance", "advance"])

    def test_command_palette_consumes_enter_before_context_handler(self) -> None:
        with (
            patch.object(self.app.window_manager, "is_shown", return_value=True),
            patch.object(self.app, "_execute_palette_input") as execute,
        ):
            self.app._handle_enter_press(user_data=dpg.mvKey_Return)

        execute.assert_called_once_with()
        self.assertEqual(self.calls, [])

    def test_modifier_or_focused_editor_blocks_context_handler(self) -> None:
        with (
            patch.object(self.app.window_manager, "is_shown", return_value=False),
            patch.object(self.app, "_is_ctrl_down", return_value=True),
            patch.object(self.app, "_is_shift_down", return_value=False),
            patch.object(self.app, "_is_alt_down", return_value=False),
        ):
            self.app._handle_enter_press(user_data=dpg.mvKey_Return)
        self.app._handle_enter_release(user_data=dpg.mvKey_Return)

        with (
            patch.object(self.app.window_manager, "is_shown", return_value=False),
            patch.object(self.app, "_is_ctrl_down", return_value=False),
            patch.object(self.app, "_is_shift_down", return_value=False),
            patch.object(self.app, "_is_alt_down", return_value=False),
            patch.object(self.app, "_focused_item_consumes_enter", return_value=True),
        ):
            self.app._handle_enter_press(user_data=dpg.mvKey_Return)

        self.assertEqual(self.calls, [])

    def test_focused_input_and_button_consume_enter_but_disabled_item_does_not(self) -> None:
        for item_type in (
            "mvAppItemType::mvInputText",
            "mvAppItemType::mvButton",
        ):
            with (
                patch("fundamental.app_shell.dpg.get_focused_item", return_value=10),
                patch("fundamental.app_shell.dpg.is_item_enabled", return_value=True),
                patch(
                    "fundamental.app_shell.dpg.get_item_info",
                    return_value={"edited_handler_applicable": False},
                ),
                patch("fundamental.app_shell.dpg.get_item_type", return_value=item_type),
            ):
                self.assertTrue(self.app._focused_item_consumes_enter())

        with (
            patch("fundamental.app_shell.dpg.get_focused_item", return_value=10),
            patch("fundamental.app_shell.dpg.is_item_enabled", return_value=False),
        ):
            self.assertFalse(self.app._focused_item_consumes_enter())


if __name__ == "__main__":
    unittest.main()
