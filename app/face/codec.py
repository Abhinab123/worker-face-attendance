"""Safe serialization of face encodings.

Encodings are stored as exactly 128 little-endian float64 values (1024 bytes).
Deserialization uses ``numpy.frombuffer`` with a fixed dtype and length check;
``pickle``/``np.load(allow_pickle=True)`` are never used.
"""
from __future__ import annotations

import numpy as np

ENCODING_DIM = 128
_DTYPE = np.dtype("<f8")
ENCODING_BYTES = ENCODING_DIM * _DTYPE.itemsize


class EncodingFormatError(ValueError):
    pass


def encode(vector: np.ndarray) -> bytes:
    arr = np.asarray(vector, dtype=_DTYPE).reshape(-1)
    if arr.shape != (ENCODING_DIM,):
        raise EncodingFormatError(f"expected {ENCODING_DIM} values, got {arr.shape}")
    if not np.all(np.isfinite(arr)):
        raise EncodingFormatError("encoding contains non-finite values")
    return arr.tobytes()


def decode(blob: bytes) -> np.ndarray:
    if not isinstance(blob, (bytes, bytearray, memoryview)) or len(blob) != ENCODING_BYTES:
        raise EncodingFormatError("stored encoding has an unexpected size")
    arr = np.frombuffer(bytes(blob), dtype=_DTYPE).astype(np.float64)
    if not np.all(np.isfinite(arr)):
        raise EncodingFormatError("stored encoding contains non-finite values")
    return arr
