"""Contour circle: shrink a hand-drawn circle onto the colony it surrounds.

Method (two-class split):
1. Take the pixels inside the drawn circle.
2. Otsu's method picks the brightness threshold that best separates them
   into two classes, colony and agar.
3. The class containing the circle's center is the colony class, so the
   method works whether colonies are brighter or darker than the agar.
4. Keep the connected region under the center, fill its holes, and trace
   its outline. The region lies inside the circle by construction.

When the circle holds no clear boundary, the result carries a reason and no
outline, and the caller keeps the plain circle.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from skimage import measure
from skimage.filters import threshold_otsu

from .detection import _GRAY_WEIGHTS

MIN_PIXELS = 20            # circles smaller than this cannot be split reliably
# Class means must differ by this much (fraction of full brightness). On the
# bundled plate, colony circles differ by >= 0.225 and empty agar by <= 0.077;
# 0.12 sits in that gap. Low-contrast plates may need a lower value.
MIN_CONTRAST = 0.12
MIN_FILL = 0.05            # region must cover at least 5% of the circle ...
MAX_FILL = 0.97            # ... and less than 97% (otherwise no boundary was found)


@dataclass(frozen=True)
class ContourResult:
    outline: np.ndarray | None       # (M, 2) x/y points, None when no boundary was found
    center: tuple[float, float]      # region centroid (or the circle center on failure)
    radius: float                    # equivalent radius sqrt(area / pi) (or the circle radius)
    reason: str = ""                 # why the contour failed, empty on success

    @property
    def found(self) -> bool:
        return self.outline is not None


def contour_circle(image_rgb: np.ndarray, x: float, y: float, r: float,
                   region_mask: np.ndarray | None = None) -> ContourResult:
    """Fit the colony outline inside the circle centered at (x, y) with radius r.

    Pixels outside region_mask (masked out by cropping) take no part in the
    split, so a circle at a slice edge separates colony from agar, not agar
    from the black mask.
    """
    rows, cols = image_rgb.shape[:2]
    r0, r1 = max(0, int(np.floor(y - r))), min(rows, int(np.ceil(y + r)) + 1)
    c0, c1 = max(0, int(np.floor(x - r))), min(cols, int(np.ceil(x + r)) + 1)
    if r1 - r0 < 3 or c1 - c0 < 3:
        return ContourResult(None, (x, y), r, "circle is outside the image")

    crop = np.round(image_rgb[r0:r1, c0:c1, :3].astype(float) @ _GRAY_WEIGHTS) / 255.0
    yy, xx = np.mgrid[r0:r1, c0:c1]
    inside = (xx - x) ** 2 + (yy - y) ** 2 <= r ** 2
    if region_mask is not None:
        inside &= region_mask[r0:r1, c0:c1]
    values = crop[inside]
    if values.size < MIN_PIXELS:
        return ContourResult(None, (x, y), r, "circle is too small to contour")

    # Two-class split inside the circle
    if values.max() - values.min() < MIN_CONTRAST:
        return ContourResult(None, (x, y), r, "no brightness difference inside the circle")
    threshold = threshold_otsu(values)
    high, low = values[values > threshold], values[values <= threshold]
    if high.size == 0 or low.size == 0 or high.mean() - low.mean() < MIN_CONTRAST:
        return ContourResult(None, (x, y), r, "no clear boundary inside the circle")

    # Colony class = the class at the circle center (3 x 3 neighborhood vote)
    cy, cx = int(round(y)) - r0, int(round(x)) - c0
    patch = crop[max(0, cy - 1):cy + 2, max(0, cx - 1):cx + 2]
    colony_is_bright = np.mean(patch > threshold) >= 0.5
    colony = ((crop > threshold) if colony_is_bright else (crop <= threshold)) & inside

    # Region under the center (or the region nearest to it)
    labels, count = ndimage.label(colony, structure=np.ones((3, 3)))
    if count == 0:
        return ContourResult(None, (x, y), r, "no colony pixels at the circle center")
    label_at_center = labels[min(max(cy, 0), labels.shape[0] - 1), min(max(cx, 0), labels.shape[1] - 1)]
    if label_at_center == 0:
        distance = np.where(labels > 0, np.hypot(yy - y, xx - x), np.inf)
        label_at_center = labels.flat[int(np.argmin(distance))]
    region = ndimage.binary_fill_holes(labels == label_at_center)

    fill = region.sum() / inside.sum()
    if fill < MIN_FILL:
        return ContourResult(None, (x, y), r, "the colony region is too small; center the circle on the colony")
    if fill > MAX_FILL:
        return ContourResult(None, (x, y), r, "no boundary inside the circle; draw a larger circle")

    # Outline in image coordinates (same convention as automatic detection)
    contours = measure.find_contours(np.pad(region, 1).astype(float), 0.5)
    outline = max(contours, key=len)
    outline_xy = np.column_stack([outline[:, 1] + c0 - 1, outline[:, 0] + r0 - 1])
    region_rows, region_cols = np.nonzero(region)
    center = (float(region_cols.mean() + c0), float(region_rows.mean() + r0))
    area = float(region.sum())
    return ContourResult(outline_xy, center, float(np.sqrt(area / np.pi)))
