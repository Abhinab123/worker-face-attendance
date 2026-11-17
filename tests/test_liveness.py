import pytest

from app.face import liveness

from .fakes import landmarks

CFG = {"LIVENESS_EAR_MIN_OPEN": 0.20, "LIVENESS_BLINK_RATIO": 0.70,
       "LIVENESS_YAW_CENTER_TOLERANCE": 0.08, "LIVENESS_YAW_DELTA": 0.12}


def test_eye_aspect_ratio():
    assert liveness.eye_aspect_ratio(landmarks(ear=0.3)["left_eye"]) == pytest.approx(0.3)
    with pytest.raises(ValueError):
        liveness.eye_aspect_ratio([(0, 0)] * 5)


def test_yaw_ratio():
    assert liveness.yaw_ratio(landmarks(yaw=0.5)) == pytest.approx(0.5)
    assert liveness.yaw_ratio(landmarks(yaw=0.7)) == pytest.approx(0.7)


@pytest.mark.parametrize("ears,expected", [
    ([0.3, 0.3, 0.1, 0.3, 0.3], True),        # open-closed-open
    ([0.3, 0.3, 0.3, 0.3, 0.3], False),       # static photo
    ([0.1, 0.1, 0.1, 0.1, 0.1], False),       # eyes never open
    ([0.3, 0.3, 0.3, 0.3, 0.1], False),       # closes but never reopens
    ([0.3, 0.1], False),                      # too few frames
])
def test_blink_detection(ears, expected):
    seq = [landmarks(ear=e) for e in ears]
    assert liveness.evaluate("blink", seq, CFG).passed is expected


@pytest.mark.parametrize("direction,yaws,expected", [
    ("turn_left", [0.5, 0.55, 0.65, 0.7, 0.5], True),
    ("turn_right", [0.5, 0.45, 0.35, 0.3, 0.5], True),
    ("turn_left", [0.5, 0.45, 0.35, 0.3, 0.5], False),   # wrong way
    ("turn_left", [0.5, 0.5, 0.5, 0.5, 0.5], False),     # no turn (photo)
    ("turn_left", [0.7, 0.7, 0.7, 0.7, 0.7], False),     # angled static photo, never centred
    ("turn_left", [0.7, 0.7, 0.7, 0.5, 0.5], False),     # turned only before facing camera
])
def test_turn_detection(direction, yaws, expected):
    seq = [landmarks(yaw=y) for y in yaws]
    assert liveness.evaluate(direction, seq, CFG).passed is expected


def test_wrong_way_reason():
    seq = [landmarks(yaw=y) for y in [0.5, 0.3, 0.3, 0.5]]
    assert liveness.evaluate("turn_left", seq, CFG).reason == "turned_wrong_way"
