# ADS1299 EMG 上位机串口解析指南

本文档描述当前固件实际发送给上位机的 EMG 原始数据帧格式。它只对应当前代码版本，不包含后续计划中的 ADS1299 status 字节、CRC 或滤波数据格式。

## 1. 串口连接参数

当前数据从 `USART1` 输出，上位机串口参数应配置为：

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
- 每次从接收缓冲区中搜索帧头 `0xAA`，再检查固定长度和帧尾 `0xBB`。

## 2. 采集配置

当前固件 ADS1299 配置如下：

| 项目 | 当前值 |
|---|---|
| 采样率 | `1000 Hz` |
| 通道数 | `8` |
| 每通道字节数 | `3` |
| 每通道含义 | `CHx = INxP - INxN` |
| ADS1299 增益 | `6` |
| SRB1 | 断开 |
| SRB2 | 断开 |
| BIAS sense | P/N 全通道启用 |
| 输出数据 | 原始 24-bit 二补码差分采样值 |

当前 `SysMode_0` 和 `SysMode_1` 都发送同一种原始 EMG 数据。当前版本没有滤波数据输出。

## 3. 当前数据帧格式

每个串口数据帧固定 `34 bytes`：

```text
0xAA
CH1[3] CH2[3] CH3[3] CH4[3] CH5[3] CH6[3] CH7[3] CH8[3]
frame_counter[8]
0xBB
```

字段偏移如下：

| Byte offset | 长度 | 字段 | 说明 |
|---:|---:|---|---|
| `0` | `1` | `frame_header` | 固定为 `0xAA` |
| `1..3` | `3` | `CH1` | 24-bit signed, MSB first |
| `4..6` | `3` | `CH2` | 24-bit signed, MSB first |
| `7..9` | `3` | `CH3` | 24-bit signed, MSB first |
| `10..12` | `3` | `CH4` | 24-bit signed, MSB first |
| `13..15` | `3` | `CH5` | 24-bit signed, MSB first |
| `16..18` | `3` | `CH6` | 24-bit signed, MSB first |
| `19..21` | `3` | `CH7` | 24-bit signed, MSB first |
| `22..24` | `3` | `CH8` | 24-bit signed, MSB first |
| `25..32` | `8` | `frame_counter` | `uint64`, big-endian |
| `33` | `1` | `frame_tail` | 固定为 `0xBB` |

注意：

- 当前帧中没有 ADS1299 的 3 字节 status word。
- 当前帧中没有 CRC/checksum。
- 当前帧头只有 1 字节，若数据中间丢字节，上位机需要靠帧长和帧尾重新同步。

## 4. 解析规则

### 4.1 帧同步

上位机应按以下规则处理字节流：

1. 在缓冲区中查找 `0xAA`。
2. 若后续不足 `34` 字节，继续等待。
3. 检查第 `33` 字节是否为 `0xBB`。
4. 若不是 `0xBB`，丢弃当前 `0xAA` 之前及该 `0xAA` 本身，继续搜索下一个 `0xAA`。
5. 若帧尾正确，解析 8 通道数据和 `frame_counter`。
6. 检查 `frame_counter` 是否连续递增。若不连续，记录丢帧或错帧。

### 4.2 24-bit 通道数据转换

每个通道是 ADS1299 原始 24-bit 二补码，MSB first。

Python 示例：

```python
def int24_be_to_signed(b0: int, b1: int, b2: int) -> int:
    raw = (b0 << 16) | (b1 << 8) | b2
    if raw & 0x800000:
        raw -= 1 << 24
    return raw
```

### 4.3 帧计数器转换

`frame_counter` 为 8 字节大端序无符号整数：

```python
counter = int.from_bytes(frame[25:33], byteorder="big", signed=False)
```

该计数器每成功读取一个 DRDY 样本后递增一次。上位机应使用它检查连续性。

## 5. 电压换算

建议上位机优先保存原始整数 code，再按需要换算为电压。

换算公式：

```text
voltage = code * (2 * VREF / gain) / 2^24
```

当前固件增益为 `gain = 6`。如果硬件 `VREF = 4.5 V`，则：

```text
LSB = (2 * 4.5 / 6) / 2^24 = 8.9407e-8 V/code
    = 0.089407 uV/code
```

Python 示例：

```python
def code_to_microvolts(code: int, vref: float = 4.5, gain: int = 6) -> float:
    return code * (2.0 * vref / gain) / (2 ** 24) * 1e6
```

如果实际硬件参考电压不是 `4.5 V`，必须把 `vref` 改成实测或原理图确认的值。

## 6. Python 解析示例

```python
FRAME_LEN = 34
HEADER = 0xAA
TAIL = 0xBB
CHANNEL_COUNT = 8

def int24_be_to_signed(b0: int, b1: int, b2: int) -> int:
    raw = (b0 << 16) | (b1 << 8) | b2
    if raw & 0x800000:
        raw -= 1 << 24
    return raw

def parse_frame(frame: bytes) -> dict:
    if len(frame) != FRAME_LEN:
        raise ValueError("invalid frame length")
    if frame[0] != HEADER or frame[33] != TAIL:
        raise ValueError("invalid frame boundary")

    channels = []
    offset = 1
    for _ in range(CHANNEL_COUNT):
        channels.append(int24_be_to_signed(frame[offset], frame[offset + 1], frame[offset + 2]))
        offset += 3

    counter = int.from_bytes(frame[25:33], byteorder="big", signed=False)
    return {
        "counter": counter,
        "channels_code": channels,
    }
```

## 7. 数据率估算

当前帧长为 `34 bytes`，采样率为 `1000 Hz`：

```text
payload = 34 bytes/frame * 1000 frame/s = 34,000 bytes/s
```

UART `921600 8N1` 理论传输能力约为：

```text
921600 bit/s / 10 = 92,160 bytes/s
```

因此当前二进制帧在理论带宽上足够，但上位机仍应检查 `frame_counter` 连续性，确认实际没有串口阻塞或固件丢帧。

## 8. 当前限制和上位机注意事项

- 当前没有 CRC/checksum。帧尾和计数器只能发现一部分错位/丢帧问题，不能严格验证所有数据损坏。
- 当前没有 ADS1299 status 字节。上位机暂时无法根据 status word 判断 lead-off 或 ADS1299 状态。
- 当前没有固件侧滤波。上位机应在保存 raw code 后再做带通、陷波、RMS/包络等处理。
- 当前 `SysMode_0` 和 `SysMode_1` 数据格式相同。上位机不要把 `SysMode_0` 当作滤波模式。
- 若发现 `frame_counter` 跳变，必须标记数据不连续，不要把不连续数据直接用于特征提取或训练。
- 若通道值频繁接近 `0x7FFFFF` 或 `-0x800000`，说明可能饱和，应检查增益、电极连接和输入幅度。

