# Fundamental Serial Acquisition

This directory contains the minimal command-driven serial acquisition GUI. It
keeps serial configuration, acquisition control, live raw plotting, stimulus
timeline labeling, and CSV saving.

## Run

From `chachito`:

```bash
uv run python -m fundamental.main
```

In the viewport:

- `Ctrl+Shift+P` opens the command palette.
- Run `serial` to open serial settings.
- Run `acquisition` to open recording controls.
- Run `plot` to open live plotting.
- Run `stimulus` to open the stimulus schedule and experiment timeline.
- `Esc` closes the active command-opened window.
- The `Log Output` window is the only default visible tool window and can be
  moved, collapsed, or docked when Dear PyGui docking is available.

## Commands

The command palette intentionally exposes only window-level commands:

- `serial`: open the serial configuration window.
- `acquisition`: open start, pause, stop, and save controls.
- `plot`: open the live raw serial plot only.
- `stimulus`: open the stimulus schedule and experiment timeline.

Acquisition controls are kept inside the acquisition window:

- `Start`: start or resume acquisition.
- `Pause`: pause acquisition and keep buffered samples.
- `Stop`: stop acquisition and keep buffered samples.
- `Save`: save buffered samples while paused or stopped.

Stimulus controls use the same acquisition controller:

- `Start`: start acquisition if needed and start the stimulus schedule.
- `Pause`: pause acquisition and the stimulus timeline; no new samples are stored.
- `Resume`: resume acquisition and continue the current stimulus event.
- `Stop`: stop acquisition and close the current stimulus event.
- `Restart Event`: mark the current event attempt as invalid and restart it.
- `Save`: save EMG samples with `stimulus_code` plus a `.stimulus.csv` sidecar log.

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
uint8 emg_channel_count
8 * 24-bit signed channel codes, MSB first
uint64 frame_counter, big-endian
0xBB
```

Default serial settings are `921600 8N1`, no parity, no flow control.

CSV output uses:

```text
time_s,frame_counter,dropped_frames_before,emg_channel_count,ch1_code,ch2_code,ch3_code,ch4_code,ch5_code,ch6_code,ch7_code,ch8_code
```

Stimulus saves add one sample-aligned numeric column:

```text
time_s,frame_counter,dropped_frames_before,emg_channel_count,stimulus_code,ch1_code,...
```

The stimulus sidecar maps numeric codes to labels and actual event intervals:

```text
event_index,stimulus_code,planned_code,label,start_time_s,end_time_s,status
```
