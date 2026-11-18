"""Automatic colony detection (Qt-free).

Irregular mode reproduces the MATLAB pipeline exactly when splitting is off: on the test
image it returns the same 33 colonies as the saved MATLAB result. Circular mode uses
scikit-image's Hough transform in place of MATLAB's imfindcircles; the two algorithms
differ, so circular results approximate the MATLAB ones.

Splitting touching colonies (irregular mode, params.split). Touching colonies form one blob
after thresholding. Without splitting, the blob either fails the single-colony test (too
large, too elongated) and is dropped, or passes as one oversized colony. With splitting, a
blob that is larger than the largest colony or more elongated than split_elongation is cut
apart: each pixel's distance to the blob's edge peaks at the center of every colony in it,
and between two colonies the distance dips where the blob narrows. Peaks that stand at least
split_pinch x the blob's widest half-width above that dip are kept as colony centers
(h-maxima), and the blob is divided between them along the dips (watershed). Every piece then
faces the same single-colony test as any blob. A blob with one such peak stays whole, so an
oval colony is not cut in two.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage
from skimage import measure, morphology, segmentation
from skimage.transform import hough_circle, hough_circle_peaks

from .colony_set import KIND_CIRCULAR, KIND_IRREGULAR, KIND_SPLIT, ColonySet
from .params import CIRCULAR, DetectionParams

# Increase when a change alters which colonies automatic detection returns.
# Each committed result records the version that produced it.
DETECTION_VERSION = 1            # results made with splitting record SPLIT_DETECTION_VERSION instead
SPLIT_DETECTION_VERSION = 2

# MATLAB rgb2gray weights
_GRAY_WEIGHTS = np.array([0.298936021293775, 0.587043074451121, 0.114020904255103])


def prepare_image(image_rgb: np.ndarray, level: float) -> np.ndarray:
    """Threshold the plate image (prepareImage).

    Returns a boolean mask that is True on candidate colony pixels.
    level >= 0: pixels darker than or equal to level are kept.
    level <  0: pixels brighter than or equal to 1 - |level| are kept.
    """
    gray = np.round(image_rgb[..., :3].astype(float) @ _GRAY_WEIGHTS) / 255.0
    if level >= 0:
        return ~(gray > level)
    return ~((1.0 - gray) > abs(level))


def apply_roi_mask(mask: np.ndarray, roi: tuple[float, float, float, float] | None) -> np.ndarray:
    """Keep only the rectangle roi = (x, y, width, height) (applyROIMask)."""
    if roi is None:
        return mask
    x, y, w, h = roi
    rows, cols = mask.shape
    r0, r1 = max(0, int(round(y))), min(rows, int(round(y + h)) + 1)
    c0, c1 = max(0, int(round(x))), min(cols, int(round(x + w)) + 1)
    limited = np.zeros_like(mask)
    limited[r0:r1, c0:c1] = mask[r0:r1, c0:c1]
    return limited


def detection_version(params: DetectionParams) -> int:
    """The version that records how a result with these settings was made."""
    return SPLIT_DETECTION_VERSION if params.split and params.detection_type != CIRCULAR else DETECTION_VERSION


def _one_colony(region, params: DetectionParams, min_area: float, max_area: float) -> bool:
    area = region.area
    return area < params.maxpix and min_area <= area <= max_area and region.eccentricity <= params.sense


def _wants_split(region, params: DetectionParams, min_area: float, max_area: float) -> bool:
    area = region.area
    return (params.split and min_area <= area < params.maxpix
            and (area > max_area or region.eccentricity > params.split_elongation))


def colony_centers(distance: np.ndarray, height: float) -> tuple[np.ndarray, int]:
    """Labelled colony centers: the peaks of the distance map, where two peaks separated by a dip
    shallower than `height` count as one (the h-maxima transform: reconstruct distance - height under
    distance by dilation; the dip between such peaks fills into one flat top; the regional maxima of
    the result are the centers). The reconstruction is written out here because scikit-image's gives
    wrong results under NumPy 2.5."""
    surface = distance - height
    eight = np.ones((3, 3), dtype=bool)
    while True:
        grown = np.minimum(ndimage.grey_dilation(surface, footprint=eight), distance)
        if np.array_equal(grown, surface):
            break
        surface = grown
    return ndimage.label(morphology.local_maxima(surface, connectivity=2), structure=eight)


def split_blob(region, params: DetectionParams, min_area: float = 0.0, max_area: float = np.inf) -> list:
    """The colonies one touching blob holds (see the module notes), as regions in the coordinates of the
    blob's bounding box (add region.bbox[:2] for image coordinates). A blob comes back whole ([region],
    in image coordinates) when it has a single colony center, or when any piece would fail the
    single-colony test: a ragged colony is not broken into fragments."""
    shape = ndimage.binary_fill_holes(region.image)
    distance = ndimage.distance_transform_edt(np.pad(shape, 1))[1:-1, 1:-1]
    height = params.split_pinch * float(distance.max())
    if height <= 0:
        return [region]
    centers, count = colony_centers(distance, height)
    if count < 2:
        return [region]
    pieces = segmentation.watershed(-distance, centers, mask=region.image)
    parts = measure.regionprops(pieces)
    if not all(_one_colony(part, params, min_area, max_area) for part in parts):
        return [region]
    return parts


def detect_irregular_colonies(mask: np.ndarray, params: DetectionParams) -> ColonySet:
    """Connected components filtered by area and eccentricity (detectIrregularColonies); touching
    colonies are split first when params.split is on."""
    labels = measure.label(mask, connectivity=2)
    min_area = np.pi * params.rmin ** 2
    max_area = np.pi * params.rmax ** 2

    centers, radii, boundaries, eccentricities, kinds = [], [], [], [], []
    for blob in measure.regionprops(labels):
        parts = split_blob(blob, params, min_area, max_area) if _wants_split(blob, params, min_area, max_area) \
            else [blob]
        split = len(parts) > 1
        kind = KIND_SPLIT if split else KIND_IRREGULAR
        r0, c0 = (blob.bbox[0], blob.bbox[1]) if split else (0, 0)     # pieces are in the blob's own frame
        for region in parts:
            if not _one_colony(region, params, min_area, max_area):
                continue
            row, col = region.centroid
            centers.append((col + c0, row + r0))
            radii.append(np.sqrt(region.area / np.pi))
            eccentricities.append(region.eccentricity)
            boundaries.append(_trace_outline(region) + np.array([c0, r0], dtype=float))
            kinds.append(kind)

    return ColonySet.from_columns(
        centers=np.array(centers).reshape(-1, 2),
        radii=radii,
        boundaries=boundaries,
        kinds=kinds,
        eccentricities=eccentricities,
    )


def _trace_outline(region) -> np.ndarray:
    """Outline of one region as (M, 2) x/y points, in image coordinates."""
    r0, c0, _, _ = region.bbox
    padded = np.pad(region.image, 1).astype(float)
    contours = measure.find_contours(padded, 0.5)
    outline = max(contours, key=len)            # outer edge; inner contours are holes
    return np.column_stack([outline[:, 1] + c0 - 1, outline[:, 0] + r0 - 1])


def detect_circular_colonies(mask: np.ndarray, params: DetectionParams) -> ColonySet:
    """Hough circle detection on the filtered colony mask (detectCircularColonies)."""
    labels = measure.label(mask, connectivity=2)
    sizes = np.bincount(labels.ravel())
    keep = sizes < params.maxpix
    keep[0] = False
    filtered = keep[labels]
    edges = filtered & ~ndimage.binary_erosion(filtered)

    radii_range = np.arange(max(1, int(params.rmin)), int(params.rmax) + 1)
    if len(radii_range) == 0 or not edges.any():
        return ColonySet.empty()
    accumulator = hough_circle(edges, radii_range)
    # The normalized accumulator is the fraction of a circle's edge that is
    # present. Higher sensitivity lowers the required fraction, so more
    # circles are found, as with imfindcircles.
    threshold = float(np.clip(1.45 - params.sense, 0.3, 0.95))
    _, cx, cy, found_radii = hough_circle_peaks(
        accumulator, radii_range, threshold=threshold,
        min_xdistance=int(params.rmin), min_ydistance=int(params.rmin))

    # A colony is a filled blob, so its center must lie on the mask
    rows = np.clip(np.asarray(cy, dtype=int), 0, filtered.shape[0] - 1)      # empty peaks come back as floats
    cols = np.clip(np.asarray(cx, dtype=int), 0, filtered.shape[1] - 1)
    centered = filtered[rows, cols]
    n = int(centered.sum())
    return ColonySet.from_columns(
        centers=np.column_stack([cx, cy])[centered].astype(float),
        radii=found_radii[centered].astype(float),
        boundaries=[None] * n,
        kinds=[KIND_CIRCULAR] * n,
    )


def calc_overlap_fraction(r1: float, r2: float, d: float) -> float:
    """Fraction of the smaller circle (r2) covered by the larger (r1)."""
    if d >= r1 + r2:
        return 0.0
    if d <= abs(r1 - r2):
        return 1.0
    part1 = r1 ** 2 * np.arccos((d ** 2 + r1 ** 2 - r2 ** 2) / (2 * d * r1))
    part2 = r2 ** 2 * np.arccos((d ** 2 + r2 ** 2 - r1 ** 2) / (2 * d * r2))
    part3 = 0.5 * np.sqrt((-d + r1 + r2) * (d + r1 - r2) * (d - r1 + r2) * (d + r1 + r2))
    return float((part1 + part2 - part3) / (np.pi * r2 ** 2))


def remove_overlaps(colonies: ColonySet, threshold: float = 0.5) -> ColonySet:
    """Drop a colony when >= threshold of its area lies inside a larger one (removeOverlaps)."""
    n = len(colonies)
    if n <= 1:
        return colonies
    keep = np.ones(n, dtype=bool)
    order = np.argsort(-colonies.radii, kind="stable")
    for a, larger in enumerate(order):
        if not keep[larger]:
            continue
        for smaller in order[a + 1:]:
            if not keep[smaller]:
                continue
            d = float(np.hypot(*(colonies.centers[larger] - colonies.centers[smaller])))
            fraction = calc_overlap_fraction(colonies.radii[larger], colonies.radii[smaller], d)
            if fraction >= threshold:
                keep[smaller] = False
    return colonies.subset(keep)


def perform_detection(image_rgb: np.ndarray, params: DetectionParams,
                      roi: tuple[float, float, float, float] | None = None,
                      region_mask: np.ndarray | None = None) -> ColonySet:
    """Full automatic detection for one image (performDetection).

    region_mask marks the pixels that belong to the index (from cropping);
    pixels outside it are never colony pixels, even where the masked-out
    black would pass a dark-colony threshold.
    Returned colonies carry placeholder ids (-1); the session assigns real ids.
    """
    mask = apply_roi_mask(prepare_image(image_rgb, params.level), roi)
    if region_mask is not None:
        mask = mask & region_mask
    if params.detection_type == CIRCULAR:
        found = detect_circular_colonies(mask, params)
    else:
        found = detect_irregular_colonies(mask, params)
    return remove_overlaps(found)


def filter_valid_colonies(candidates: ColonySet, existing: ColonySet,
                          threshold: float = 0.5) -> ColonySet:
    """Drop candidates that overlap an existing colony by >= threshold (filterValidColonies)."""
    if len(candidates) == 0 or len(existing) == 0:
        return candidates
    keep = np.ones(len(candidates), dtype=bool)
    for i in range(len(candidates)):
        for j in range(len(existing)):
            d = float(np.hypot(*(candidates.centers[i] - existing.centers[j])))
            r_new, r_old = candidates.radii[i], existing.radii[j]
            larger, smaller = max(r_new, r_old), min(r_new, r_old)
            if calc_overlap_fraction(larger, smaller, d) >= threshold:
                keep[i] = False
                break
    return candidates.subset(keep)
