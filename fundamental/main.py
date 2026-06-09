"""Entry point for the fundamental serial acquisition GUI."""

from __future__ import annotations

from fundamental.acquisition import AcquisitionController
from fundamental.app_shell import FundamentalApp
from fundamental.plot_window import register as register_plot_window
from fundamental.serial_config import register as register_serial_config


def build_app() -> FundamentalApp:
    app = FundamentalApp()
    controller = AcquisitionController()
    app.register_service("acquisition", controller)
    register_serial_config(app, controller)
    register_plot_window(app, controller)
    return app


def main() -> None:
    build_app().run()


if __name__ == "__main__":
    main()
