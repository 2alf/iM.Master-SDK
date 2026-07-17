"""
Vision — the robot's eyes via a phone running an IP-camera app (e.g. IP Webcam).

Stage 1: reliably pull a JPEG snapshot from the phone and report basic info
(dimensions, brightness, a dark/blind safety signal). Full scene understanding
(objects, obstacles) arrives in Stage 2 via a Vision-Language Model. See
docs/PERCEPTION_PLAN.md.

Pure-ish: uses stdlib for the HTTP fetch; Pillow (optional) for resize/brightness
— degrades gracefully if Pillow isn't installed. No BLE, so it's testable off the
robot as long as the phone camera is reachable.

CLI test (on the Pi, phone streaming):
    python3 -m immaster.vision http://<phone-ip>:8080/shot.jpg
"""

from __future__ import annotations
import base64
import io
import os
import ssl
import urllib.request
from urllib.parse import urlparse, urlunparse

# Snapshot endpoint of the phone IP-camera app. IP Webcam exposes /shot.jpg.
# Prefer http:// on a LAN — the app's https uses a self-signed cert.
IPCAM_URL = os.environ.get("IPCAM_URL", "http://<phone-ip>:8080/shot.jpg")
IPCAM_TIMEOUT = float(os.environ.get("IPCAM_TIMEOUT", "4"))
IPCAM_MAX_DIM = int(os.environ.get("IPCAM_MAX_DIM", "480"))
DARK_THRESHOLD = float(os.environ.get("IPCAM_DARK", "35"))  # avg luminance 0-255
# IP Webcam login (basic auth). May also be given inline: http://user:pass@host/..
IPCAM_USER = os.environ.get("IPCAM_USER")
IPCAM_PASS = os.environ.get("IPCAM_PASS")
# Accept the app's self-signed https cert (on by default; set 0 to enforce verify).
IPCAM_INSECURE = os.environ.get("IPCAM_INSECURE", "1") != "0"


def _split_credentials(url: str) -> tuple[str, str | None, str | None]:
    """Pull user:pass out of the URL if present; otherwise fall back to env.
    Returns (clean_url, user, password)."""
    user, pw = IPCAM_USER, IPCAM_PASS
    p = urlparse(url)
    if p.username or p.password:
        user, pw = p.username, p.password
        host = p.hostname or ""
        if p.port:
            host += f":{p.port}"
        url = urlunparse(p._replace(netloc=host))
    return url, user, pw


def capture_frame(url: str | None = None, timeout: float | None = None) -> bytes | None:
    """Fetch one JPEG snapshot. Handles basic-auth and self-signed https.
    Returns raw bytes, or None if unreachable."""
    url = url or IPCAM_URL
    url, user, pw = _split_credentials(url)
    req = urllib.request.Request(url)
    if user is not None:
        token = base64.b64encode(f"{user}:{pw or ''}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    ctx = ssl._create_unverified_context() if (url.startswith("https") and IPCAM_INSECURE) else None
    try:
        with urllib.request.urlopen(req, timeout=timeout or IPCAM_TIMEOUT, context=ctx) as resp:
            data = resp.read()
        return data or None
    except Exception:
        return None


def _load(jpeg: bytes):
    """Open JPEG as a PIL RGB image, or None if Pillow is missing / decode fails."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        return Image.open(io.BytesIO(jpeg)).convert("RGB")
    except Exception:
        return None


def downscale(jpeg: bytes, max_dim: int | None = None, quality: int = 80) -> bytes:
    """Shrink the longest edge to max_dim to speed the VLM / cut bandwidth.
    Returns the original bytes unchanged if Pillow isn't available."""
    max_dim = max_dim or IPCAM_MAX_DIM
    img = _load(jpeg)
    if img is None:
        return jpeg
    w, h = img.size
    scale = min(1.0, max_dim / max(w, h))
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality)
    return out.getvalue()


def brightness(jpeg: bytes) -> float | None:
    """Average luminance 0-255, or None without Pillow. Used for a 'too dark to
    drive' safety signal."""
    img = _load(jpeg)
    if img is None:
        return None
    hist = img.convert("L").histogram()
    total = sum(hist)
    if not total:
        return None
    return sum(i * c for i, c in enumerate(hist)) / total


def frame_info(jpeg: bytes) -> dict:
    info: dict = {"bytes": len(jpeg)}
    img = _load(jpeg)
    if img is not None:
        info["width"], info["height"] = img.size
        b = brightness(jpeg)
        if b is not None:
            info["brightness"] = round(b, 1)
            info["too_dark"] = b < DARK_THRESHOLD
    return info


