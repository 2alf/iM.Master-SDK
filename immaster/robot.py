"""
High-level Robot API. This is the surface an LLM or application talks to.

Design notes:
 - Movement is *stateful and continuous*: calling `forward()` sets the current
   command and returns immediately; the background broadcaster keeps it going
   until you change or stop it. This matches how an autonomous agent thinks
   ("start moving forward", "now turn") rather than blocking calls.

 - A watchdog (deadman switch) auto-stops the robot if no command is refreshed
   within `watchdog_s`. Critical for autonomous control: if the agent loop
   stalls or crashes, the robot halts instead of driving into a wall forever.
   
 - `for_duration()` is provided for simple scripted "do X for N seconds" moves.
"""

from __future__ import annotations
import threading
import time

from . import protocol
from .protocol import Wheel


class Robot:
    def __init__(self, hci_index: int = 0, rate_hz: float = 25.0, watchdog_s: float = 1.5):
        # Imported lazily so protocol/agent code can be used off-Pi without BLE.
        from .driver import HciBroadcaster

        self._bc = HciBroadcaster(hci_index=hci_index, rate_hz=rate_hz)
        self._bc.start()

        self._watchdog_s = watchdog_s
        self._last_cmd = time.monotonic()
        self._current = "stop"
        self._wd_running = True
        self._wd = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._wd.start()

    # --- core ---
    def _apply(self, left: Wheel | int, right: Wheel | int, name: str = "custom") -> None:
        self._bc.set_frame(protocol.build_frame(left, right))
        self._current = name
        self._last_cmd = time.monotonic()

    def command(self, name: str) -> None:
        """Run a named command from protocol.COMMANDS (forward, spin_cw, ...)."""
        self._apply(*protocol.COMMANDS[name], name=name)

    @property
    def state(self) -> str:
        return self._current

    # --- named movements ---
    def forward(self):   self.command("forward")
    def reverse(self):   self.command("reverse")
    def spin_cw(self):   self.command("spin_cw")
    def spin_ccw(self):  self.command("spin_ccw")
    def veer_left(self): self.command("veer_left")
    def veer_right(self):self.command("veer_right")

    def stop(self):
        self._apply(Wheel.STOP, Wheel.STOP, name="stop")

    def set_wheels(self, left: int, right: int):
        """Directly set each wheel to 0=stop / 1=fwd / 2=rev."""
        self._apply(left, right, name=f"wheels({left},{right})")

    # --- scripted helper ---
    def for_duration(self, command: str, seconds: float):
        """Blocking convenience: run `command` for N seconds, then stop.

        Refreshes the deadman timer while running so the watchdog doesn't cut a
        move short when `seconds` exceeds `watchdog_s`.
        """
        self.command(command)
        end = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < end:
            self.keepalive()
            time.sleep(0.2)
        self.stop()

    # --- watchdog ---
    def keepalive(self):
        """Refresh the deadman timer without changing the command (for agents
        that want to hold a movement across think-loops)."""
        self._last_cmd = time.monotonic()

    def _watchdog_loop(self):
        while self._wd_running:
            if self._current != "stop" and (time.monotonic() - self._last_cmd) > self._watchdog_s:
                self.stop()
            time.sleep(0.1)

    def close(self):
        self._wd_running = False
        self._bc.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
