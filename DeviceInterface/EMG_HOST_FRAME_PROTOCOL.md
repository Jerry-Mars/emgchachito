# ADS1299 EMG 上位机串口解析指南

本文档描述当前固件实际通过 `USART1` 发给上位机的 EMG 数据帧格式。它对应当前项目代码，不包含未发送的 ADS1299 status 字节、CRC/checksum、滤波数据或频谱结果格式。

## 1. 串口连接参数

当前 EMG 数据从 `USART1` 输出，上位机串口参数应配置为：

| 参数 | 值 |
|---|---|
| Baud rate | `921600` |
| Data bits | `8` |
| Stop bits | `1` |
| Parity | `None` |
| Flow control | `None` |
| Encoding | 二进制流，不是 ASCII 文本 |

推荐上位机串口读取方式：

- 按字节流读取，不要按文本行读取。
- 不要使用 `readline()` 或字符串解码。
- DMA 发送块不保证和帧边界对齐，上位机必须在接收缓冲区中搜索帧头 `0xAA`，读取 `emg_channel_count`，再检查固定长度和帧尾 `0xBB`。

## 2. 当前固件模式

当前任务初始化后只保留 DIP 模式轮询、ADS1299 采集和 `USART1` 数据流输出。

| 模式 | 进入条件 | 上位机输出 |
|---|---|---|
| `SysMode_0` | `DIP1 == 0` | 当前 EMG 二进制帧 |
| `SysMode_1` | `DIP1 == 1` | 当前 EMG 二进制帧 |

`DIP2` 当前被忽略。`SysMode_0` 和 `SysMode_1` 的上位机帧格式相同，当前版本没有单独的滤波输出模式。

## 3. ADS1299 采集配置

当前固件在进入 EMG 模式时写入以下 ADS1299 配置：

| 项目 | 当前值 |
|---|---|
| `CONFIG1` | `0xD4` |
| 采样率 | `1000 Hz` |
| `CONFIG2` | `0xC0`, 内部测试信号关闭 |
| `CONFIG3` | `0xEC` |
| `CONFIG4` | `0x00`, continuous conversion |
| `CH1SET..CH8SET` | `0x20`, normal input, SRB2 off, gain = `4` |
| `MISC1` | `0x20`, SRB1 connected as common inverting reference |
| `BIASSENSP` | 当前活动通道 mask，当前为 `0xFF` |
| `BIASSENSN` | 当前活动通道 mask，当前为 `0xFF` |
| `LOFF` / `LOFFSENSP` / `LOFFSENSN` / `LOFFFLIP` | `0x00` |

ADS1299 每次 DRDY 后读取完整 `27 bytes`：

```text
status[3] + raw_ch1[3] + ... + raw_ch8[3]
```

固件不会把 `status[3]` 发给上位机。固件先把 8 路 ADS1299 原始通道转换成 host channel，再发送 host channel。

## 4. Host Channel 映射

当前 host frame 固定保留 8 个 `int24` 通道槽位，并在帧头后发送 `emg_channel_count`。当前 `emg_channel_count = 4`，表示只有前 4 个槽位是有效 EMG 差分结果：

| Host 字段 | 当前内容 |
|---|---|
| `CH1` | `ADS_raw_CH1 - ADS_raw_CH2` |
| `CH2` | `ADS_raw_CH3 - ADS_raw_CH4` |
| `CH3` | `ADS_raw_CH5 - ADS_raw_CH6` |
| `CH4` | `ADS_raw_CH7 - ADS_raw_CH8` |
| `CH5` | `0` |
| `CH6` | `0` |
| `CH7` | `0` |
| `CH8` | `0` |

因为 `MISC1 = 0x20` 连接 SRB1 到各通道反相输入，`ADS_raw_CHx` 近似表示 `INxP - SRB1`。所以上述差分在未饱和时等效为：

