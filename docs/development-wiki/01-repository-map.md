# 01 — Repository Map

## Runtime entry points

The production application is started from the repository root with:

```powershell
uv run python -m fundamental.main
```

On PowerShell, `./run_in_powershell.ps1` is a convenience wrapper for the same
command. `fundamental.main.build_app()` is the composition root: it constructs
the acquisition and stimulus models, creates `RecordingSession`, registers the
shared services, and registers every tool window.

The exploratory top-level scripts and notebooks are not application entry
points and are not a substitute for automated tests.

## Directory layout

```text
emgchachito/
├── fundamental/        production application and reusable runtime contracts
│   └── sources/        source adapters and hardware workers
├── DeviceInterface/    pure byte-level protocol parsers/builders
├── analysis/           offline capture inspection and signal diagnostics
├── tests/              automated unit and headless UI tests
├── docs/
│   ├── development-wiki/  maintained implementation and refactoring wiki
│   ├── manuals/           operator-facing procedures
│   └── engineering-notes/ dated reviews and experiment findings
├── device_host_demo/   hardware/API experiments; not imported by production
├── captures/           generated experiment data; ignored by Git
├── pyproject.toml      project metadata and dependencies
└── uv.lock             locked Python dependency graph
```

`reference document/`, hardware demos, and notebooks may contain useful local
evidence, but production code must not import them. If a behavior learned from
a demo is required at runtime, encode it in a protocol test and document the
resulting contract.

## Production module ownership

### Composition and shell

| Module | Responsibility | Important public symbols |
| --- | --- | --- |
| `fundamental/main.py` | Dependency construction and feature registration | `build_app()`, `main()` |
| `fundamental/app_shell.py` | Dear PyGui context, frame loop, services, logs, global shortcuts | `FundamentalApp` |
| `fundamental/commands.py` | GUI-independent command lookup and dispatch | `CommandSpec`, `CommandRegistry`, `CommandContext` |
| `fundamental/window_manager.py` | Lazy construction and visibility of registered tool windows | `ManagedWindow`, `WindowManager` |

The shell must remain unaware of sensor packet formats and experiment-specific
state. Feature modules register callbacks; `main.py` wires their controllers.

### Acquisition and stream contracts

| Module | Responsibility | Important public symbols |
| --- | --- | --- |
| `fundamental/acquisition.py` | Active source set, workers, queues, readiness barrier, lifecycle, health, save dispatch | `AcquisitionController` |
| `fundamental/messages.py` | Small cross-thread messages and shared lifecycle/config defaults | `AcquisitionState`, `SerialConfig`, `WorkerEvent` |
| `fundamental/streams.py` | Immutable schema, block, snapshot, cursor, and plot-series contracts | `StreamSpec`, `FieldSpec`, `StreamBlock`, `CaptureResumeState` |
| `fundamental/capture_store.py` | Full in-memory rows and bounded live plot windows | `CaptureStore` |
| `fundamental/csv_writer.py` | Schema-driven raw CSV, metadata JSON, stimulus sidecar | `save_capture()`, `default_capture_path()` |

### Source, transport, and protocol layers

| Module | Layer | Responsibility |
| --- | --- | --- |
| `fundamental/sources/base.py` | source contract | Shared clock/gate, worker protocols, worker grouping |
| `fundamental/transports.py` | transport | Reusable Serial byte and BLE GATT connection mechanics |
| `fundamental/sources/serial_ads1299.py` | source adapter | Serial worker, ADS1299 schema, packet-to-stream conversion |
| `fundamental/sources/ble_w2.py` | source adapter | Per-device Serial/BLE selection, W2 commands, timing, worker group |
| `fundamental/sources/bwt901.py` | source adapter | Multi-device BWT901 BLE worker, IMU schema, receive-time timestamps |
| `fundamental/sources/myo.py` | source adapter | `pymyo` callbacks and independent EMG/IMU streams |
| `DeviceInterface/ads1299_protocol.py` | protocol | ADS1299 host-frame parsing and counter continuity |
| `DeviceInterface/w2_protocol.py` | protocol | W2 commands, framing, raw/RMS decoding |
| `DeviceInterface/bwt901_protocol.py` | protocol | Pure BWT901 frame decoder and physical-unit scaling |

The intended direction is `source -> transport + protocol`, never `protocol ->
source/UI`. `DeviceInterface` code should be testable with byte strings and no
hardware, threads, Dear PyGui, or filesystem.

### Session and stimulus

