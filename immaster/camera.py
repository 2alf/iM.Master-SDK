from __future__ import annotations
import os
from abc import ABC, abstractmethod


class Camera(ABC):

    @abstractmethod
    def capture(self) -> bytes | None:
        # Return one JPEG frame as bytes, or None if unavailable.

    def __call__(self) -> bytes | None:
        return self.capture()


class IPCamera(Camera):

    def __init__(self, url: str | None = None, timeout: float | None = None):
        self.url = url or os.environ.get("IPCAM_URL")
        self.timeout = timeout

    def capture(self) -> bytes | None:
        from . import vision
        return vision.capture_frame(self.url, self.timeout)


class StaticImage(Camera):

    def __init__(self, path: str):
        self.path = path

    def capture(self) -> bytes | None:
        try:
            with open(self.path, "rb") as f:
                return f.read()
        except OSError:
            return None
