"""Starting detection settings estimated from the image itself.

Brightness threshold and polarity: the pixels of the index split into two
brightness classes at Otsu's threshold. Colonies cover less of a plate than
agar does, so the smaller class is the colonies: when it is the darker class
the colonies are dark (positive level), otherwise they are light (negative
level, see detection.prepare_image).

Radius range: the connected blobs at that threshold are measured. Specks of a
few pixels and blobs larger than a tenth of the index are dropped, and the
radius range spans the typical blob sizes with room on both sides. The largest
colony area follows from the largest radius. Sensitivity and the detection
type are not estimated; they keep their current values.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
from skimage import measure
from skimage.filters import threshold_otsu

from .detection import _GRAY_WEIGHTS
from .params import PARAM_SPECS, DetectionParams

MIN_SPECK_PX = 6                  # blobs smaller than this are noise, not colonies
MAX_BLOB_FRACTION = 0.10          # blobs larger than this share of the index are background or clumps
MIN_RADIUS_PX = 2.0               # smaller blobs cannot be told apart from noise
LEVEL_MARGIN = 0.03               # move the cutoff slightly toward the agar so colony edges are kept


def estimate_parameters(image_rgb: np.ndarray, mask: np.ndarray | None, start: DetectionParams) -> DetectionParams:
    """Settings for this image, starting from `start` for the values that are not estimated.
    Returns `start` unchanged when the image has too little contrast to estimate from."""
    return estimate_with_extent(image_rgb, mask, start)[0]


def estimate_with_extent(image_rgb: np.ndarray, mask: np.ndarray | None,
                         start: DetectionParams) -> tuple[DetectionParams, str]:
    """estimate_parameters, with how much of the estimate the image allowed:
    'full' (level and radius range), 'level only' (fewer than three usable blobs), or
    'none' (too little contrast: `start` is returned unchanged)."""
    gray = np.round(image_rgb[..., :3].astype(float) @ _GRAY_WEIGHTS) / 255.0
    inside = mask if mask is not None else np.ones(gray.shape, dtype=bool)
    values = gray[inside]
    if values.size < 100 or float(values.max() - values.min()) < 0.05:
        return start, "none"
    cutoff = float(threshold_otsu(values))
    dark_share = float(np.mean(values <= cutoff))
    if dark_share <= 0.5:                                    # dark colonies on lighter agar
        level = min(cutoff + LEVEL_MARGIN, 0.99)
        colonies = (gray <= level) & inside
    else:                                                    # light colonies on darker agar
        level = -min(1.0 - (cutoff - LEVEL_MARGIN), 0.99)
        colonies = (gray >= 1.0 - abs(level)) & inside
    level = round(level, 2)

    areas = np.array([region.area for region in measure.regionprops(measure.label(colonies, connectivity=2))])
    largest = MAX_BLOB_FRACTION * float(inside.sum())
    areas = areas[(areas >= MIN_SPECK_PX) & (areas <= largest)]
    if areas.size < 3:
        return replace(start, level=level), "level only"
    radii = np.sqrt(areas / np.pi)
    low, high = np.percentile(radii, [25, 90])
    spec_min, spec_max = PARAM_SPECS["rmin"], PARAM_SPECS["rmax"]
    rmin = float(np.clip(math.floor(0.6 * low), MIN_RADIUS_PX, spec_min.maximum))
    rmax = float(np.clip(math.ceil(1.5 * high), rmin + 1, spec_max.maximum))
    return replace(start, level=level, rmin=rmin, rmax=rmax, maxpix=maxpix_for(rmax)), "full"


def maxpix_for(rmax: float) -> float:
    """Largest object for a maximum radius: twice a circle of that radius, rounded up to the control's
    step. Above one colony's area, so it never removes a single colony the radius limits keep."""
    spec = PARAM_SPECS["maxpix"]
    return float(np.clip(math.ceil(2 * math.pi * rmax ** 2 / spec.step) * spec.step, spec.step, spec.maximum))
