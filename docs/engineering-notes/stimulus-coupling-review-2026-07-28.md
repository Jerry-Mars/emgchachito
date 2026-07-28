# Stimulus 范式耦合审阅（2026-07-28）

## 结论

当前 Stimulus 是“顺序、固定时长、单标签”的实验时间表。它适合简单肌电
范式，也能在停止状态下通过 GUI 修改后立即用于下一次实验。

Source、Transport、Protocol 和 Plot 不依赖具体 Stimulus。耦合主要集中在：

```text
Stimulus Window ──> StimulusController
                         │
                         v
                  RecordingSession ──> Acquisition lifecycle
                         │
                         v
                   CSV label resolver + event sidecar
```

因此，改变简单的事件顺序、code、label 或 duration 不需要修改采集核心；复杂
范式则不能只替换事件列表，但仍可通过小型接口隔离，避免侵入 Source 和
Acquisition。

## 当前可以表达的内容

`StimulusEvent` 只有：

- `code`
- `label`
- `duration_s`

当前支持：

- 一维顺序事件；
- 通过重复添加事件表达动作重复和休息段；
- Start、Pause、Resume、Stop；
- 重做当前事件，旧尝试保存为 code `-1`；
- 按采集时间自动切换下一事件；
- 每个样本保存一个整数 `stimulus_code`；
- 实际事件尝试保存到独立 sidecar。

运行或暂停中禁止修改 schedule；停止后修改会在下一次运行立即生效。这是对实验
完整性的合理限制，但当前 GUI 修改只存在内存中，关闭程序后不会保留，也没有
范式预设的导入导出。

## 当前不能原生表达的内容

- block、trial、phase、repeat 层级；
- 随机顺序、平衡随机、随机种子；
- 条件分支、自适应规则；
- 键盘、按钮或受试者响应驱动的事件结束；
- 不定时长或实验员手动推进；
- 图片、全屏文字、声音或外部刺激设备；
- 反应时间、正确率和响应内容；
- block、动作、阶段等并行多层标签；
- 精确 TTL 或硬件同步刺激 onset；
- 范式结束后的可配置 post-recording 时间。

当前窗口主要是 schedule 编辑和状态文字，不是一个精确的视觉或听觉刺激呈现器。

## 模块耦合审阅

### Source 与 Plot

Source、Transport 和 Protocol 不知道 Stimulus 的存在。Plot 也只读取
CaptureStore，不控制范式。新增范式不应修改这些模块。

未来如需在 Plot 中显示刺激区间，应添加只读 annotation 接口，而不是让 Plot
直接访问或控制 StimulusController。

### RecordingSession 与 Acquisition

双方协调集中在 `RecordingSession`：

- 等所有采集设备 ready 后再启动 Stimulus；
- Pause、Resume、Stop 同时作用于采集和 Stimulus；
- 采集失败会停止 Stimulus；
- 当前范式自然完成会自动停止整次采集。

前三项是合理的稳定行为。最后一项是硬编码策略：若新范式要求刺激结束后继续记录
基线，必须将完成动作改为可配置策略。

### 时间轴耦合

当前所有 Stimulus 操作均由 `acquisition.buffer.latest_time_s` 驱动。
CaptureStore 在多 Stream 时返回所有流中的最大时间。

这会带来一个重要问题：多台 W2 的时间是在首包锚定后按各自配置的 1000 Hz
重建的，最快 Stream 可能逐渐领先其他设备和实际共享采集时钟。因此 Stimulus
切换和结束可能被最快的流提前驱动。

此外，事件状态由应用 frame callback 更新。event log 会使用计划边界补齐精确
时间，但实际界面内容切换可能晚一个或多个 GUI frame，且当前没有记录真实呈现
onset。

在加入新范式前，建议先提供权威的 `timeline_time_s`：

- 托管 W2/BWT 采集使用共享 CaptureClock；
- Stimulus 不再直接读取多流最大样本时间；
- 保存标签仍使用同一个时间坐标；
- 若需要精确刺激呈现，额外记录 planned onset 与 actual onset。

