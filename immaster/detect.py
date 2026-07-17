"""
Object detection on the Hailo-10H NPU

Runs a COCO YOLO HEF (default /usr/share/hailo-models/yolov11m_h10.hef) on a
camera frame via the HailoRT Python API and returns detections (label, score,
center x/y in 0..1, area). COCO includes cup, bottle, chair, person, laptop, etc.

The hailo-models HEFs are compiled WITH NMS, so HailoRT returns decoded boxes,
no anchor decoding needed. The exact NMS output layout can vary by build, so the
CLI dumps the raw structure on first run; parsing is best-effort and robust to
the known coordinate-order quirk (center = midpoint is order-agnostic).

NOTE on the single NPU: hailo-ollama (the LLM) also uses the NPU. Test this
detector STANDALONE first (LLM loop not running). If VDevice() fails with
"device in use", we handle contention separately (see PERCEPTION_PLAN).

CLI (on the Pi):
    python3 -m immaster.detect vision_test.jpg        # from a saved frame
    python3 -m immaster.detect                        # live from IPCAM_URL
"""

from __future__ import annotations
import io
import os
from abc import ABC, abstractmethod

HEF_PATH = os.environ.get("HAILO_HEF", "/usr/share/hailo-models/yolov11m_h10.hef")
SCORE_THRESH = float(os.environ.get("DET_THRESH", "0.4"))
# On-chip NMS confidence floor, anything below this is dropped IN HARDWARE before
# we see it. Keep it low so dim/hard objects (black mug!) still come through; the
# software SCORE_THRESH does the final filtering.
NMS_SCORE = float(os.environ.get("HAILO_NMS_THRESH", "0.15"))
NMS_MAX_PER_CLASS = int(os.environ.get("HAILO_NMS_MAX", "25"))

COCO = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
    "toothbrush",
]


class ObjectDetector(ABC):
    """Turns a JPEG frame into a list of detections.

    Each detection is a dict: ``{label, score, cx, cy, area, box}`` with
    coordinates normalized 0..1 (cx/cy are the box center; box is
    [x0, y0, x1, y1]). Implement ``detect()`` to plug in a different model (a
    different YOLO, an ONNX runtime, a cloud API, whatever) — the perception loop
    only depends on this shape, so it works with any of them unchanged.
    """

    @abstractmethod
    def detect(self, jpeg: bytes, thresh: float = SCORE_THRESH) -> list[dict]:
        #Return detections for one JPEG frame.

    def close(self) -> None:
        #Release any resources (override if your detector holds hardware).


class Detector(ObjectDetector):

    def __init__(self, hef_path: str = HEF_PATH):
        # Modern InferModel async API (the legacy InferVStreams/configure path is
        # HAILO_NOT_IMPLEMENTED on Hailo-10H / HailoRT 5.x).
        from hailo_platform import VDevice, FormatType
        params = VDevice.create_params()
        self.target = VDevice(params)
        self.infer_model = self.target.create_infer_model(hef_path)
        self.infer_model.set_batch_size(1)
        self.infer_model.input().set_format_type(FormatType.UINT8)
        self.infer_model.output().set_format_type(FormatType.FLOAT32)
        # Lower the hardware NMS threshold so faint objects aren't dropped on-chip.
        out = self.infer_model.output()
        for meth, val in (("set_nms_score_threshold", NMS_SCORE),
                          ("set_nms_iou_threshold", 0.45),
                          ("set_nms_max_proposals_per_class", NMS_MAX_PER_CLASS)):
            try:
                getattr(out, meth)(val)
            except Exception as e:
                print(f"note: {meth} unavailable ({e})")
        self.in_h, self.in_w = self.infer_model.input().shape[0], self.infer_model.input().shape[1]
        self.in_name = self.infer_model.input().name
        self.out_name = self.infer_model.output().name
        self._np = __import__("numpy")
        self.configured = self.infer_model.configure()

    def infer_raw(self, jpeg: bytes):
        np = self._np
        from PIL import Image
        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        W, H = img.size
        # Letterbox: preserve aspect ratio (stretching to a square wrecks detection).
        scale = min(self.in_w / W, self.in_h / H)
        nw, nh = int(round(W * scale)), int(round(H * scale))
        px, py = (self.in_w - nw) // 2, (self.in_h - nh) // 2
        canvas = Image.new("RGB", (self.in_w, self.in_h), (114, 114, 114))
        canvas.paste(img.resize((nw, nh)), (px, py))
        self._lb = (scale, px, py, W, H)  # to map boxes back to original coords
        arr = np.array(canvas, dtype=np.uint8, order="C")  # writeable, contiguous
        bindings = self.configured.create_bindings()
        bindings.input().set_buffer(arr)
        out_buf = np.empty(self.infer_model.output().shape, dtype=np.float32)
        bindings.output().set_buffer(out_buf)
        self.configured.run([bindings], 10000)  # timeout ms
        return {self.out_name: bindings.output().get_buffer()}

    def detect(self, jpeg: bytes, thresh: float = SCORE_THRESH) -> list[dict]:
        dets = parse_detections(self.infer_raw(jpeg), thresh)
        return self._unletterbox(dets)

    def _unletterbox(self, dets: list[dict]) -> list[dict]:
        scale, px, py, W, H = self._lb
        iw, ih = self.in_w, self.in_h
        for d in dets:
            x0, y0, x1, y1 = d["box"]
            nx0 = min(1, max(0, ((x0 * iw) - px) / scale / W))
            nx1 = min(1, max(0, ((x1 * iw) - px) / scale / W))
            ny0 = min(1, max(0, ((y0 * ih) - py) / scale / H))
            ny1 = min(1, max(0, ((y1 * ih) - py) / scale / H))
            d["box"] = [round(nx0, 3), round(ny0, 3), round(nx1, 3), round(ny1, 3)]
            d["cx"], d["cy"] = round((nx0 + nx1) / 2, 2), round((ny0 + ny1) / 2, 2)
            d["area"] = round((nx1 - nx0) * (ny1 - ny0), 3)
        return dets

    def close(self):
        # Release the configured model BEFORE the VDevice, else HailoRT warns
        # "VDevice released while the CIM is in use" at teardown.
        self.configured = None
        self.infer_model = None
        self.target = None


