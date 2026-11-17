"""Basic webcam liveness heuristics from 68-point facial landmarks.

These are *deterrents*, not anti-spoofing. A video replay, a mask, or a person holding
a phone that plays a recorded blink/head turn can pass. See docs/recognition.md.

Landmark conventions (face_recognition / dlib 68-point model):
- ``left_eye`` / ``right_eye``: 6 points each, p1..p6 around the eye.
- ``chin``: 17 jaw points; index 0 is on the image-left side of the face.
- ``nose_bridge``: 4 points; the last is the nose tip (point 30).

Frames arrive *un-mirrored* (as the camera sees the worker), even though the kiosk
preview is mirrored for the worker. When a worker turns their head to *their* left,
the nose moves towards the image-right jaw edge, so the yaw ratio increases.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

INSTRUCTIONS = {
    "blink": "Look at the camera and blink slowly two times.",
    "turn_left": "Look at the camera, then slowly turn your head to YOUR left, and back.",
    "turn_right": "Look at the camera, then slowly turn your head to YOUR right, and back.",
    "none": "Look at the camera and hold still.",
}


@dataclass
class LivenessResult:
    passed: bool
    reason: str
    metrics: dict


def _dist(a, b) -> float:
    return float(np.linalg.norm(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)))


def eye_aspect_ratio(eye: list[tuple[int, int]]) -> float:
    """Soukupová & Čech (2016): (|p2-p6| + |p3-p5|) / (2 |p1-p4|)."""
    if len(eye) != 6:
        raise ValueError("eye landmark must have 6 points")
    horizontal = _dist(eye[0], eye[3])
    if horizontal == 0:
        return 0.0
    return (_dist(eye[1], eye[5]) + _dist(eye[2], eye[4])) / (2.0 * horizontal)


def yaw_ratio(landmarks: dict) -> float:
    """Horizontal nose-tip position between the jaw edges: ~0.5 frontal."""
    chin = landmarks["chin"]
    nose_tip = landmarks["nose_bridge"][-1]
    left_x, right_x = chin[0][0], chin[-1][0]
    span = right_x - left_x
    if span <= 0:
        return 0.5
    return (nose_tip[0] - left_x) / span


def evaluate_blink(seq: list[dict], cfg) -> LivenessResult:
    ears = [(eye_aspect_ratio(lm["left_eye"]) + eye_aspect_ratio(lm["right_eye"])) / 2.0 for lm in seq]
    metrics = {"frames": len(ears)}
    if len(ears) < 3:
        return LivenessResult(False, "not_enough_frames", metrics)
    open_level = float(np.percentile(ears, 80))
    metrics.update(open_level=round(open_level, 3), min_ear=round(min(ears), 3))
    if open_level < cfg["LIVENESS_EAR_MIN_OPEN"]:
        return LivenessResult(False, "eyes_not_visible", metrics)
    closed_limit = open_level * cfg["LIVENESS_BLINK_RATIO"]
    open_limit = open_level * 0.9
    for j, ear in enumerate(ears):
        if ear <= closed_limit:
            opened_before = any(e >= open_limit for e in ears[:j])
            opened_after = any(e >= open_limit for e in ears[j + 1:])
            if opened_before and opened_after:
                return LivenessResult(True, "blink_detected", metrics)
    return LivenessResult(False, "no_blink_detected", metrics)


def evaluate_turn(seq: list[dict], direction: str, cfg) -> LivenessResult:
    ratios = [yaw_ratio(lm) for lm in seq]
    metrics = {"frames": len(ratios)}
    if len(ratios) < 3:
        return LivenessResult(False, "not_enough_frames", metrics)
    metrics.update(min_ratio=round(min(ratios), 3), max_ratio=round(max(ratios), 3))
    tol, delta = cfg["LIVENESS_YAW_CENTER_TOLERANCE"], cfg["LIVENESS_YAW_DELTA"]
    centered = [i for i, r in enumerate(ratios) if abs(r - 0.5) <= tol]
    if direction == "turn_left":
        turned = [i for i, r in enumerate(ratios) if r >= 0.5 + delta]
        wrong = [i for i, r in enumerate(ratios) if r <= 0.5 - delta]
    else:
        turned = [i for i, r in enumerate(ratios) if r <= 0.5 - delta]
        wrong = [i for i, r in enumerate(ratios) if r >= 0.5 + delta]
    if not centered:
        return LivenessResult(False, "never_faced_camera", metrics)
    if not turned:
        reason = "turned_wrong_way" if wrong else "no_turn_detected"
        return LivenessResult(False, reason, metrics)
    # Must face the camera before turning (order matters, not just a static angled photo).
    if min(centered) > max(turned):
        return LivenessResult(False, "no_turn_detected", metrics)
    return LivenessResult(True, "turn_detected", metrics)


def evaluate(challenge_type: str, seq: list[dict], cfg) -> LivenessResult:
    if challenge_type == "none":
        return LivenessResult(True, "liveness_disabled", {})
    if challenge_type == "blink":
        return evaluate_blink(seq, cfg)
    if challenge_type in {"turn_left", "turn_right"}:
        return evaluate_turn(seq, challenge_type, cfg)
    return LivenessResult(False, "unknown_challenge", {})
