"""
Pure protocol layer for the im.master BLE robot.

Frame (14 manufacturer-data bytes, under company id 0x53A6 / `a6 53` LE):

    ae 4a 07 00 00 00 00  B7  00 00  B7  CK  c3 99

    B7  = 0xC0 | (left << 2) | right        motor command byte
          left, right in {0=stop, 1=fwd, 2=rev}  (2 bits each, never 3)
          0xC0 high bits = motors-enabled flag (constant in every observed frame)
    byte10 = copy of B7
    CK  = popcount(B7)  -> integrity byte (NOT a sequence counter)
    c3 99 = fixed "active drive profile" trailer

Idle beacon (no active drive): ae 00 00 00 00 00 00 00 00 00 00 00 e1 99
"""

from __future__ import annotations
from enum import IntEnum

COMPANY_ID = 0x53A6
COMPANY_LE = bytes([COMPANY_ID & 0xFF, COMPANY_ID >> 8])  # a6 53

MOTOR_ENABLE = 0xC0
TRAILER = bytes([0xC3, 0x99])
HEADER = bytes([0xAE, 0x4A, 0x07])

IDLE_FRAME = bytes([0xAE, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xE1, 0x99])


class Wheel(IntEnum):
    STOP = 0
    FWD = 1
    REV = 2


def _popcount(x: int) -> int:
    return bin(x).count("1")


def motor_byte(left: Wheel | int, right: Wheel | int) -> int:
    left, right = int(left), int(right)
    if left not in (0, 1, 2) or right not in (0, 1, 2):
        raise ValueError(f"wheel states must be 0/1/2, got left={left} right={right}")
    return MOTOR_ENABLE | (left << 2) | right


def build_frame(left: Wheel | int, right: Wheel | int) -> bytes:
    b7 = motor_byte(left, right)
    ck = _popcount(b7)
    return bytes([0xAE, 0x4A, 0x07, 0, 0, 0, 0, b7, 0, 0, b7, ck, 0xC3, 0x99])


def decode_frame(frame: bytes) -> tuple[Wheel, Wheel]:
    if len(frame) != 14 or frame[:3] != HEADER or frame[12:] != TRAILER:
        raise ValueError(f"not a drive frame: {frame.hex()}")
    b7 = frame[7]
    if frame[10] != b7:
        raise ValueError("byte10 mirror mismatch")
    if frame[11] != _popcount(b7):
        raise ValueError("checksum mismatch")
    return Wheel((b7 >> 2) & 0x3), Wheel(b7 & 0x3)


def advertising_data(frame: bytes) -> bytes:
    manuf = COMPANY_LE + frame
    adv = b"\x02\x01\x05" + bytes([len(manuf) + 1]) + b"\xff" + manuf
    return adv.ljust(31, b"\x00")


COMMANDS: dict[str, tuple[Wheel, Wheel]] = {
    "stop":     (Wheel.STOP, Wheel.STOP),
    "forward":  (Wheel.FWD,  Wheel.FWD),
    "reverse":  (Wheel.REV,  Wheel.REV),
    "spin_cw":  (Wheel.FWD,  Wheel.REV),
    "spin_ccw": (Wheel.REV,  Wheel.FWD),
    "veer_right": (Wheel.FWD, Wheel.STOP),
    "veer_left":  (Wheel.STOP, Wheel.FWD),
    "back_right": (Wheel.REV, Wheel.STOP),
    "back_left":  (Wheel.STOP, Wheel.REV),
}


def frame_for(command: str) -> bytes:
    if command not in COMMANDS:
        raise KeyError(f"unknown command {command!r}; valid: {sorted(COMMANDS)}")
    return build_frame(*COMMANDS[command])