```text
CH1 = IN1P - IN2P
CH2 = IN3P - IN4P
CH3 = IN5P - IN6P
CH4 = IN7P - IN8P
```

差分结果在固件中被限制到 24-bit signed 范围：

```text
-8388608 .. 8388607
```

如果 host channel 接近 `0x7FFFFF` 或 `-0x800000`，可能是 ADS1299 原始通道饱和，也可能是固件差分后发生了 24-bit 限幅。

## 5. 当前数据帧格式

每个串口数据帧固定 `35 bytes`：

```text
0xAA
emg_channel_count[1]
CH1[3] CH2[3] CH3[3] CH4[3] CH5[3] CH6[3] CH7[3] CH8[3]
frame_counter[8]
0xBB
```

字段偏移如下：

| Byte offset | 长度 | 字段 | 说明 |
|---:|---:|---|---|
| `0` | `1` | `frame_header` | 固定为 `0xAA` |
| `1` | `1` | `emg_channel_count` | 有效 EMG 通道数，当前为 `4` |
| `2..4` | `3` | `CH1` | 24-bit signed, MSB first, `ADS_raw_CH1 - ADS_raw_CH2` |
| `5..7` | `3` | `CH2` | 24-bit signed, MSB first, `ADS_raw_CH3 - ADS_raw_CH4` |
| `8..10` | `3` | `CH3` | 24-bit signed, MSB first, `ADS_raw_CH5 - ADS_raw_CH6` |
| `11..13` | `3` | `CH4` | 24-bit signed, MSB first, `ADS_raw_CH7 - ADS_raw_CH8` |
| `14..16` | `3` | `CH5` | 24-bit signed, MSB first, 当前固定为 `0` |
| `17..19` | `3` | `CH6` | 24-bit signed, MSB first, 当前固定为 `0` |
| `20..22` | `3` | `CH7` | 24-bit signed, MSB first, 当前固定为 `0` |
| `23..25` | `3` | `CH8` | 24-bit signed, MSB first, 当前固定为 `0` |
| `26..33` | `8` | `frame_counter` | `uint64`, big-endian |
| `34` | `1` | `frame_tail` | 固定为 `0xBB` |

注意：

- 当前帧中没有 ADS1299 的 3 字节 status word。
- 当前帧中没有 CRC/checksum。
- `emg_channel_count` 表示前几个 host channel 是有效 EMG 数据，不表示后续通道槽位的字节数；当前帧仍固定包含 8 个 host channel 槽位。
- 当前帧头只有 1 字节，若数据中间丢字节，上位机需要靠帧长、帧尾和 `frame_counter` 重新同步。

## 6. 解析规则

### 6.1 帧同步

上位机应按以下规则处理字节流：

1. 在缓冲区中查找 `0xAA`。
2. 若后续不足 `35` 字节，继续等待。
3. 读取第 `1` 字节 `emg_channel_count`，检查它是否在 `1..8` 范围内。
4. 检查候选帧第 `34` 字节是否为 `0xBB`。
5. 若 `emg_channel_count` 非法或帧尾不是 `0xBB`，丢弃当前 `0xAA` 之前及该 `0xAA` 本身，继续搜索下一个 `0xAA`。
6. 若帧尾正确，解析 8 个 host channel、`emg_channel_count` 和 `frame_counter`。
7. 检查 `frame_counter` 是否连续递增。若不连续，记录丢帧、重复帧或错帧。

### 6.2 24-bit 通道数据转换

每个 host channel 是 24-bit 二补码，MSB first。

Python 示例：

```python
def int24_be_to_signed(b0: int, b1: int, b2: int) -> int:
    raw = (b0 << 16) | (b1 << 8) | b2
    if raw & 0x800000:
        raw -= 1 << 24
    return raw
```

### 6.3 帧计数器转换

`frame_counter` 为 8 字节大端序无符号整数：

```python
counter = int.from_bytes(frame[26:34], byteorder="big", signed=False)
```

