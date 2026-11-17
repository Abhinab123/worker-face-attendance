"""Test doubles for the face engine.

These produce *mocked* encodings and landmarks so that the recognition, enrollment and
attendance logic can be tested deterministically. They do not exercise dlib and say
nothing about real-world biometric accuracy.

Each test image carries an 8x8 solid-colour "tag" in its top-left corner (PNG, lossless).
The fake engine reads the tag and returns the faces registered for it.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
from PIL import Image

BOX = (60, 330, 280, 110)  # top, right, bottom, left -> 220 px face


@dataclass
class FakeFace:
    encoding: np.ndarray
    landmarks: dict | None = None
    box: tuple = BOX


class FakeEngine:
    def __init__(self) -> None:
        self._scenes: dict[int, list[FakeFace]] = {}
        self._next = 1
        self.calls = 0

    def register(self, faces: list[FakeFace]) -> int:
        tag = self._next
        self._next += 1
        self._scenes[tag] = faces
        return tag

    @staticmethod
    def _tag(rgb: np.ndarray) -> int:
        r, g, b = (int(v) for v in rgb[2, 2])
        return (r << 16) | (g << 8) | b

    def _faces(self, rgb):
        return self._scenes.get(self._tag(rgb), [])

    def detect(self, rgb):
        self.calls += 1
        return [f.box for f in self._faces(rgb)]

    def encode(self, rgb, boxes):
        return [f.encoding for f in self._faces(rgb)][: len(boxes)]

    def landmarks(self, rgb, boxes):
        return [f.landmarks for f in self._faces(rgb) if f.landmarks][: len(boxes)]


def make_png(tag: int, *, width: int = 480, height: int = 360, low: int = 60, high: int = 200, seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed + tag)
    arr = rng.integers(low, high, size=(height, width, 3), dtype=np.uint8)
    arr[:8, :8] = ((tag >> 16) & 255, (tag >> 8) & 255, tag & 255)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


class Person:
    """A synthetic identity: a base 128-d vector; samples add small noise.

    Scale chosen so different people are ~0.9 apart and samples of one person ~0.2-0.3,
    mimicking typical dlib descriptor distances.
    """

    def __init__(self, seed: int):
        self.rng = np.random.default_rng(seed)
        self.base = self.rng.normal(0, 0.055, 128)

    def sample(self, noise: float = 0.012) -> np.ndarray:
        return self.base + self.rng.normal(0, noise, 128)


def landmarks(*, ear: float = 0.30, yaw: float = 0.5) -> dict:
    """Synthetic 68-point landmark dict with a controllable eye aspect ratio and yaw ratio."""
    def eye(x0, y):
        w = 30.0
        h = ear * w  # EAR = (h + h) / (2w)
        return [(x0, y), (x0 + 10, y - h / 2), (x0 + 20, y - h / 2), (x0 + w, y),
                (x0 + 20, y + h / 2), (x0 + 10, y + h / 2)]
    chin = [(100 + i * 12.5, 250) for i in range(17)]  # x from 100 to 300
    nose_x = 100 + yaw * 200
    return {
        "chin": chin,
        "left_eye": eye(140, 150),
        "right_eye": eye(230, 150),
        "nose_bridge": [(200, 140), (200, 160), (200, 180), (nose_x, 200)],
        "nose_tip": [(nose_x - 10, 205), (nose_x, 207), (nose_x + 10, 205)],
    }


@dataclass
class FrozenClock:
    now: datetime
    history: list = field(default_factory=list)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now = self.now + timedelta(**kwargs)