### 保存边界

当前保存接口中值得保留的设计：

- `stimulus_code_at(time_s)` 是按时间查询 code 的接口；
- CSV Writer 只接收 resolver，不依赖 StimulusController；
- 同一 resolver 应用于各 Stream 自己的时间戳；
- 实际事件记录保存在独立 sidecar。

当前限制：

- 没有保存完整计划范式快照；
- 没有范式名称、版本、参数和随机种子；
- 每个样本只能有一个整数 code；
- sidecar 列固定，不能无损保存 block、trial、response 等复杂信息。

为兼容已有分析脚本，传感器 CSV 中的单一 `stimulus_code` 应继续保留。复杂信息应
放在范式 JSON 和扩展事件 sidecar 中，而不是不断扩宽每个传感器文件。

### GUI

`stimulus_window.py` 直接访问 schedule、current event、attempt 和具体的
`code/label/duration_s` 字段，是当前最紧的耦合点。增加事件字段就可能同时修改
模型和窗口。

`RecordingSession` 对 Controller 的实际方法依赖较小，后续容易抽成 Protocol；
`main.py` 已采用显式构造和注入，替换实例本身并不困难。

## 建议保留的稳定接口

- 基于采集时间的 `start/update/pause/resume/stop` 生命周期；
- `stimulus_code_at(time_s)`；
- 实际事件 sidecar；
- `RecordingSession` 作为采集与范式唯一协调点；
- Source 和 Plot 不依赖具体范式；
- 原始传感器 CSV 保留单一 `stimulus_code`。

不应视为长期稳定扩展接口：

- 当前三字段 `StimulusEvent`；
- GUI 对 Controller 内部字段的直接访问；
- 固定 sidecar 列；
- “范式完成必然停止采集”；
- 用最大样本时间代表真实刺激呈现时刻。

## 最小演进路径

在收到具体新范式前不提前重构。收到需求后按两类处理：

### 仍是固定时长顺序范式

直接生成新的 `StimulusEvent` 列表或新增范式预设；保留 Controller、
RecordingSession、Acquisition、Source 和 Plot。补充范式定义快照即可。

### 包含随机化、层级、响应或真实刺激呈现

1. 分开不可变的 `ParadigmDefinition` 和运行时 Runner。
2. 为 RecordingSession 定义很小的 `StimulusRunner` Protocol。
3. 保留现有 Controller 作为 `LinearScheduleRunner`。
4. GUI 通过通用 view model 操作，不直接修改 `.schedule`。
5. 使用权威 `timeline_time_s`，不再由最快 Stream 驱动。
6. 将视觉、声音或外部设备 Presenter 作为独立组件。
7. 保存范式版本、参数、编译后的实际顺序和随机种子。
8. 保留旧 `stimulus_code`，复杂信息进入 JSON/sidecar。

## 新范式需求确认清单

收到具体要求后应先确认：

1. Stimulus 只是数据标注，还是软件需要真正呈现文字、图片或声音？
2. 顺序是否固定，是否需要随机化、重复、平衡和随机种子？
3. 是否存在 block/trial/phase 层级？
4. 事件由时间、实验员、受试者响应还是外部触发结束？
5. Pause 后继续、重做还是判当前 trial 无效？
6. 范式结束后立即停止采集，还是保留前后基线？
7. 单一 code 是否足够，还需保存哪些 trial/block/response 字段？
8. onset 精度要求是 GUI 级、采样级还是 TTL/硬件级？
9. 范式通过 GUI、JSON/YAML 还是 Python 预设维护？

## 测试缺口

已有测试覆盖设备 ready 后启动、按样本时间标签、Stop/失败联动、完成后停止采集、
Restart Event 无效标记和 sidecar 保存。

新增复杂范式前应补充：

- Pause/Resume 后事件剩余时间；
- schedule 编辑校验和 UI 行为；
- 一个 frame 跨越多个事件的边界；
- 范式快照及随机种子复现；
- planned onset 与 actual onset 偏差；
- 范式结束后继续采集策略。
