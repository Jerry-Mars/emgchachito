# EMGChachito Development Wiki

This wiki is the maintained technical map of the acquisition application. It
documents the code as it exists on **2026-07-29**, including heterogeneous EMG
and IMU streams, coordinated W2/BWT901 acquisition, sample-aligned stimulus
labels, and MIIL Guided Sequence.

It has two purposes:

1. let a developer safely extend or debug the current application; and
2. preserve contracts and invariants that should survive a future refactor or
   extraction into reusable packages.

The wiki describes current behavior, not an aspirational rewrite. Proposed
changes are isolated in the refactoring guide.

## Reading paths

### New contributor

1. [Repository map](01-repository-map.md)
2. [Architecture and dependency boundaries](02-architecture.md)
3. [Runtime lifecycle and failure handling](03-runtime-lifecycle.md)
4. [Testing and debugging](08-testing-debugging.md)

### Adding or changing a device

1. [Devices, transports, and protocols](04-devices-transports-protocols.md)
2. [Streams, timing, buffering, and storage](05-streams-timing-storage.md)
3. [Extension playbooks](09-extension-playbooks.md)

### Changing stimulus or experiment behavior

1. [Stimulus and labeling](06-stimulus-labeling.md)
2. [UI, commands, keyboard, and plot](07-ui-commands-plot.md)
3. [Extension playbooks](09-extension-playbooks.md)

### Planning a refactor

1. [Architecture and dependency boundaries](02-architecture.md)
2. [Streams, timing, buffering, and storage](05-streams-timing-storage.md)
3. [Refactoring guide](10-refactoring-guide.md)

## Chapters

| Chapter | Scope |
| --- | --- |
| [01 — Repository map](01-repository-map.md) | Directory/module ownership, entry points, configuration locations |
| [02 — Architecture](02-architecture.md) | Layers, dependencies, coordinators, stable boundaries |
| [03 — Runtime lifecycle](03-runtime-lifecycle.md) | Start barrier, queues, Pause/Resume/Stop/Save, fail-fast behavior |
| [04 — Devices/transports/protocols](04-devices-transports-protocols.md) | ADS1299, W2, BWT901, Myo, BLE/Serial separation |
| [05 — Streams/timing/storage](05-streams-timing-storage.md) | Schema contracts, clocks, buffers, CSV and metadata |
| [06 — Stimulus/labeling](06-stimulus-labeling.md) | Timed Schedule, MIIL, Guided Sequence, sample labels |
| [07 — UI/commands/plot](07-ui-commands-plot.md) | Dear PyGui shell, window registration, keyboard routing, plotting |
| [08 — Testing/debugging](08-testing-debugging.md) | Test map, commands, diagnostics, hardware checks |
| [09 — Extension playbooks](09-extension-playbooks.md) | Step-by-step recipes for common additions |
| [10 — Refactoring guide](10-refactoring-guide.md) | Invariants, debt register, extraction map, phased target architecture |

## Core invariants

These rules are intentionally repeated throughout the wiki because silently
breaking one can invalidate an experiment:

- Device protocol parsing stays outside UI, plotting, and persistence.
- A physical independently sampled signal is stored as its own stream; raw
  streams are not padded, truncated, or silently resampled to equal length.
- `StreamSpec` is the schema contract consumed by capture, plot, and CSV code.
- `AcquisitionController` owns physical source lifecycle. Stimulus code does
  not open or close device connections.
- `RecordingSession` is the only coordinator of acquisition and annotation
  lifecycle.
- Managed W2/BWT901 sources cross a readiness barrier before the shared capture
  gate opens; a required-source failure stops the capture set.
- Pause excludes wall-clock pause time from the logical capture timeline.
- MIIL boundaries retain both shared time and per-stream next-row cursors.
- Raw sensor values remain raw in saved CSV files. Display transforms do not
  modify persisted evidence.
- One experiment receives one directory with independent stream CSV files,
  shared metadata, and an optional stimulus event sidecar.

## Documentation maintenance rule

Update this wiki in the same commit when a change affects any of the following:

- source/transport/protocol ownership;
- source defaults or maximum device counts;
- stream IDs, field keys, units, timestamp meaning, or output file names;
- Start/Pause/Resume/Stop/Save semantics;
- readiness, failure, or queue behavior;
- stimulus codes, boundary semantics, or saved label metadata;
- command names, keyboard routing, or configuration workflow;
- extension APIs or refactoring assumptions.

Prefer links to modules and symbol names over fragile copied code. Mermaid
diagrams are source-controlled text and should be changed with the behavior
they describe.

## Related documents

- [MIIL operator manual](../manuals/miil-user-manual.md)
- [Engineering notes index](../engineering-notes/README.md)
- [ADS1299 host-frame protocol](../../DeviceInterface/EMG_HOST_FRAME_PROTOCOL.md)
- [Capture analysis tool](../../analysis/README.md)
