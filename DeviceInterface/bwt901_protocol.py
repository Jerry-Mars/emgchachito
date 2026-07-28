"""Pure BWT901BLE 0x55 0x61 realtime-frame decoder."""

from __future__ import annotations

import struct
from dataclasses import dataclass


BWT901_FRAME_HEADER = b"\x55\x61"
BWT901_FRAME_LENGTH = 20


@dataclass(frozen=True)
class BWT901Packet:
    """One decoded device frame without host transport timing."""

    sequence: int
    acc_x_g: float
    acc_y_g: float
    acc_z_g: float
    gyro_x_dps: float
    gyro_y_dps: float
    gyro_z_dps: float
    angle_x_deg: float
    angle_y_deg: float
    angle_z_deg: float
    raw: tuple[int, int, int, int, int, int, int, int, int]

    @property
    def scaled_values(self) -> tuple[float, ...]:
        return (
            self.acc_x_g,
            self.acc_y_g,
            self.acc_z_g,
            self.gyro_x_dps,
            self.gyro_y_dps,
            self.gyro_z_dps,
            self.angle_x_deg,
            self.angle_y_deg,
            self.angle_z_deg,
        )


class BWT901StreamDecoder:
    """Decode split, merged, and misaligned BWT901BLE realtime frames."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.sequence = 0
        self.skipped_bytes = 0

    def reset(self) -> None:
        self.buffer.clear()
        self.sequence = 0
        self.skipped_bytes = 0

    def feed(self, data: bytes | bytearray | memoryview) -> list[BWT901Packet]:
        if data:
            self.buffer.extend(data)

        packets: list[BWT901Packet] = []
        while True:
            header_index = self.buffer.find(BWT901_FRAME_HEADER)
            if header_index < 0:
                keep_tail = bool(self.buffer and self.buffer[-1] == BWT901_FRAME_HEADER[0])
                skipped = len(self.buffer) - int(keep_tail)
                self.skipped_bytes += skipped
                self.buffer[:] = self.buffer[-1:] if keep_tail else b""
                return packets

            if header_index:
                self.skipped_bytes += header_index
                del self.buffer[:header_index]

            if len(self.buffer) < BWT901_FRAME_LENGTH:
                return packets

            frame = bytes(self.buffer[:BWT901_FRAME_LENGTH])
            del self.buffer[:BWT901_FRAME_LENGTH]
            packets.append(self._decode_frame(frame))

    def _decode_frame(self, frame: bytes) -> BWT901Packet:
        if len(frame) != BWT901_FRAME_LENGTH or frame[:2] != BWT901_FRAME_HEADER:
            raise ValueError("Invalid BWT901BLE realtime frame.")

        raw = struct.unpack("<9h", frame[2:])
        self.sequence += 1
        acc = tuple(round(value / 32768.0 * 16.0, 3) for value in raw[0:3])
        gyro = tuple(round(value / 32768.0 * 2000.0, 3) for value in raw[3:6])
        angle = tuple(round(value / 32768.0 * 180.0, 3) for value in raw[6:9])
        return BWT901Packet(
            sequence=self.sequence,
            acc_x_g=acc[0],
            acc_y_g=acc[1],
            acc_z_g=acc[2],
            gyro_x_dps=gyro[0],
            gyro_y_dps=gyro[1],
            gyro_z_dps=gyro[2],
            angle_x_deg=angle[0],
            angle_y_deg=angle[1],
            angle_z_deg=angle[2],
            raw=raw,
        )


__all__ = [
    "BWT901_FRAME_HEADER",
    "BWT901_FRAME_LENGTH",
    "BWT901Packet",
    "BWT901StreamDecoder",
]
