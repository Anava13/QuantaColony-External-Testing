"""Statistics library (Qt-free): the tables analyses read, and the math they share.

    ColonyTable   every colony of every detected index, with its levels and scale
    IndexTable    one row per detected index, with plate-level measures (colony count,
                  coverage, ...)
    math          Freedman-Diaconis bandwidth, Gaussian KDE, summary statistics,
                  measurement-fidelity zones

How variables are grouped for a plot is the analysis's choice (see quantacolony.analyses).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

LINE_STATISTICS = ("Mean", "Median", "Standard Deviation", "Coefficient Of Variation", "Skew", "Kurtosis",
                   "Peak Location", "Peak Density")
ZONE_EDGES = (0.0, 5.0, 10.0, 25.0, 50.0, np.inf)        # percent measurement uncertainty
ZONE_LABELS = ("high fidelity (0-5%)", "good fidelity (5-10%)", "moderate fidelity (10-25%)",
               "low fidelity (25-50%)", "sub-pixel (50%+)")
NORMALIZED_BANDWIDTH_FLOOR = 0.05
RESOLUTION_PX = 1.0             # KDE bandwidth floor for raw sizes: one pixel, in each index's own µm
LOW_COUNT, HIGH_UNCERTAINTY = 20, 25.0


# =========================================================================
# inputs
# =========================================================================
@dataclass(frozen=True)
class StatsVariable:
    name: str
    display_name: str
    kind: str                                  # 'nominal', 'ordinal', 'replicate'
    values: tuple[str, ...]
    displays: tuple[str, ...]
    control: str | None = None                 # value of the control level, if one is marked

    def display(self, value: str) -> str:
        return self.displays[self.values.index(value)] if value in self.values else value


@dataclass(frozen=True)
class ColonyTable:
    """Every colony of every detected index, one array entry per colony."""

    variables: tuple[StatsVariable, ...]
    index_ids: np.ndarray                      # (n,) index of each colony
    levels: dict                               # variable name -> (n,) level values (object arrays)
    radius_px: np.ndarray
    radius_um: np.ndarray                      # NaN for colonies on a photo without a scale
    um_per_px: np.ndarray                      # scale of each colony's photo (NaN: no scale)
    touching: np.ndarray | None = None         # True for colonies split from a blob of touching colonies

    def __len__(self) -> int:
        return len(self.radius_px)


def build_colony_table(variables, indices: list[tuple[int, dict, float | None, list[dict]]]) -> ColonyTable:
    """indices: (index id, {variable: value}, px_per_mm or None without a scale, colony measurement rows)
    for every detected index."""
    ids, px, um, scale, touching = [], [], [], [], []
    levels = {v.name: [] for v in variables}
    for index_id, labels, px_per_mm, rows in indices:
        for row in rows:
            ids.append(index_id)
            px.append(float(row["radius_px"]))
            um.append(np.nan if row.get("radius_um") is None or not px_per_mm else float(row["radius_um"]))
            scale.append(1000.0 / px_per_mm if px_per_mm else np.nan)
            touching.append(row.get("source") == "split")
            for v in variables:
                levels[v.name].append(labels.get(v.name))
    return ColonyTable(tuple(variables), np.asarray(ids, dtype=int),
                       {k: np.asarray(v, dtype=object) for k, v in levels.items()},
                       np.asarray(px, dtype=float), np.asarray(um, dtype=float), np.asarray(scale, dtype=float),
                       np.asarray(touching, dtype=bool))


@dataclass(frozen=True)
class IndexTable:
    """One row per detected index: its levels, scale and plate-level measures."""

    variables: tuple[StatsVariable, ...]
    index_ids: np.ndarray
    levels: dict                               # variable name -> (n,) level values
    um_per_px: np.ndarray                      # NaN for an index on a photo without a scale
    measures: dict                             # measure id -> (n,) values (NaN where not measured)
    measure_labels: dict                       # measure id -> axis label
    pixel_measures: dict = field(default_factory=dict)   # micrometer measure id -> (n,) same in pixels

    def __len__(self) -> int:
        return len(self.index_ids)


def build_index_table(variables, rows: list[tuple[int, dict, float | None, dict]], labels: dict) -> IndexTable:
    """rows: (index id, {variable: value}, px_per_mm or None, {measure id: value}) per detected index.
    A micrometer measure ('..._um') with a '..._px' twin in the rows gets that twin as its pixel measure."""
    measures = {key: np.asarray([r[3].get(key, np.nan) for r in rows], dtype=float) for key in labels}
    pixels = {key: np.asarray([r[3].get(key[:-3] + "_px", np.nan) for r in rows], dtype=float)
              for key in labels if key.endswith("_um") and rows and all(key[:-3] + "_px" in r[3] for r in rows)}
    return IndexTable(tuple(variables), np.asarray([r[0] for r in rows], dtype=int),
                      {v.name: np.asarray([r[1].get(v.name) for r in rows], dtype=object) for v in variables},
                      np.asarray([1000.0 / r[2] if r[2] else np.nan for r in rows], dtype=float), measures,
                      dict(labels), pixels)


# =========================================================================
# density and summary statistics
# =========================================================================
def bandwidth(values: np.ndarray, floor: float) -> float:
    """Freedman-Diaconis bandwidth, never below the floor (calculateKDE)."""
    q75, q25 = np.percentile(values, [75, 25])
    return max(2 * (q75 - q25) / np.cbrt(len(values)), floor)


def kde(values: np.ndarray, grid: np.ndarray, h: float) -> np.ndarray:
    """Gaussian kernel density at the grid points (ksdensity with a fixed bandwidth)."""
    z = (grid[:, None] - values[None, :]) / h
    return np.exp(-0.5 * z * z).sum(axis=1) / (len(values) * h * np.sqrt(2 * np.pi))


def bandwidth_floor(um_per_px: np.ndarray, normalized: bool) -> float:
    """Raw sizes: one pixel in µm at the group's own scale (median over its rows). Normalized: 0.05."""
    if normalized:
        return NORMALIZED_BANDWIDTH_FLOOR
    return RESOLUTION_PX * float(np.median(um_per_px)) if um_per_px.size else 0.0


