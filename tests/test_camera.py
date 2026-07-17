"""Unit tests for the pluggable camera layer — no live camera."""

from immaster.camera import Camera, IPCamera, StaticImage


def test_static_image_reads_bytes(tmp_path):
    p = tmp_path / "frame.jpg"
    p.write_bytes(b"\xff\xd8jpegdata\xff\xd9")
    cam = StaticImage(str(p))
    assert cam.capture() == b"\xff\xd8jpegdata\xff\xd9"


def test_static_image_missing_file_returns_none():
    assert StaticImage("/no/such/file.jpg").capture() is None


def test_camera_is_callable(tmp_path):
    p = tmp_path / "f.jpg"
    p.write_bytes(b"x")
    cam = StaticImage(str(p))
    assert cam() == b"x"  # __call__ delegates to capture()


def test_ipcamera_is_a_camera():
    assert isinstance(IPCamera("http://host/shot.jpg"), Camera)


def test_ipcamera_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("IPCAM_URL", "http://from-env/shot.jpg")
    assert IPCamera().url == "http://from-env/shot.jpg"
