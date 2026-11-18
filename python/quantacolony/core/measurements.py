"""Per-colony measurements for one image. Writing them to files is the DataManager's job."""

from __future__ import annotations

import numpy as np

from .colony_set import ColonySet

COLUMNS = ["colony_id", "x_px", "y_px", "radius_px", "radius_um", "area_um2",
           "circularity", "eccentricity", "source"]


def measure_colonies(colonies: ColonySet, px_per_mm: float | None) -> list[dict]:
    """One row per colony. Circularity = 4*pi*area / perimeter^2 of the outline (1 for a circle).
    px_per_mm None (a photo without a scale): radius_um and area_um2 are None."""
    um_per_px = 1000.0 / px_per_mm if px_per_mm else None
    rows = []
    for i in range(len(colonies)):
        radius = float(colonies.radii[i])
        outline = colonies.boundaries[i]
        if outline is not None and len(outline) > 2:
            closed = np.vstack([outline, outline[:1]])
            perimeter = float(np.sum(np.hypot(*np.diff(closed, axis=0).T)))
            area_px = np.pi * radius ** 2
            circularity = 4 * np.pi * area_px / perimeter ** 2 if perimeter > 0 else np.nan
        else:
            circularity = 1.0
        rows.append({
            "colony_id": int(colonies.ids[i]),
            "x_px": round(float(colonies.centers[i, 0]), 3),
            "y_px": round(float(colonies.centers[i, 1]), 3),
            "radius_px": round(radius, 4),
            "radius_um": round(radius * um_per_px, 3) if um_per_px else None,
            "area_um2": round(np.pi * (radius * um_per_px) ** 2, 1) if um_per_px else None,
            "circularity": round(float(circularity), 4),
            "eccentricity": (round(float(colonies.eccentricities[i]), 4)
                             if not np.isnan(colonies.eccentricities[i]) else ""),
            "source": colonies.kinds[i],
        })
    return rows


def summarize_colonies(colonies: ColonySet, px_per_mm: float | None) -> dict:
    """Counts by origin and radius statistics for the summary card: in micrometers, or in pixels
    (unit 'px', um_per_px None) for a photo without a scale. The *_um keys hold the values either way."""
    um_per_px = 1000.0 / px_per_mm if px_per_mm else None
    added = sum(1 for kind in colonies.kinds if kind == "manual")
    radii = colonies.radii * (um_per_px or 1.0)
    summary = {"total": len(colonies), "automatic": len(colonies) - added, "added": added,
               "um_per_px": um_per_px, "unit": "µm" if um_per_px else "px",
               "mean_um": None, "median_um": None, "min_um": None, "max_um": None}
    if len(colonies):
        summary.update(mean_um=float(np.mean(radii)), median_um=float(np.median(radii)),
                       min_um=float(np.min(radii)), max_um=float(np.max(radii)))
    return summary
