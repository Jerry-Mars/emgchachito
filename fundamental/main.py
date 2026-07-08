"""Entry point for the fundamental serial acquisition GUI."""

from __future__ import annotations

from fundamental.acquisition import AcquisitionController
from fundamental.acquisition_window import register as register_acquisition_window
from fundamental.app_shell import FundamentalApp
from fundamental.plot_window import register as register_plot_window
from fundamental.serial_config import register as register_serial_config
from fundamental.stimulus_model import StimulusController
from fundamental.stimulus_window import register as register_stimulus_window


def build_app() -> FundamentalApp:
    app = FundamentalApp()
    acquisition = AcquisitionController()
    stimulus = StimulusController()
    app.register_service("acquisition", acquisition)
    app.register_service("stimulus", stimulus)
    register_serial_config(app, acquisition)
    register_acquisition_window(app, acquisition)
    register_plot_window(app, acquisition)
    register_stimulus_window(app, acquisition, stimulus)
    return app


def main() -> None:
    build_app().run()


if __name__ == "__main__":
    main()