def summary_values(values: np.ndarray) -> dict:
    """fastStatistics: sample standard deviation; skew and kurtosis from the moments (excess kurtosis)."""
    n = len(values)
    mean = float(np.mean(values))
    d = values - mean
    variance = float(np.sum(d * d) / (n - 1)) if n > 1 else 0.0
    sd = np.sqrt(variance)
    skew = float(np.sum(d ** 3) / n / sd ** 3) if sd > 0 and n > 2 else 0.0
    kurt = float(np.sum(d ** 4) / n / variance ** 2 - 3) if sd > 0 and n > 2 else 0.0
    return {"Mean": mean, "Median": float(np.median(values)), "Standard Deviation": float(sd),
            "Coefficient Of Variation": float(sd / mean) if mean else np.nan, "Skew": skew, "Kurtosis": kurt}


# =========================================================================
# measurement fidelity (pixel space, never normalized)
# =========================================================================
def zone_of(value: float) -> int:
    for z in range(len(ZONE_EDGES) - 1):
        if ZONE_EDGES[z] <= value < ZONE_EDGES[z + 1]:
            return z
    return len(ZONE_EDGES) - 2


def fidelity_conclusion(counts, pct, total, medians, grid_counts, rows, columns) -> tuple[str, ...]:
    if not total:
        return ("No colonies with a measured radius yet.",)
    dominant = int(np.argmax(pct))
    lines = [f"Bulk assessment: {pct[dominant]:.1f}% of colonies ({int(total)} total) fall in the "
             f"{ZONE_LABELS[dominant]} zone."]
    problematic = pct[3:].sum()
    if problematic < 0.1:
        lines.append("Tail assessment: every colony has less than 25% measurement uncertainty.")
    else:
        lines.append(f"Tail assessment: {int(counts[3:].sum())} colonies ({problematic:.1f}%) exceed 25% "
                     "measurement uncertainty.")
    lines.append({0: "Population-level and individual colony-to-colony comparisons are well supported at this "
                     "precision.",
                  1: "Population-level and individual colony-to-colony comparisons are well supported at this "
                     "precision.",
                  2: "Population-level analyses (KDE, mixture modeling, group comparisons) are robust. Individual "
                     "colony size differences below ~20% are not resolvable."}.get(
        dominant, "Population-level distributional features should be interpreted with caution. Individual colony "
                  "size comparisons are unreliable."))
    low = [f"{rows[r]} / {columns[c]} (n={grid_counts[r, c]})" for r in range(len(rows)) for c in range(len(columns))
           if 0 < grid_counts[r, c] < LOW_COUNT]
    high = [f"{rows[r]} / {columns[c]} ({medians[r, c]:.1f}%)" for r in range(len(rows)) for c in range(len(columns))
            if np.isfinite(medians[r, c]) and medians[r, c] >= HIGH_UNCERTAINTY]
    if low:
        lines.append(f"Low colony count: {', '.join(low)}.")
    if high:
        lines.append(f"High median uncertainty: {', '.join(high)}.")
    return tuple(lines)
