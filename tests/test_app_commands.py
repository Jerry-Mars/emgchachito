from __future__ import annotations

import unittest
from pathlib import Path


class AppCommandTests(unittest.TestCase):
    def test_source_replaces_serial_config_command(self) -> None:
        main_source = Path("fundamental/main.py").read_text(encoding="utf-8")

        self.assertIn("register_source_config(app, acquisition)", main_source)
        self.assertNotIn("register_serial_config(app, acquisition)", main_source)


if __name__ == "__main__":
    unittest.main()