def parse_detections(results: dict, thresh: float = SCORE_THRESH) -> list[dict]:
    """Best-effort parse of HailoRT NMS-by-class output into a flat detection list.
    Each: {label, score, cx, cy, area} with coords normalized 0..1. Center is
    computed as a midpoint, so it's robust to box coordinate ordering."""
    import numpy as np
    dets: list[dict] = []
    for _name, val in results.items():
        per_class = val
        # unwrap a batch dimension if present
        if isinstance(per_class, list) and len(per_class) == 1:
            per_class = per_class[0]
        elif isinstance(per_class, np.ndarray) and per_class.ndim >= 3:
            per_class = per_class[0]
        try:
            iterator = list(enumerate(per_class))
        except TypeError:
            continue
        for cls_idx, cls_dets in iterator:
            cd = np.asarray(cls_dets)
            if cd.size == 0:
                continue
            if cd.ndim == 1:
                cd = cd[None, :]
            for row in cd:
                if len(row) < 5:
                    continue
                a, b, c, d, score = (float(x) for x in row[:5])
                if score < thresh:
                    continue
                # positions 0,2 are the two vertical coords; 1,3 the horizontal.
                # min/max makes the box order-agnostic (robust to the coord quirk).
                x0, x1 = sorted((b, d))
                y0, y1 = sorted((a, c))
                cy, cx = (y0 + y1) / 2, (x0 + x1) / 2
                area = (x1 - x0) * (y1 - y0)
                label = COCO[cls_idx] if cls_idx < len(COCO) else f"class{cls_idx}"
                dets.append({"label": label, "score": round(score, 2),
                             "cx": round(cx, 2), "cy": round(cy, 2),
                             "area": round(area, 3),
                             "box": [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]})
    dets.sort(key=lambda d: d["score"], reverse=True)
    return dets


def describe_detections(dets: list[dict], max_items: int = 5) -> str:
    # turn detections into a short text observation for the LLM.
    if not dets:
        return "no recognizable objects in view"
    parts = []
    for d in dets[:max_items]:
        cx = d["cx"]
        side = "left" if cx < 0.4 else "right" if cx > 0.6 else "center"
        dist = "near" if d["area"] > 0.12 else "far"
        parts.append(f"{d['label']} ({side}, {dist})")
    return "; ".join(parts)


def _cli():
    import sys
    from . import vision
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        jpeg = open(sys.argv[1], "rb").read()
    else:
        jpeg = vision.capture_frame()
        if jpeg is None:
            sys.exit("no frame — pass a .jpg path or set IPCAM_URL")

    print(f"loading detector: {HEF_PATH}")
    det = Detector()
    print(f"input: {det.in_name}  {det.in_w}x{det.in_h}")
    raw = det.infer_raw(jpeg)
    # first-run diagnostic: dump the raw output structure
    import numpy as np
    print("\n--- raw non-empty classes (ANY score) ---")
    total = 0
    for name, val in raw.items():
        if isinstance(val, list):
            for ci, arr in enumerate(val):
                a = np.asarray(arr)
                if a.size:
                    n = a.shape[0] if a.ndim > 1 else 1
                    total += n
                    label = COCO[ci] if ci < len(COCO) else ci
                    print(f"  [{ci}] {label}: shape={a.shape}  rows={a.tolist()[:3]}")
        elif isinstance(val, np.ndarray):
            print(f"  {name}: ndarray shape={val.shape} dtype={val.dtype}")
    print(f"  total raw boxes (all scores): {total}")

    dets = parse_detections(raw, thresh=0.1)
    print("\n--- detections (score>=0.1) ---")
    for d in dets:
        print(" ", d)
    print("\nobservation:", describe_detections(dets))
    det.close()


if __name__ == "__main__":
    _cli()