| Module | Responsibility | Important public symbols |
| --- | --- | --- |
| `fundamental/recording_session.py` | Coordinates acquisition and exactly one annotation paradigm | `RecordingSession` |
| `fundamental/stimulus_model.py` | Duration-driven linear Timed Schedule | `StimulusController`, `StimulusEvent` |
| `fundamental/miil_model.py` | Manual interval codebook, row/time boundaries, drop/no-stimulus semantics | `MIILController`, `MIILAction`, `CapturePosition` |
| `fundamental/guided_sequence.py` | Pure Enter-paced plan/cursor/attempt state machine | `GuidedSequenceController` |

Stimulus models produce annotation decisions. They do not control ports or BLE
connections. `RecordingSession` translates a user/session action into calls on
both acquisition and the selected annotation runner.

### Dear PyGui feature windows

| Module | Window/feature |
| --- | --- |
| `fundamental/source_config.py` | Source selection, W2/BWT rows, connection settings, health and inspection |
| `fundamental/acquisition_window.py` | Global Start/Pause/Stop/Save controls |
| `fundamental/plot_window.py` | Generic schema-driven live plot slots |
| `fundamental/plot_processing.py` | Pure display transforms, downsampling, scaling, and status text |
| `fundamental/stimulus_window.py` | Timed Schedule, MIIL setup/runtime, Guided Sequence editor/operator console |

Window modules may call session/controller APIs and render view state. They
must not parse packets, mutate raw capture rows, or own worker threads.

### Offline analysis

`analysis/inspect_capture.py` is a separate downstream consumer. It loads saved
CSV, checks continuity and amplitude, computes EMG-oriented metrics, and can
plot a report. Its filtering and RMS computations are analysis operations, not
part of raw acquisition or persistence.

## Where current defaults live

The UI currently has no persisted user-profile/config file. Defaults are Python
constants or dataclass defaults and take effect on the next process start.

| Setting | Source of truth |
| --- | --- |
| ADS1299 port/baud/timeout | `fundamental.messages.DEFAULT_SERIAL_*` and `SerialConfig` |
| Plot window/buffer sizes | `fundamental.messages.DEFAULT_PLOT_*` |
| W2 maximum and protocol defaults | `fundamental.sources.ble_w2.MAX_W2_DEVICES` and `W2Config` |
| Initially displayed W2 rows/ports | `fundamental.sources.ble_w2.DEFAULT_W2_DEVICES` |
| BWT901 maximum, address, UUIDs | constants and dataclasses in `fundamental.sources.bwt901` |
| Myo scan/stream defaults | `MyoBLEConfig` in `fundamental.sources.myo` |
| Timed Schedule initial events | `StimulusController.__init__()` |
| MIIL initial codebook | `fundamental.miil_model.DEFAULT_MIIL_ACTIONS` |

Current experiment defaults at this wiki revision are five Serial W2 rows on
`COM9`, `COM11`, `COM12`, `COM13`, and `COM10`, all at `256000 8N1`, plus one
BWT901 address `E9:34:17:08:9F:4A`. These are deployment defaults, not protocol
constants. A refactor should move deployment profiles out of source modules
without changing normalized config or metadata semantics.

## Dependency and ownership rules

| Concern | Owner | Consumers |
| --- | --- | --- |
| Bytes on a connection | transport/worker | protocol decoder |
| Packet shape and validation | `DeviceInterface` protocol | source adapter/tests |
| Device configuration and packet-to-row mapping | source adapter | acquisition controller |
| Physical Start/Pause/Resume/Stop | `AcquisitionController` | `RecordingSession`, UI |
| Annotation lifecycle | selected stimulus model | `RecordingSession`, UI, writer resolver |
| Cross-domain orchestration | `RecordingSession` | acquisition/stimulus windows |
| Raw rows | `CaptureStore` | plot, save, stimulus boundary snapshots |
| File schema/path/metadata | `csv_writer` + `StreamSpec` | offline tools |
| Display transforms | `plot_processing` | plot window only |

If a change appears to require a reverse dependency—for example, a protocol
parser importing Dear PyGui, or a stimulus model opening a Serial port—the
change belongs in an adapter/coordinator instead.

## Tests as executable maps

Test modules mirror the production boundaries:

- `test_*_protocol` and parser sections validate byte-level behavior;
- source tests validate worker/config/stream adaptation;
- `test_capture_store.py` validates generic stream and persistence behavior;
- `test_recording_session.py` validates cross-domain lifecycle and labels;
- `test_miil_model.py` and `test_guided_sequence.py` validate pure state models;
- `test_*_window.py` and `test_app_shell_keyboard.py` validate headless UI state;
- `test_acquisition_sources.py` validates multi-source coordination.

For a change to a boundary, update the test at that boundary before relying on
a full GUI or hardware run.
