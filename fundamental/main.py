"""Entry point for the fundamental serial acquisition GUI."""

from __future__ import annotations

from fundamental.acquisition import AcquisitionController
from fundamental.acquisition_window import register as register_acquisition_window
from fundamental.app_shell import FundamentalApp
from fundamental.plot_window import register as register_plot_window
from fundamental.recording_session import RecordingSession
from fundamental.source_config import register as register_source_config
from fundamental.stimulus_model import StimulusController
from fundamental.stimulus_window import register as register_stimulus_window


def build_app() -> FundamentalApp:
    app = FundamentalApp()
    acquisition = AcquisitionController()
    stimulus = StimulusController()
    session = RecordingSession(acquisition, stimulus)
    app.register_service("acquisition", acquisition)
    app.register_service("stimulus", stimulus)
    app.register_service("recording_session", session)
    app.register_frame_callback(lambda frame_app: session.on_frame(frame_app.log))
    register_source_config(app, acquisition)
    register_acquisition_window(app, session)
    register_plot_window(app, acquisition.buffer)
    register_stimulus_window(app, session)
    return app


def main() -> None:
    build_app().run()


if __name__ == "__main__":
    main()
