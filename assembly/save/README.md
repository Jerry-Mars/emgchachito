# Save

`assembly.save` 是一个刻意保持很薄的 normalized-data persistence capability。

当前 V1 contract：

```text
input
    StreamSchema + committed StreamRow

output
    one HDF5 file

Start
    create a new file and begin accepting subsequently committed rows

Stop
    flush and close the file
```

它不负责：

- worker/device lifecycle；
- BLE/serial；
- Plot；
- stimulus；
- pause/resume；
- filtering/resampling/alignment；
- derived timestamps；
- field projection；
- offline post-processing。

## HDF5 layout

每个 normalized stream 对应 `/streams/stream_XXXX` group。真实 `stream_id` 保存在 group attribute 中，避免 HDF5 `/` path semantics 改变 stream ID。

每个 stream 保存：

```text
runtime_index
host_monotonic_ns
host_unix_ns
values[row, field]
```

schema metadata 保存：

```text
stream_id
field_keys
nominal_rate_known
nominal_rate_hz   # only when known
```

Recorder 不根据 nominal rate 重建时间，也不修改数值。

## Composition seam

`StreamStoreTap` 是可选的 composition helper：

```text
Ingestor
   ↓
StreamStoreTap
   ├→ RealtimeStreamStore
   └→ H5StreamRecorder (only while recording)
```

已有 Plot 路径仍然可以直接：

```text
Ingestor → RealtimeStreamStore → Plot
```

不需要经过 Recorder 或 Tap。

## Executable checks

无硬件：

```bash
uv run python -m assembly.testers.recorder_tester
```

最小真实 W2 Save dashboard：

```bash
uv run python -m assembly.live_w2_save
```

其中 acquisition 在 dashboard 生命周期内持续运行；Save Start/Stop 只控制 recorder。
