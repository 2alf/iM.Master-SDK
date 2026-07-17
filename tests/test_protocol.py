"""Unit tests for the pure protocol layer — no BLE, runs anywhere.

These double as an executable spec of the reverse-engineered frame format
documented in docs/BLUETOOTH.md.
"""

import pytest

from immaster import protocol
from immaster.protocol import Wheel, build_frame, decode_frame, motor_byte, frame_for


def test_forward_frame_matches_capture():
    # The exact forward frame observed in the real app's packet capture.
    assert build_frame(Wheel.FWD, Wheel.FWD).hex() == "ae4a0700000000c50000c504c399"


def test_stop_and_reverse_frames():
    assert build_frame(Wheel.STOP, Wheel.STOP).hex() == "ae4a0700000000c00000c002c399"
    assert build_frame(Wheel.REV, Wheel.REV).hex() == "ae4a0700000000ca0000ca04c399"


@pytest.mark.parametrize("left,right", [
    (l, r) for l in (0, 1, 2) for r in (0, 1, 2)
])
def test_roundtrip_all_nine_commands(left, right):
    frame = build_frame(left, right)
    assert decode_frame(frame) == (Wheel(left), Wheel(right))


def test_motor_byte_encoding():
    # B7 = 0xC0 | (left << 2) | right
    assert motor_byte(Wheel.FWD, Wheel.FWD) == 0xC5
    assert motor_byte(Wheel.REV, Wheel.FWD) == 0xC9  # spin
    assert motor_byte(Wheel.STOP, Wheel.STOP) == 0xC0


def test_checksum_is_popcount_of_motor_byte():
    # Byte 11 is popcount(B7), NOT a sequence counter — verified on every
    # frame in the capture.
    for left in (0, 1, 2):
        for right in (0, 1, 2):
            frame = build_frame(left, right)
            b7 = frame[7]
            assert frame[11] == bin(b7).count("1")


def test_byte10_mirrors_byte7():
    frame = build_frame(Wheel.FWD, Wheel.REV)
    assert frame[10] == frame[7]


def test_frame_length_is_14():
    assert len(build_frame(Wheel.FWD, Wheel.FWD)) == 14


def test_wheel_state_3_is_rejected():
    # The value 3 (0b11) never appears in the protocol.
    with pytest.raises(ValueError):
        motor_byte(3, 0)


def test_decode_rejects_bad_checksum():
    frame = bytearray(build_frame(Wheel.FWD, Wheel.FWD))
    frame[11] ^= 0xFF  # corrupt the checksum
    with pytest.raises(ValueError):
        decode_frame(bytes(frame))


def test_decode_rejects_wrong_length():
    with pytest.raises(ValueError):
        decode_frame(b"\x00\x01\x02")


def test_command_table_covers_named_moves():
    for name in ("forward", "reverse", "spin_cw", "spin_ccw",
                 "veer_left", "veer_right", "stop"):
        assert name in protocol.COMMANDS
        assert len(frame_for(name)) == 14


def test_frame_for_unknown_command_raises():
    with pytest.raises(KeyError):
        frame_for("teleport")


def test_advertising_data_is_31_bytes_with_company_id():
    frame = build_frame(Wheel.FWD, Wheel.FWD)
    adv = protocol.advertising_data(frame)
    assert len(adv) == 31
    # company id 0x53A6 appears little-endian as a6 53
    assert b"\xa6\x53" in adv
