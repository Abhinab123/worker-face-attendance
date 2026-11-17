"""1:N matching of a probe encoding against enrolled, active workers.

Face *distance* is the Euclidean distance between 128-d dlib descriptors. Lower means more
similar. It is a matching metric, not a calibrated probability or confidence percentage.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import numpy as np
import sqlalchemy as sa
from flask import current_app

from ..extensions import db
from ..face import codec
from ..models import ENCODING_ACTIVE, WORKER_ACTIVE, FaceEncoding, Worker

log = logging.getLogger(__name__)

MATCH = "match"
UNKNOWN = "unknown"
AMBIGUOUS = "ambiguous"
NO_ENROLLMENTS = "no_enrollments"


@dataclass
class MatchResult:
    status: str
    worker_id: int | None = None
    distance: float | None = None
    second_distance: float | None = None


@dataclass
class _Index:
    signature: tuple
    worker_ids: np.ndarray      # (N,) sorted
    matrix: np.ndarray          # (N, 128)
    group_starts: np.ndarray    # start offsets of each worker's rows
    unique_workers: np.ndarray  # worker id per group


class EncodingCache:
    """Process-local cache of active encodings for active workers.

    Invalidated explicitly on enrollment changes and worker (de)activation, and also
    re-validated on every lookup with a cheap aggregate query so that changes made by
    another process (e.g. a second worker process or a CLI purge) are picked up.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._index: _Index | None = None

    def invalidate(self) -> None:
        with self._lock:
            self._index = None

    @staticmethod
    def _active_filter():
        return sa.and_(FaceEncoding.status == ENCODING_ACTIVE, Worker.status == WORKER_ACTIVE)

    def _signature(self) -> tuple:
        row = db.session.execute(
            sa.select(sa.func.count(FaceEncoding.id), sa.func.coalesce(sa.func.max(FaceEncoding.id), 0),
                      sa.func.coalesce(sa.func.sum(FaceEncoding.id), 0))
            .join(Worker, Worker.id == FaceEncoding.worker_id).where(self._active_filter())
        ).one()
        return tuple(int(v) for v in row)

    def get(self) -> _Index:
        signature = self._signature()
        with self._lock:
            if self._index is not None and self._index.signature == signature:
                return self._index
        rows = db.session.execute(
            sa.select(FaceEncoding.worker_id, FaceEncoding.encoding)
            .join(Worker, Worker.id == FaceEncoding.worker_id).where(self._active_filter())
            .order_by(FaceEncoding.worker_id, FaceEncoding.id)
        ).all()
        ids, vectors = [], []
        for worker_id, blob in rows:
            try:
                vectors.append(codec.decode(blob))
                ids.append(worker_id)
            except codec.EncodingFormatError:
                log.warning("Skipping malformed face encoding for worker id %s", worker_id)
        worker_ids = np.asarray(ids, dtype=np.int64)
        matrix = np.vstack(vectors) if vectors else np.zeros((0, codec.ENCODING_DIM))
        unique_workers, group_starts = (np.unique(worker_ids, return_index=True) if len(ids)
                                        else (np.zeros(0, np.int64), np.zeros(0, np.int64)))
        index = _Index(signature, worker_ids, matrix, group_starts, unique_workers)
        with self._lock:
            self._index = index
        return index


def get_cache() -> EncodingCache:
    return current_app.extensions["encoding_cache"]


def invalidate_cache() -> None:
    get_cache().invalidate()


def per_worker_min_distance(index: _Index, probe: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    distances = np.linalg.norm(index.matrix - probe[None, :], axis=1)
    return index.unique_workers, np.minimum.reduceat(distances, index.group_starts)


def decide(worker_ids: np.ndarray, distances: np.ndarray, threshold: float, margin: float) -> MatchResult:
    """Threshold + ambiguity-margin decision over per-worker best distances."""
    if len(worker_ids) == 0:
        return MatchResult(NO_ENROLLMENTS)
    order = np.argsort(distances)
    best = float(distances[order[0]])
    second = float(distances[order[1]]) if len(order) > 1 else None
    if best > threshold:
        return MatchResult(UNKNOWN, distance=best, second_distance=second)
    if second is not None and second <= threshold + margin and (second - best) < margin:
        # Two different workers are both close: refuse rather than guess.
        return MatchResult(AMBIGUOUS, distance=best, second_distance=second)
    return MatchResult(MATCH, worker_id=int(worker_ids[order[0]]), distance=best, second_distance=second)


def match_encoding(probe: np.ndarray) -> MatchResult:
    cfg = current_app.config
    index = get_cache().get()
    if index.matrix.shape[0] == 0:
        return MatchResult(NO_ENROLLMENTS)
    workers, dists = per_worker_min_distance(index, np.asarray(probe, dtype=np.float64))
    return decide(workers, dists, cfg["FACE_MATCH_THRESHOLD"], cfg["FACE_AMBIGUITY_MARGIN"])


@dataclass
class SequenceMatch:
    status: str
    worker_id: int | None = None
    distance: float | None = None
    matched_frames: int = 0


def match_sequence(encodings: list[np.ndarray]) -> SequenceMatch:
    """Require several frames to agree on one worker; any frame that is ambiguous or that
    matches a *different* worker rejects the whole sequence."""
    cfg = current_app.config
    results = [match_encoding(e) for e in encodings]
    if any(r.status == NO_ENROLLMENTS for r in results):
        return SequenceMatch(NO_ENROLLMENTS)
    if any(r.status == AMBIGUOUS for r in results):
        return SequenceMatch(AMBIGUOUS)
    matched = [r for r in results if r.status == MATCH]
    identities = {r.worker_id for r in matched}
    if len(identities) > 1:
        return SequenceMatch(AMBIGUOUS, matched_frames=len(matched))
    if len(matched) < min(cfg["RECOGNITION_MIN_MATCHED_FRAMES"], len(encodings)) or not matched:
        return SequenceMatch(UNKNOWN, matched_frames=len(matched))
    return SequenceMatch(MATCH, worker_id=matched[0].worker_id,
                         distance=float(np.median([r.distance for r in matched])), matched_frames=len(matched))


def nearest_other_workers(probe: np.ndarray, exclude_worker_id: int, threshold: float) -> list[tuple[int, float]]:
    """Enrollment duplicate check: compare against *all* stored encodings of other workers,
    including inactive workers and pending encodings (re-enrollment under a new ID)."""
    rows = db.session.execute(
        sa.select(FaceEncoding.worker_id, FaceEncoding.encoding).where(FaceEncoding.worker_id != exclude_worker_id)
    ).all()
    best: dict[int, float] = {}
    for worker_id, blob in rows:
        try:
            d = float(np.linalg.norm(codec.decode(blob) - probe))
        except codec.EncodingFormatError:
            continue
        if d <= threshold and d < best.get(worker_id, np.inf):
            best[worker_id] = d
    return sorted(best.items(), key=lambda kv: kv[1])
