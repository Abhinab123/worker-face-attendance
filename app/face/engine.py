"""Face engine abstraction.

``DlibFaceEngine`` wraps the ``face_recognition`` library (dlib HOG/CNN detector,
68-point landmark predictor and ResNet face descriptor). Tests inject a fake engine
with the same interface so recognition *logic* can be tested with mocked encodings;
that says nothing about real-world biometric accuracy (see docs/testing.md).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .imaging import ImageLimits, ImageRejected, brightness, load_rgb, sharpness

Box = tuple[int, int, int, int]  # (top, right, bottom, left), face_recognition convention


@dataclass
class DetectedFace:
    box: Box
    encoding: np.ndarray | None = None
    landmarks: dict[str, list[tuple[int, int]]] | None = None

    @property
    def size_px(self) -> int:
        top, right, bottom, left = self.box
        return int(min(bottom - top, right - left))


class FaceEngine(Protocol):
    def detect(self, rgb: np.ndarray) -> list[Box]: ...

    def encode(self, rgb: np.ndarray, boxes: list[Box]) -> list[np.ndarray]: ...

    def landmarks(self, rgb: np.ndarray, boxes: list[Box]) -> list[dict[str, list[tuple[int, int]]]]: ...


class FaceEngineUnavailable(RuntimeError):
    pass


class DlibFaceEngine:
    """Real engine. ``face_recognition`` is imported lazily so the web UI still loads
    (and reports a clear error) if dlib is not installed correctly."""

    def __init__(self, model: str = "hog", upsample: int = 1, jitters: int = 1):
        self.model = model
        self.upsample = upsample
        self.jitters = jitters
        self._fr = None
        self._lock = threading.Lock()

    def _lib(self):
        if self._fr is None:
            with self._lock:
                if self._fr is None:
                    try:
                        import face_recognition  # noqa: PLC0415
                    except Exception as exc:  # ImportError, or pkg_resources errors from face_recognition_models
                        raise FaceEngineUnavailable(
                            "face_recognition/dlib could not be imported. See docs/troubleshooting.md."
                        ) from exc
                    self._fr = face_recognition
        return self._fr

    def available(self) -> bool:
        try:
            self._lib()
            return True
        except FaceEngineUnavailable:
            return False

    def detect(self, rgb: np.ndarray) -> list[Box]:
        return [tuple(int(v) for v in b) for b in
                self._lib().face_locations(rgb, number_of_times_to_upsample=self.upsample, model=self.model)]

    def encode(self, rgb: np.ndarray, boxes: list[Box]) -> list[np.ndarray]:
        return [np.asarray(e, dtype=np.float64) for e in
                self._lib().face_encodings(rgb, known_face_locations=boxes, num_jitters=self.jitters, model="small")]

    def landmarks(self, rgb: np.ndarray, boxes: list[Box]):
        return self._lib().face_landmarks(rgb, face_locations=boxes, model="large")


@dataclass
class FrameAnalysis:
    ok: bool
    code: str = "ok"
    message: str = ""
    face: DetectedFace | None = None
    face_count: int = 0
    brightness: float | None = None
    sharpness: float | None = None
    rgb: np.ndarray | None = field(default=None, repr=False)


GUIDANCE = {
    "too_dark": "Too dark. Face a light source or turn on more lights; avoid a bright window behind you.",
    "too_bright": "Overexposed. Move away from direct sunlight or a bright lamp.",
    "no_face": "No face found. Look straight at the camera with your whole face inside the frame.",
    "multiple_faces": "More than one face is visible. Only one person should be in front of the camera.",
    "face_too_small": "Face is too small. Move closer to the camera.",
    "blurry": "Image is blurry. Hold still and clean the camera lens.",
}


def analyze_frame(engine: FaceEngine, raw: bytes, cfg, *, want_encoding: bool = True,
                  want_landmarks: bool = False, keep_rgb: bool = False) -> FrameAnalysis:
    """Validate one image and extract exactly one face. Never raises for bad input."""
    try:
        rgb = load_rgb(raw, ImageLimits.from_config(cfg))
    except ImageRejected as rej:
        return FrameAnalysis(False, rej.code, rej.message)

    light = brightness(rgb)
    if light < cfg["MIN_BRIGHTNESS"]:
        return FrameAnalysis(False, "too_dark", GUIDANCE["too_dark"], brightness=light)
    if light > cfg["MAX_BRIGHTNESS"]:
        return FrameAnalysis(False, "too_bright", GUIDANCE["too_bright"], brightness=light)

    boxes = engine.detect(rgb)
    if not boxes:
        return FrameAnalysis(False, "no_face", GUIDANCE["no_face"], brightness=light)
    if len(boxes) > 1:
        return FrameAnalysis(False, "multiple_faces", GUIDANCE["multiple_faces"], face_count=len(boxes), brightness=light)

    face = DetectedFace(box=boxes[0])
    if face.size_px < cfg["MIN_FACE_SIZE_PX"]:
        return FrameAnalysis(False, "face_too_small", GUIDANCE["face_too_small"], face_count=1, brightness=light)
    sharp = sharpness(rgb, face.box)
    if sharp < cfg["MIN_SHARPNESS"]:
        return FrameAnalysis(False, "blurry", GUIDANCE["blurry"], face_count=1, brightness=light, sharpness=sharp)

    if want_encoding:
        encodings = engine.encode(rgb, [face.box])
        if not encodings:
            return FrameAnalysis(False, "no_face", GUIDANCE["no_face"], brightness=light)
        face.encoding = encodings[0]
    if want_landmarks:
        marks = engine.landmarks(rgb, [face.box])
        face.landmarks = marks[0] if marks else None
    return FrameAnalysis(True, face=face, face_count=1, brightness=light, sharpness=sharp,
                         rgb=rgb if keep_rgb else None)
