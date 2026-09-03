# Assembly Testers

这里放置可直接运行的 capability tester，用来验证 `assembly` 中的独立功能边界。

Tester 不是 production runtime，也不是完整应用。它们的目标是让开发者用最短路径回答：

1. worker 是否能独立完成 startup / acquisition / shutdown；
2. raw record 的真实结构和计数是否符合预期；
3. 可选地，worker record 是否能通过现有 `QueuePump + Ingestor + RealtimeStreamStore` 完成 normalization。

当前 tester：

- `myo_worker_tester.py`
- `w2_worker_tester.py`
- `bwt901_worker_tester.py`
- `recorder_tester.py`
- `miil_valid_simple.py`：不依赖 acquisition，用确定性的 host-clock boundary 演示 MIIL codebook、动作切换、drop/no_stimulus、stop、interval 查询与 `code_at()` 对齐接口。

Worker tester 当前支持两种模式：

- `raw`：只验证 worker lifecycle 与 raw records；
- `ingest`：在 raw worker 后追加现有 ingestor/store，验证 acquisition normalization seam。

Tester 的交互形式按 capability 选择，不把 Tyro 当成固定架构要求。当前 worker tester 使用 `tyro` 方便传入硬件参数；`recorder_tester.py` 使用普通 `argparse`，后续也可以按需要使用 notebook、GUI 或其他形式。

示例：

```bash
uv run python -m assembly.testers.w2_worker_tester --help
uv run python -m assembly.testers.bwt901_worker_tester --help
uv run python -m assembly.testers.myo_worker_tester --help
```

## Tester 设计约束

- tester 可以依赖 production module；production module 不依赖 tester；
- tester 不引入 Plot / DearPyGui；
- 不为了 tester 修改 `StreamSchema`、`StreamSample`、`StreamRow`、`RealtimeStreamStore`、`WorkerGroup`、`QueuePump` 的语义；
- 不追求统一所有设备的启动细节。BLE discovery、serial port、device metadata 等真实差异应在各自 tester 中保持显式；
- tester 是 executable specification，可以被以后更成熟的 CLI/GUI composition 替换。
