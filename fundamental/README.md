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

The plot window is display-only. It reads plottable scalar series declared by
the active source schema, lets the user add or delete plot slots, and offers
per-slot series, compatible signal view, and scale controls. It does not know
about ADS1299, W2, or Myo packet shapes, and it does not own acquisition start,
pause, stop, or save behavior. The existing plot-card layout and optional hidden
controls are preserved.

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

- `acquisition`: acquisition controller, selected source, and capture store owner.
- `stimulus`: sample-time stimulus schedule and annotation model.
- `recording_session`: shared `start/pause/resume/stop/save` coordination across
  acquisition and optional stimulus labels.

## Acquisition Sources

The acquisition controller owns the selected source and blocks source switching
unless acquisition is stopped. Current sources:

- `serial_ads1299`: serial ADS1299 worker using
  `DeviceInterface/ads1299_protocol.py` (accepts both the current 35-byte frame
  and the legacy 34-byte frame).
- `ble_w2`: BLE W2 worker using `DeviceInterface/w2_protocol.py`.
- `ble_myo`: Myo armband worker using `pymyo` over `bleak`; it can acquire raw
  8-channel EMG, IMU, or both.

W2 defaults to scanning for an advertised name containing `RunE W2`. Myo
defaults to the Myo control-service UUID and an advertised name containing
`Myo`. Enter an address only when intentionally pinning acquisition to one
known unit; demo addresses are device-specific.

The `source` command opens the shared source window. Its data inspection block
shows the selected source's worker, transport/parser, and declared stream
schemas.

## Heterogeneous Stream Contract

All production sources publish the same small contract:

- `StreamSpec` declares a stream ID, nominal rate, time source, and ordered
  `FieldSpec` columns.
- `StreamBlock` carries a validated batch from exactly one independently sampled
  stream.
- `CaptureStore` retains full rows for saving and bounded per-series windows for
  live consumers.

ADS1299, W2, and Myo source modules own their protocol-specific parsing. Plotting
and CSV persistence consume only the schema and store interfaces. A future
sensor can therefore add fields or streams without adding device branches to
the plot or writer. A future 3D IMU view can query quaternion/IMU series from the
same store without depending on BLE code.

## Myo Timing and Files

Myo EMG and IMU belong to one device connection and one capture session, but
they are not delivered as one simultaneous row: pymyo receives independent BLE
notifications at different native rates (approximately 200 EMG samples/s and
50 IMU samples/s). The raw acquisition therefore keeps two lossless tables
instead of silently repeating IMU values, dropping EMG rows, or inventing an
interpolation policy:

```text
capture.myo_emg.csv
capture.myo_imu.csv
capture.metadata.json
```

The JSON sidecar ties both files to the same capture and records source config,
device information, schemas, nominal rates, row counts, and timestamp meaning.
If only EMG or only IMU is enabled, the single stream uses the requested CSV
path directly. A synchronized/fused table should be an explicit downstream
transformation whose resampling policy is chosen for the analysis, not a change
to the raw evidence.

Myo does not provide a sample timestamp. Its columns therefore mean:

- `time_s`: capture-relative time reconstructed independently for each stream
  from its nominal rate; suitable for the common plot/capture timeline.
- `host_rx_time_s`: capture-relative host callback time; useful for auditing BLE
  notification jitter or gaps. The two EMG samples in one notification share
  this value.

No artificial `sample_index`, `Cnt`, or duplicate wall-clock columns are stored:
CSV row order already supplies a lossless index. Device-native counters are kept
when they actually exist (ADS1299 `frame_counter`). Pause/resume uses an active
capture timeline, so time continues from the last stored sample rather than
including the wall-clock pause.

## Schema-driven CSV

CSV headers come directly from each `StreamSpec`: metadata fields first,
optional `stimulus_code`, then signal fields. The writer has no device-specific
header branches. One populated stream produces one CSV; multiple independent
streams produce one raw CSV per stream plus one shared metadata sidecar.

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
