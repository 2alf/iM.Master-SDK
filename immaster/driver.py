"""
Raw HCI transport + continuous advertising broadcaster. 
Raspberry Pi / Linux only.

The robot only moves while it is receiving a steady stream of advertising
packets, so the driver runs a background thread that re-broadcasts whatever the
current frame is at a fixed rate. Callers just swap the current frame; the
thread keeps the radio busy.
"""

from __future__ import annotations
import os
import socket
import struct
import sys
import threading
import time

from . import protocol

# HCI opcodes (OGF 0x08 = LE Controller)
OGF_LE = 0x08
OCF_SET_ADV_DATA = 0x0008
OCF_SET_ADV_ENABLE = 0x000A


class HciBroadcaster:
    def __init__(self, hci_index: int = 0, rate_hz: float = 25.0):
        if not sys.platform.startswith("linux"):
            raise RuntimeError("HciBroadcaster only runs on Linux (Raspberry Pi)")
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            raise PermissionError("raw HCI requires root — run with sudo")

        self._sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
        self._sock.bind((hci_index,))
        self._interval = 1.0 / rate_hz

        self._frame = protocol.IDLE_FRAME
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

        self._cmd(OCF_SET_ADV_ENABLE, b"\x00")  # clear any lingering adv

    # --- low level ---
    def _cmd(self, ocf: int, params: bytes = b"") -> None:
        opcode = (OGF_LE << 10) | ocf
        self._sock.send(b"\x01" + struct.pack("<HB", opcode, len(params)) + params)

    def _push(self, frame: bytes) -> None:
        adv = protocol.advertising_data(frame)
        self._cmd(OCF_SET_ADV_ENABLE, b"\x00")
        self._cmd(OCF_SET_ADV_DATA, bytes([31]) + adv)
        self._cmd(OCF_SET_ADV_ENABLE, b"\x01")

    # --- public ---
    def set_frame(self, frame: bytes) -> None:
        with self._lock:
            self._frame = frame

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while self._running:
            with self._lock:
                frame = self._frame
            self._push(frame)
            time.sleep(self._interval)

    def stop(self) -> None:
        self.set_frame(protocol.build_frame(0, 0))
        # let a few stop frames go out before killing the radio
        time.sleep(self._interval * 4)
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        self._cmd(OCF_SET_ADV_ENABLE, b"\x00")

    def close(self) -> None:
        try:
            self.stop()
        finally:
            self._sock.close()
