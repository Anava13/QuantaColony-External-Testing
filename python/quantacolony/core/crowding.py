"""The per-colony crowding score (Qt-free): how much competitor growth surrounds each colony.

A port of the MATLAB QuantaColony crowding score (calculateEligiblePointsMask, calculateOccupationMask,
calculateFinalScoreMask). Each colony's zone of competition starts at its boundary and reaches three
radii out. Every pixel of the zone carries the weight 1 / (1 + (d / r)^2), with d its distance from the
colony boundary and r the colony radius, so the weight is 1 at the boundary, 1/2 one radius out and
1/10 at three radii. The score is the weighted share of the zone that competitor growth covers, as a
percentage (0 to 100):

    score = 100 * sum(weight * competitor) / sum(weight)

Competitor growth is the plate's growth mask (core.growth) with the focal colony removed, so a colony is
never its own competitor. Pixels inside the colony carry no weight. Distances come from the colony's
traced outline where it has one and from its circle otherwise; the zone is computed in a box of
8 radii around the center, as in MATLAB.

MATLAB built the occupation mask by inverting the competitor mask (1 = no growth) and kept the colony's
own interior at weight 1 for circular colonies. The score here follows the definition in the dissertation
text (the share of the zone that competitor growth covers, the focal colony excluded). The two differ by
a constant and by the interior term, which leaves the within-state fit's R² unchanged in sign and near
zero either way; this module is the definition reported.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage
from skimage import draw

from .colony_set import ColonySet

ZONE_RADII = 3.0                                 # the zone reaches three radii past the boundary
BOX_RADII = 4.0                                  # half-width of the box the zone is computed in


def colony_scores(growth: np.ndarray, colonies: ColonySet) -> np.ndarray:
    """One crowding score (0 to 100, NaN when the zone is empty) per colony.

    growth: (rows, cols) bool growth mask in the colonies' coordinates."""
    growth = np.asarray(growth, dtype=bool)
    rows, cols = growth.shape
    scores = np.full(len(colonies), np.nan)
    for i in range(len(colonies)):
        x, y = (float(v) for v in colonies.centers[i])
        r = max(float(colonies.radii[i]), 0.5)
        half = BOX_RADII * r
        r0, r1 = max(0, int(np.floor(y - half))), min(rows, int(np.ceil(y + half)) + 1)
        c0, c1 = max(0, int(np.floor(x - half))), min(cols, int(np.ceil(x + half)) + 1)
        if r1 <= r0 or c1 <= c0:
            continue
        shape = (r1 - r0, c1 - c0)
        inside = np.zeros(shape, dtype=bool)
        outline = colonies.boundaries[i]
        if outline is not None and len(outline) > 2:
            rr, cc = draw.polygon(outline[:, 1] - r0, outline[:, 0] - c0, shape)
            inside[rr, cc] = True
            distance = ndimage.distance_transform_edt(~inside)            # 0 inside, distance to it outside
        else:
            yy, xx = np.mgrid[r0:r1, c0:c1]
            to_center = np.hypot(xx - x, yy - y)
            inside = to_center <= r
            distance = np.where(inside, 0.0, to_center - r)
        zone = (~inside) & (distance <= ZONE_RADII * r)
        weight = np.where(zone, 1.0 / (1.0 + (distance / r) ** 2), 0.0)
        total = weight.sum()
        if total <= 0:
            continue
        competitor = growth[r0:r1, c0:c1] & ~inside
        scores[i] = 100.0 * float((weight * competitor).sum()) / float(total)
    return scores
