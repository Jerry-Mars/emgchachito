# Fundamental Serial Acquisition

This directory contains the minimal command-driven serial acquisition GUI. It
only keeps serial configuration, live raw plotting, and CSV saving.

## Run

From `chachito`:

```bash
uv run python -m fundamental.main
```

In the viewport:

- `Ctrl+Shift+P` opens the command palette.
- Run `serial` to open serial settings.
- Run `plot` to open live plotting.
- `Esc` closes the active command-opened window.
- The `Log Output` window is the only default visible tool window and can be
  moved, collapsed, or docked when Dear PyGui docking is available.

## Commands

The command palette intentionally exposes only window-level commands:

- `serial`: open the serial configuration window.
- `plot`: open the live raw serial plot.

Acquisition controls are kept inside the plot window:

- `Start`: start or resume acquisition.
- `Pause`: pause acquisition and keep buffered samples.
- `Stop`: stop acquisition and keep buffered samples.
- `Save`: save buffered samples while paused or stopped.

## Extension Contract

Modules expose one registration function:

```python
def register(app: FundamentalApp) -> None:
    app.window_manager.register(ManagedWindow(...))
    app.register_command(CommandSpec(...))
```

Optional integration points:

- `app.register_frame_callback(callback)` for UI-thread queue draining.
- `app.register_shutdown_callback(callback)` for thread or resource cleanup.
- `app.log(message)` for status and command results.
- `app.execute_command("command.name")` for top-level command dispatch.
- `app.register_service(name, service)` for shared controllers.

This keeps each feature's UI beside its own feature logic while the shell owns
only lifecycle, command routing, window routing, and logs.

## Serial Protocol

Each sample is expected as one UTF-8 line with six comma-separated integer
values:

```text
123,456,789,321,654,987
```

CSV output uses:

```text
time_s,ch1,ch2,ch3,ch4,ch5,ch6
```
