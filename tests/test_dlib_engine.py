"""Smoke tests for the *real* dlib engine using synthetic, face-free images.

They prove dlib/face_recognition are installed and wired correctly (detection runs, the
"no face" path works, encodings have the expected shape). They do NOT measure recognition
accuracy: no real faces are used. See docs/testing.md for consented accuracy evaluation.
"""
import numpy as np
import pytest

face_recognition = pytest.importorskip("face_recognition")

from app.face import codec  # noqa: E402
from app.face.engine import DlibFaceEngine, analyze_frame  # noqa: E402

from .fakes import make_png  # noqa: E402

CFG = {"MAX_IMAGE_BYTES": 2_000_000, "MIN_IMAGE_DIM": 240, "MAX_IMAGE_DIM": 1920, "PROCESSING_MAX_WIDTH": 640,
       "MIN_BRIGHTNESS": 50, "MAX_BRIGHTNESS": 215, "MIN_FACE_SIZE_PX": 80, "MIN_SHARPNESS": 20}


def test_engine_loads_and_finds_no_face_in_noise():
    engine = DlibFaceEngine()
    assert engine.available()
    result = analyze_frame(engine, make_png(7), CFG)
    assert not result.ok and result.code == "no_face"


def test_encoder_output_shape_and_codec():
    engine = DlibFaceEngine()
    rgb = np.random.default_rng(0).integers(0, 255, size=(300, 300, 3), dtype=np.uint8)
    # Force an encoding on an arbitrary box: checks the descriptor pipeline, not accuracy.
    encodings = engine.encode(rgb, [(50, 250, 250, 50)])
    assert len(encodings) == 1 and encodings[0].shape == (128,)
    assert len(codec.encode(encodings[0])) == codec.ENCODING_BYTES
    marks = engine.landmarks(rgb, [(50, 250, 250, 50)])
    assert len(marks[0]["chin"]) == 17 and len(marks[0]["left_eye"]) == 6
