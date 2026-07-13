"""RunE W2 command and notification protocol helpers."""

from __future__ import annotations

import struct
from dataclasses import dataclass


W2_NOTIFY_HEADER = 0xA5
W2_NOTIFY_TAIL = 0x5A
W2_COMMAND_HEADER = 0xAA
W2_COMMAND_TAIL = 0xBB
W2_DATA_FRAME_TYPE = 0x11


@dataclass(frozen=True)
class W2RawPacket:
    """One W2 raw EMG/EEG packet decoded from a notification frame."""

    mode: int
    values: tuple[float, ...]

    @property
    def signal_kind(self) -> str:
        if self.mode == W2CommandBuilder.MODE_EEG_RAW:
            return "eeg_raw"
        return "emg_raw"


@dataclass(frozen=True)
class W2RmsPacket:
    """One W2 RMS packet decoded from a notification frame."""

    rms: int


W2Packet = W2RawPacket | W2RmsPacket


class W2CommandBuilder:
    """Build W2 host command frames."""

    ADDRESS_SOFTWARE = 0x02
    ADDRESS_DEVICE_NAME = 0x03
    ADDRESS_POWER = 0x0B
    ADDRESS_EMG_START = 0x11
    ADDRESS_SHUTDOWN = 0x14
    ADDRESS_HW_VERSION = 0x1D

    MODE_STOP = 0x00
    MODE_EMG_RMS = 0x01
    MODE_EMG_RAW = 0x03
    MODE_EEG_RAW = 0x04

    MODE_BY_NAME = {
        "stop": MODE_STOP,
        "emg_rms": MODE_EMG_RMS,
        "emg_raw": MODE_EMG_RAW,
        "eeg_raw": MODE_EEG_RAW,
    }

    @staticmethod
    def pack(address: int, is_write: bool, data: bytes | bytearray | list[int]) -> bytes:
        payload = bytes(data)
        command = bytearray(
            [
                W2_COMMAND_HEADER,
                len(payload) + 3,
                0x80 if is_write else 0x81,
                int(address) & 0xFF,
            ]
        )
        command.extend(payload)
        command.append(0x00)
        command.append(W2_COMMAND_TAIL)

        checksum = 0x00
        for value in command[1:]:
            checksum ^= value
        command[-2] = checksum
        return bytes(command)

    @classmethod
    def start_collect(cls, mode: int) -> bytes:
        return cls.pack(cls.ADDRESS_EMG_START, True, bytes([mode]) + bytes(8))

    @classmethod
    def stop_collect(cls) -> bytes:
        return cls.start_collect(cls.MODE_STOP)

    @classmethod
    def start_emg_rms(cls) -> bytes:
        return cls.start_collect(cls.MODE_EMG_RMS)

    @classmethod
    def start_emg_raw(cls) -> bytes:
        return cls.start_collect(cls.MODE_EMG_RAW)

    @classmethod
    def start_eeg_raw(cls) -> bytes:
        return cls.start_collect(cls.MODE_EEG_RAW)

    @classmethod
    def start_for_mode(cls, mode: str) -> bytes:
        try:
            mode_code = cls.MODE_BY_NAME[mode]
        except KeyError as exc:
            raise ValueError(f"unsupported W2 mode: {mode}") from exc
        return cls.start_collect(mode_code)

    @classmethod
    def read(cls, address: int) -> bytes:
        return cls.pack(address, False, b"")


class W2StreamParser:
    """Buffered parser for W2 BLE notification frames."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.skipped_bytes = 0
        self.bad_checksum_count = 0
        self.bad_tail_count = 0
        self.bad_payload_count = 0
        self.unsupported_frame_count = 0

    def feed(self, data: bytes | bytearray) -> list[W2Packet]:
        if data:
            self.buffer.extend(data)

        packets: list[W2Packet] = []
        while True:
            header_index = self._find_header()
            if header_index < 0:
                self.skipped_bytes += len(self.buffer)
                self.buffer.clear()
                return packets

            if header_index > 0:
                self.skipped_bytes += header_index
                del self.buffer[:header_index]

            if len(self.buffer) < 4:
                return packets

            frame_len = self.buffer[1] + 3
            if frame_len < 6:
                self.bad_payload_count += 1
                self.skipped_bytes += 1
                del self.buffer[0]
                continue

            if len(self.buffer) < frame_len:
                return packets

            candidate = bytes(self.buffer[:frame_len])
            if candidate[-1] != W2_NOTIFY_TAIL:
                self.bad_tail_count += 1
                self.skipped_bytes += 1
                del self.buffer[0]
                continue

            if candidate[3] != (candidate[1] ^ candidate[2]):
                self.bad_checksum_count += 1
                self.skipped_bytes += 1
                del self.buffer[0]
                continue

            del self.buffer[:frame_len]
            packet = self._parse_frame(candidate)
            if packet is not None:
                packets.append(packet)

    def _find_header(self) -> int:
        try:
            return self.buffer.index(W2_NOTIFY_HEADER)
        except ValueError:
            return -1

    def _parse_frame(self, frame: bytes) -> W2Packet | None:
        if frame[2] != W2_DATA_FRAME_TYPE:
            self.unsupported_frame_count += 1
            return None

        mode = frame[4]
        if mode == W2CommandBuilder.MODE_EMG_RMS:
            if len(frame) <= 18:
                self.bad_payload_count += 1
                return None
            return W2RmsPacket(rms=frame[17] * 256 + frame[18])

        if mode in (W2CommandBuilder.MODE_EMG_RAW, W2CommandBuilder.MODE_EEG_RAW):
            data_count = (frame[1] - 9 - 4) // 2
            if data_count < 0 or len(frame) < 15 + data_count * 2 + 1:
                self.bad_payload_count += 1
                return None

            value = struct.unpack("<f", frame[11:15])[0]
            deltas = struct.unpack("<" + "h" * data_count, frame[15 : 15 + data_count * 2])
            factor = 12.5786 if mode == W2CommandBuilder.MODE_EEG_RAW else 3.1457

            values = [value]
            for delta in deltas:
                value += delta / factor
                values.append(value)
            return W2RawPacket(mode=mode, values=tuple(values))

        self.unsupported_frame_count += 1
        return None
