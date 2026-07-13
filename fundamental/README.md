# Fundamental Acquisition

This directory contains the minimal command-driven acquisition GUI. The
command/window model stays as the top-level interaction pattern, while a shared
recording session coordinates acquisition, optional stimulus labels, and CSV
saving.

## Run

From `chachito`:

```bash
uv run python -m fundamental.main
```

In the viewport:

- `Ctrl+Shift+P` opens the command palette.
- Run `source` to select and configure the acquisition source.
- Run `acquisition` to open recording controls.
- Run `plot` to open live plotting.
- Run `stimulus` to open the stimulus schedule and experiment timeline.
- `Esc` closes the active command-opened window.
- The `Log Output` window is the only default visible tool window and can be
  moved, collapsed, or docked when Dear PyGui docking is available.

## Commands

The command palette intentionally exposes only window-level commands:

- `source`: open acquisition source selection and configuration.
- `acquisition`: open start, pause, stop, and save controls.
- `plot`: open the live signal plot monitor only.
- `stimulus`: open the stimulus schedule and experiment timeline.

Acquisition controls are kept inside the acquisition window and call the shared
recording session:

- `Start`: start or resume acquisition.
- `Pause`: pause acquisition, and pause stimulus too when this capture has a
  stimulus timeline.
- `Stop`: stop acquisition, and close any active stimulus event at the latest
  sample time.
- `Save`: save buffered samples while paused or stopped. If the current capture
  has a stimulus timeline, save sample-aligned `stimulus_code` and the sidecar
  event log.

Stimulus controls use the same recording session:

- `Start`: start acquisition if needed and start the stimulus schedule.
- `Pause`: pause acquisition and the stimulus timeline; no new samples are stored.
- `Resume`: resume acquisition and continue the current stimulus event.
- `Stop`: stop acquisition and close the current stimulus event.
- `Restart Event`: mark the current event attempt as invalid and restart it.
- `Save`: save EMG samples with `stimulus_code` plus a `.stimulus.csv` sidecar log.

The plot window is display-only. It reads the acquisition buffer, lets the user
add or delete plot slots, and offers per-slot channel, signal view, and scale
controls without owning acquisition start, pause, stop, or save behavior. Slot
controls can be hidden to give each plot more vertical space.

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

The current shared services are:

- `acquisition`: acquisition controller, selected source, and frame buffer owner.
- `stimulus`: sample-time stimulus schedule and annotation model.
- `recording_session`: shared `start/pause/resume/stop/save` coordination across
  acquisition and optional stimulus labels.

## Acquisition Sources

The acquisition controller owns the selected source and blocks source switching
unless acquisition is stopped. Current sources:

- `serial_ads1299`: serial ADS1299 worker using `DeviceInterface/ads1299_protocol.py`.
- `ble_w2`: BLE W2 worker using `DeviceInterface/w2_protocol.py`.

The `source` command opens the shared source window. Its data inspection block
shows the selected source's worker handle, transport handle, parser, and current
`SampleFrame` output shape.

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