def describe(url: str | None = None) -> dict:
    """Capture one frame and report status. This is what the `look()` tool calls.

    Stage 1 returns connectivity + basic info (proves the eyes work and gives a
    dark/blind safety signal). Scene understanding ('wall ahead', 'person left')
    is filled in by the VLM in Stage 2.
    """
    jpeg = capture_frame(url)
    if jpeg is None:
        return {
            "ok": False,
            "camera": "unreachable",
            "url": url or IPCAM_URL,
            "advice": "cannot see — do not drive blind; stop.",
        }
    info = frame_info(jpeg)
    result = {"ok": True, "camera": "connected", **info, "scene": "not_analyzed_yet",
              "note": "VLM scene understanding lands in Stage 2."}
    if info.get("too_dark"):
        result["advice"] = "scene is very dark — unsafe to drive; consider stopping."
    return result


def _gray_small(jpeg: bytes, size=(64, 48)):
    """Downsized grayscale pixel list for cheap frame comparison. None w/o Pillow."""
    img = _load(jpeg)
    if img is None:
        return None
    return list(img.convert("L").resize(size).getdata())


def frame_change(a: bytes, b: bytes, size=(64, 48)) -> float | None:
    """Fraction (0..1) by which frame b differs from a. The core 'am I stuck?'
    signal: after a forward move, a near-zero change means you're blocked."""
    ga, gb = _gray_small(a, size), _gray_small(b, size)
    if ga is None or gb is None or len(ga) != len(gb):
        return None
    return sum(abs(x - y) for x, y in zip(ga, gb)) / len(ga) / 255.0


def region_brightness(jpeg: bytes) -> dict | None:
    """Average luminance of the left / center / right thirds. A weak but free
    'where is it more open' hint (brighter/further often = more open space)."""
    img = _load(jpeg)
    if img is None:
        return None
    g = img.convert("L")
    w, h = g.size

    def avg(box):
        hist = g.crop(box).histogram()
        tot = sum(hist)
        return round(sum(i * c for i, c in enumerate(hist)) / tot, 1) if tot else 0.0

    t = w // 3
    return {"left": avg((0, 0, t, h)), "center": avg((t, 0, 2 * t, h)),
            "right": avg((2 * t, 0, w, h))}


def observe(cur_jpeg: bytes, prev_jpeg: bytes | None = None,
            last_action: str | None = None) -> dict:
    """Build a text observation from CPU-only cues, for feeding a text LLM.
    Combines brightness, frame-change (stuck detection) and open-side hint."""
    info = frame_info(cur_jpeg)
    parts: list[str] = []
    b = info.get("brightness")
    if b is not None:
        parts.append("very dark" if info.get("too_dark") else f"brightness {b:.0f}/255")

    change = frame_change(prev_jpeg, cur_jpeg) if prev_jpeg is not None else None
    if change is not None:
        pct = round(change * 100)
        if last_action and last_action not in ("stop", None) and pct < 4:
            parts.append(f"after '{last_action}' the view barely changed ({pct}%) — "
                         "you are probably blocked; turn to a new heading")
        else:
            parts.append(f"view changed {pct}% since last move")

    regions = region_brightness(cur_jpeg)
    if regions:
        openest = max(regions, key=regions.get)
        parts.append(f"L/C/R brightness {regions['left']}/{regions['center']}/"
                     f"{regions['right']} (most open looks: {openest})")

    return {
        "text": "; ".join(parts) if parts else "no visual detail",
        "brightness": b,
        "too_dark": info.get("too_dark"),
        "change": change,
        "regions": regions,
    }


def save_frame(path: str = "vision_test.jpg", url: str | None = None,
               downscaled: bool = True) -> bool:
    """Grab a frame and write it to disk (for eyeballing what the robot sees)."""
    jpeg = capture_frame(url)
    if jpeg is None:
        return False
    if downscaled:
        jpeg = downscale(jpeg)
    with open(path, "wb") as f:
        f.write(jpeg)
    return True


def _cli():
    import json
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else None
    d = describe(url)
    print(json.dumps(d, indent=2))
    if d.get("ok"):
        print("saved vision_test.jpg" if save_frame(url=url) else "save failed")
    else:
        print(f"Set IPCAM_URL or pass the URL. Tried: {d.get('url')}")


if __name__ == "__main__":
    _cli()
