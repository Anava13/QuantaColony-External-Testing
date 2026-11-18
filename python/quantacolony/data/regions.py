"""Plate regions: shape parameters, their outline, and the crop and mask they produce.

A region is stored as its parameters (geometry, for exact editing in the
cropping module) and as an outline polygon in photo pixels (one shape type
for everything else). The crop is the outline's bounding box; pixels
outside the outline become black, and the mask records which pixels belong
to the index.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from skimage.draw import polygon2mask

RECTANGLE, SECTOR, POLYGON, FREEHAND = "rectangle", "sector", "polygon", "freehand"
REGION_KINDS = (RECTANGLE, SECTOR, POLYGON, FREEHAND)
ARC_STEP_DEG = 1.0          # outline resolution along a sector's arc


@dataclass(frozen=True)
class Crop:
    image: np.ndarray        # (rows, cols, 3) uint8, black outside the region
    mask: np.ndarray         # (rows, cols) bool, True inside the region
    offset: tuple[int, int]  # photo pixel (x, y) of the crop's top-left pixel


def outline_from_geometry(kind: str, geometry: dict) -> np.ndarray:
    """Polygon (M, 2) x/y in photo pixels.

    rectangle: x, y, width, height (pixel-center coordinates of the corner, size in pixels)
    sector:    cx, cy, radius, start_deg, end_deg (degrees clockwise from +x, image y points down);
               a span of 360 degrees is the whole dish
    polygon / freehand: points, a list of [x, y]
    """
    if kind == RECTANGLE:
        x, y, w, h = (float(geometry[k]) for k in ("x", "y", "width", "height"))
        return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])
    if kind == SECTOR:
        cx, cy, radius = float(geometry["cx"]), float(geometry["cy"]), float(geometry["radius"])
        start, end = float(geometry["start_deg"]), float(geometry["end_deg"])
        span = (end - start) % 360 or 360.0
        steps = max(2, int(np.ceil(span / ARC_STEP_DEG)) + 1)
        angles = np.deg2rad(np.linspace(start, start + span, steps))
        arc = np.column_stack([cx + radius * np.cos(angles), cy + radius * np.sin(angles)])
        return arc if span >= 360 else np.vstack([[cx, cy], arc])
    if kind in (POLYGON, FREEHAND):
        points = np.asarray(geometry["points"], dtype=float).reshape(-1, 2)
        if len(points) < 3:
            raise ValueError("A polygon region needs at least 3 points")
        return points
    raise ValueError(f"Unknown region kind: {kind}")


def crop_box(outline: np.ndarray, photo_shape: tuple[int, ...]) -> tuple[int, int, int, int]:
    """Bounding box (x0, y0, x1, y1), end-exclusive, clipped to the photo: the pixels
    the outline overlaps (pixel c spans c - 0.5 to c + 0.5)."""
    rows, cols = photo_shape[:2]
    x0 = max(0, int(np.floor(outline[:, 0].min() + 0.5)))
    y0 = max(0, int(np.floor(outline[:, 1].min() + 0.5)))
    x1 = min(cols, int(np.ceil(outline[:, 0].max() + 0.5)))
    y1 = min(rows, int(np.ceil(outline[:, 1].max() + 0.5)))
    if x1 <= x0 or y1 <= y0:
        raise ValueError("The region lies outside the photo")
    return x0, y0, x1, y1


def cut_region(photo: np.ndarray, outline: np.ndarray) -> Crop:
    """Crop the photo to the region and black out the pixels outside it."""
    x0, y0, x1, y1 = crop_box(outline, photo.shape)
    local = outline - np.array([x0, y0])
    # polygon2mask takes (row, col) vertices; a pixel is inside when its center is
    mask = polygon2mask((y1 - y0, x1 - x0), local[:, ::-1])
    image = photo[y0:y1, x0:x1, :3].copy()
    image[~mask] = 0
    return Crop(image, mask, (x0, y0))
