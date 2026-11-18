"""Circles drawn by hand in Add Individual Colonies, before they are added.

A DrawnCircle is the circle the user controls (move, resize, remove) plus an
optional contour fitted inside it. Both are immutable; edits produce new ones.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from .colony_set import KIND_MANUAL, ColonySet
from .contour import ContourResult, contour_circle

EDGE_BAND = 0.25      # press within 25% of the radius from the edge resizes (min set by the view)


@dataclass(frozen=True)
class DrawnCircle:
    x: float
    y: float
    r: float
    contour: ContourResult | None = None

    def moved_to(self, x: float, y: float) -> "DrawnCircle":
        return replace(self, x=x, y=y, contour=None)

    def resized_to(self, r: float) -> "DrawnCircle":
        return replace(self, r=r, contour=None)

    def with_contour(self, image_rgb: np.ndarray, region_mask: np.ndarray | None = None) -> "DrawnCircle":
        return replace(self, contour=contour_circle(image_rgb, self.x, self.y, self.r, region_mask))

    def without_contour(self) -> "DrawnCircle":
        return replace(self, contour=None)

    @property
    def outline(self) -> np.ndarray | None:
        return self.contour.outline if self.contour is not None else None


def hit_test(circles: list[DrawnCircle], x: float, y: float, edge_tolerance: float) -> tuple[int, str]:
    """Which drawn circle a point falls on, and where: 'edge', 'inside' or '' for none.

    Later circles sit on top, so they are tested first. edge_tolerance is in
    image pixels (the view converts its screen-pixel tolerance).
    """
    for index in range(len(circles) - 1, -1, -1):
        circle = circles[index]
        distance = float(np.hypot(x - circle.x, y - circle.y))
        band = max(edge_tolerance, EDGE_BAND * circle.r)
        if abs(distance - circle.r) <= band:
            return index, "edge"
        if distance < circle.r:
            return index, "inside"
    return -1, ""


def drawn_to_colonies(circles: list[DrawnCircle]) -> ColonySet:
    """Contoured circles become traced colonies; the rest stay circles."""
    if not circles:
        return ColonySet.empty()
    centers, radii, outlines = [], [], []
    for circle in circles:
        if circle.contour is not None and circle.contour.found:
            centers.append(circle.contour.center)
            radii.append(circle.contour.radius)
            outlines.append(circle.contour.outline)
        else:
            centers.append((circle.x, circle.y))
            radii.append(circle.r)
            outlines.append(None)
    return ColonySet.from_columns(centers, radii, boundaries=outlines,
                                  kinds=[KIND_MANUAL] * len(circles))
