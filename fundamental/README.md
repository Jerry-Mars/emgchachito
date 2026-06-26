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

The current hardware uses the ADS1299 binary host-frame protocol documented in
`DeviceInterface/EMG_HOST_FRAME_PROTOCOL.md`:

```text
0xAA
8 * 24-bit signed channel codes, MSB first
uint64 frame_counter, big-endian
0xBB
```

Default serial settings are `921600 8N1`, no parity, no flow control.

CSV output uses:

```text
time_s,frame_counter,dropped_frames_before,ch1_code,ch2_code,ch3_code,ch4_code,ch5_code,ch6_code,ch7_code,ch8_code
```