该计数器在固件每处理一次 DRDY 样本时递增一次。上位机应使用它检查连续性。

## 7. 电压换算

建议上位机优先保存原始整数 code，再按需要换算为电压。

换算公式：

```text
voltage = code * (2 * VREF / gain) / 2^24
```

当前固件 ADS1299 PGA 增益为 `gain = 4`。如果硬件 `VREF = 4.5 V`，则：

```text
LSB = (2 * 4.5 / 4) / 2^24 = 1.3411045e-7 V/code
    = 0.134110 uV/code
```

Python 示例：

```python
def code_to_microvolts(code: int, vref: float = 4.5, gain: int = 4) -> float:
    return code * (2.0 * vref / gain) / (2 ** 24) * 1e6
```

如果实际硬件参考电压不是 `4.5 V`，必须把 `vref` 改成实测值或原理图确认值。

## 8. Python 解析示例

```python
FRAME_LEN = 35
HEADER = 0xAA
TAIL = 0xBB
CHANNEL_SLOT_COUNT = 8

def int24_be_to_signed(b0: int, b1: int, b2: int) -> int:
    raw = (b0 << 16) | (b1 << 8) | b2
    if raw & 0x800000:
        raw -= 1 << 24
    return raw

def parse_frame(frame: bytes) -> dict:
    if len(frame) != FRAME_LEN:
        raise ValueError("invalid frame length")
    if frame[0] != HEADER or frame[34] != TAIL:
        raise ValueError("invalid frame boundary")

    emg_channel_count = frame[1]
    if emg_channel_count < 1 or emg_channel_count > CHANNEL_SLOT_COUNT:
        raise ValueError("invalid emg channel count")

    channels = []
    offset = 2
    for _ in range(CHANNEL_SLOT_COUNT):
        channels.append(int24_be_to_signed(frame[offset], frame[offset + 1], frame[offset + 2]))
        offset += 3

    counter = int.from_bytes(frame[26:34], byteorder="big", signed=False)
    return {
        "counter": counter,
        "emg_channel_count": emg_channel_count,
        "channels_code": channels,
        "emg_channels_code": channels[:emg_channel_count],
        "reserved_zero_channels_code": channels[emg_channel_count:],
    }
```

## 9. 数据率估算

当前帧长为 `35 bytes`，采样率为 `1000 Hz`：

```text
payload = 35 bytes/frame * 1000 frame/s = 35,000 bytes/s
```

UART `921600 8N1` 理论传输能力约为：

```text
921600 bit/s / 10 = 92,160 bytes/s
```

因此当前二进制帧在理论带宽上足够。当前 ADS1299 数据环形缓冲区大小为 `8000 bytes`，约等于 `228` 帧或 `228 ms` 的 1 kSPS 数据；上位机仍应检查 `frame_counter` 连续性，确认实际没有串口阻塞或固件丢帧。

## 10. 当前限制和上位机注意事项

- 当前没有 CRC/checksum。帧尾和计数器只能发现一部分错位/丢帧问题，不能严格验证所有数据损坏。
- 当前没有 ADS1299 status 字节。上位机暂时无法根据 status word 判断 lead-off 或 ADS1299 状态。
- 当前 `emg_channel_count = 4`，只发送 4 个有效 EMG 差分通道；`CH5..CH8` 是固定零占位。
- 当前没有固件侧滤波。上位机应在保存 raw code 后再做带通、陷波、RMS/包络等处理。
- 当前 `SysMode_0` 和 `SysMode_1` 数据格式相同。上位机不要把 `SysMode_0` 或 `SysMode_1` 当作滤波模式。
- 若发现 `frame_counter` 跳变，必须标记数据不连续，不要把不连续数据直接用于特征提取或训练。
- 若通道值频繁接近 `0x7FFFFF` 或 `-0x800000`，说明可能发生原始输入饱和或差分限幅，应检查增益、电极连接和输入幅度。
