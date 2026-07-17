"""Off-Pi stand-ins so the MCP server / agent loop can be exercised without BLE.

Set IMMASTER_DRY_RUN=1 to make the MCP server use DryRobot instead of the real
BLE broadcaster. Useful for testing the LLM tool loop on a laptop.
"""

from __future__ import annotations
import time


class DryRobot:
    """Same surface as immaster.robot.Robot, but only prints."""

    def __init__(self):
        self.state = "stop"

    def command(self, name: str) -> None:
        self.state = name
        print(f"[dry] command {name}", flush=True)

    def for_duration(self, name: str, seconds: float) -> None:
        print(f"[dry] {name} for {seconds}s", flush=True)
        self.state = name
        time.sleep(min(seconds, 0.2))  # don't actually block long in tests
        self.state = "stop"

    def stop(self) -> None:
        self.state = "stop"
        print("[dry] stop", flush=True)

    def keepalive(self) -> None:
        pass

    def close(self) -> None:
        pass
