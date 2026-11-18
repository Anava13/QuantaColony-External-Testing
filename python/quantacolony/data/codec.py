"""Conversions between Python values and what the project file stores.

Arrays are stored in NumPy's .npy format without pickling, so opening a
shared project never runs code from the file.
"""

from __future__ import annotations

import io
import json
from dataclasses import asdict

import numpy as np

from ..core.params import DetectionParams


def array_to_blob(array: np.ndarray | None) -> bytes | None:
    if array is None:
        return None
    buffer = io.BytesIO()
    np.save(buffer, np.ascontiguousarray(array), allow_pickle=False)
    return buffer.getvalue()


def blob_to_array(blob: bytes | None) -> np.ndarray | None:
    if blob is None:
        return None
    return np.load(io.BytesIO(blob), allow_pickle=False)


def params_to_json(params: DetectionParams) -> str:
    return json.dumps(asdict(params))


def params_from_json(text: str) -> DetectionParams:
    return DetectionParams(**json.loads(text))
