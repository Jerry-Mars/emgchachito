"""ADS1299 binary host-frame parsing.

This module mirrors DeviceInterface/EMG_HOST_FRAME_PROTOCOL.md and intentionally
has no GUI or serial-port dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass


FRAME_HEADER = 0xAA
FRAME_TAIL = 0xBB
FRAME_LEN = 35
CHANNEL_COUNT = 8
CHANNEL_BYTES = 3
CHANNEL_COUNT_OFFSET = 1
CHANNELS_OFFSET = 2
COUNTER_OFFSET = 26
COUNTER_LEN = 8
SAMPLE_RATE_HZ = 1000


@dataclass(frozen=True)
class ADS1299Frame:
    """One validated ADS1299 host frame."""

    counter: int
    emg_channel_count: int
    channels_code: tuple[int, ...]
    dropped_frames_before: int = 0

    @property
    def emg_channels_code(self) -> tuple[int, ...]:
        return self.channels_code[: self.emg_channel_count]


def int24_be_to_signed(b0: int, b1: int, b2: int) -> int:
    raw = (b0 << 16) | (b1 << 8) | b2
    if raw & 0x800000:
        raw -= 1 << 24
    return raw


def parse_frame(frame: bytes) -> ADS1299Frame:
    if len(frame) != FRAME_LEN:
        raise ValueError("invalid frame length")
    if frame[0] != FRAME_HEADER or frame[-1] != FRAME_TAIL:
        raise ValueError("invalid frame boundary")

    emg_channel_count = frame[CHANNEL_COUNT_OFFSET]
    if emg_channel_count < 1 or emg_channel_count > CHANNEL_COUNT:
        raise ValueError("invalid emg channel count")

    channels: list[int] = []
    offset = CHANNELS_OFFSET
    for _ in range(CHANNEL_COUNT):
        channels.append(int24_be_to_signed(frame[offset], frame[offset + 1], frame[offset + 2]))
        offset += CHANNEL_BYTES

    counter = int.from_bytes(
        frame[COUNTER_OFFSET : COUNTER_OFFSET + COUNTER_LEN],
        byteorder="big",
        signed=False,
    )
    return ADS1299Frame(
        counter=counter,
        emg_channel_count=emg_channel_count,
        channels_code=tuple(channels),
    )


class ADS1299StreamParser:
    """Incrementally parse fixed-length binary frames from a byte stream."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.expected_counter: int | None = None
        self.skipped_bytes = 0
        self.bad_tail_count = 0
        self.bad_channel_count = 0

    def feed(self, data: bytes) -> list[ADS1299Frame]:
        if data:
            self.buffer.extend(data)

        frames: list[ADS1299Frame] = []
        while True:
            header_index = self._find_header()
            if header_index < 0:
                self.skipped_bytes += len(self.buffer)
                self.buffer.clear()
                return frames

            if header_index > 0:
                self.skipped_bytes += header_index
                del self.buffer[:header_index]

            if len(self.buffer) < FRAME_LEN:
                return frames

            candidate = bytes(self.buffer[:FRAME_LEN])
            if candidate[-1] != FRAME_TAIL:
                self.bad_tail_count += 1
                self.skipped_bytes += 1
                del self.buffer[0]
                continue
            if not 1 <= candidate[CHANNEL_COUNT_OFFSET] <= CHANNEL_COUNT:
                self.bad_channel_count += 1
                self.skipped_bytes += 1
                del self.buffer[0]
                continue

            del self.buffer[:FRAME_LEN]
            parsed = parse_frame(candidate)
            frames.append(self._mark_continuity(parsed))

    def _find_header(self) -> int:
        try:
            return self.buffer.index(FRAME_HEADER)
        except ValueError:
            return -1

    def _mark_continuity(self, frame: ADS1299Frame) -> ADS1299Frame:
        if self.expected_counter is None:
            dropped = 0
        elif frame.counter == self.expected_counter:
            dropped = 0
        elif frame.counter > self.expected_counter:
            dropped = frame.counter - self.expected_counter
        else:
            dropped = -1

        self.expected_counter = frame.counter + 1
        return ADS1299Frame(
            counter=frame.counter,
            emg_channel_count=frame.emg_channel_count,
            channels_code=frame.channels_code,
            dropped_frames_before=dropped,
        )
