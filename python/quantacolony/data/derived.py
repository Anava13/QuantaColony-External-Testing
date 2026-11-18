"""Derived results: calculations the DataManager runs on stored decisions.

Each derivation has a version. Increase it when a change alters the
derivation's output; cached results computed by an older version are then
recomputed on the next request. A derivation that reads the image also
depends on the index's crop region, so the region is part of its fingerprint.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from ..core.colony_set import ColonySet
from ..core.growth import GROWTH_VERSION, growth_mask
from ..core.measurements import COLUMNS as MEASUREMENT_COLUMNS
from ..core.measurements import measure_colonies

COVERAGE_COLUMNS = ("growable_px", "growth_px", "colony_px", "growth_coverage", "colony_coverage",
                    "separate_coverage", "captured_px", "capture_ratio", "uncertain_fraction", "supervised")


@dataclass(frozen=True)
class DerivationInputs:
    """Everything a derivation may read. All of it comes from the project file."""

    index_id: int
    colonies: ColonySet          # final colonies, photo coordinates
    px_per_mm: float | None      # None: the photo has no scale
    revision: int                # detection revision of the index
    photo_sha256: str
    region_sha256: str = ""      # the index's crop region (outline), for derivations that read the image
    crop: Callable = field(default=None, compare=False)   # () -> (image, mask, (x0, y0)); read only when needed


@dataclass(frozen=True)
class Derivation:
    name: str
    version: int
    columns: tuple[str, ...]
    compute: Callable[[DerivationInputs], list[dict]]
    cache: bool                  # False for calculations cheaper than a cache lookup
    reads_image: bool = False


def plate_coverage(inputs: DerivationInputs) -> list[dict]:
    """Growth and colony coverage of the index's growable area (core.growth), one row."""
    image, mask, (x0, y0) = inputs.crop()
    result = growth_mask(image, mask, inputs.colonies.translated(-x0, -y0))
    metrics = result.metrics()
    return [{key: (None if isinstance(value, float) and not np.isfinite(value) else value)
             for key, value in metrics.items()}]


CROWDING_COLUMNS = ("colony_id", "crowding_score")
CROWDING_VERSION = 1


def colony_crowding(inputs: DerivationInputs) -> list[dict]:
    """Each colony's crowding score (core.crowding) against the plate's growth mask."""
    from ..core.crowding import colony_scores
    image, mask, (x0, y0) = inputs.crop()
    colonies = inputs.colonies.translated(-x0, -y0)
    result = growth_mask(image, mask, colonies)
    scores = colony_scores(result.growth, colonies)
    return [{"colony_id": int(i), "crowding_score": (None if not np.isfinite(v) else float(v))}
            for i, v in zip(colonies.ids, scores)]


DERIVATIONS: dict[str, Derivation] = {d.name: d for d in [
    Derivation("colony_measurements", 2, tuple(MEASUREMENT_COLUMNS),
               lambda inputs: measure_colonies(inputs.colonies, inputs.px_per_mm), cache=True),
    Derivation("plate_coverage", GROWTH_VERSION, COVERAGE_COLUMNS, plate_coverage, cache=True, reads_image=True),
    Derivation("colony_crowding", 100 * GROWTH_VERSION + CROWDING_VERSION, CROWDING_COLUMNS, colony_crowding,
               cache=True, reads_image=True),
]}


def fingerprint(derivation: Derivation, inputs: DerivationInputs) -> str:
    """Identifies the inputs and the code version a cached value came from."""
    key = {"name": derivation.name, "version": derivation.version, "index": inputs.index_id,
           "revision": inputs.revision, "photo": inputs.photo_sha256, "px_per_mm": inputs.px_per_mm}
    if derivation.reads_image:
        key["region"] = inputs.region_sha256
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
