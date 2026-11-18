"""The within-state test of crowding predictors (Qt-free), pooled across projects.

A biological state is one combination of design levels (genotype and passage in the Rescue Dataset; the
whole Crowding Dataset is one state). Within each state every candidate predictor and the response are
centered on the state's own mean, so only plating varies; the centered values of every state are pooled
and fit with one line through the origin (both axes are centered). R² is the share of the within-state
variation in the response that the predictor accounts for.

    Plate-level predictors (one point per plate, response: log10 median radius):
        HCE (log10), colony count (log10), growth coverage (fraction)
    Colony-level predictor (one point per colony, response: log10 colony radius):
        crowding score (fraction of the zone, 0 to 1)

    commonality    the joint fit of colony count and growth coverage, split into the part each
                   predictor carries alone and the part they share
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SEPARABLE = 0.25


@dataclass
class Plate:
    """One plate of the test."""

    project: str
    state: str                                    # the biological state (project and its levels)
    label: str
    hce: float
    count: float
    growth: float                                 # growth coverage, fraction of the growable area
    capture: float
    median_radius: float                          # µm
    radii: np.ndarray = field(default_factory=lambda: np.zeros(0))       # µm, one per colony
    crowding: np.ndarray = field(default_factory=lambda: np.zeros(0))    # 0 to 100, one per colony


@dataclass(frozen=True)
class Fit:
    name: str
    level: str                                    # 'plate' or 'colony'
    n: int
    slope: float
    r2: float
    x: np.ndarray
    y: np.ndarray


def _center(values: np.ndarray, states: np.ndarray) -> np.ndarray:
    out = np.full(values.shape, np.nan)
    for state in np.unique(states):
        rows = states == state
        out[rows] = values[rows] - np.mean(values[rows])
    return out


def origin_fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Slope and R² of y = b x through the origin, on centered data (R² against the centered total)."""
    keep = np.isfinite(x) & np.isfinite(y)
    x, y = x[keep], y[keep]
    if x.size < 3 or not np.any(x):
        return float("nan"), float("nan")
    slope = float(np.dot(x, y) / np.dot(x, x))
    total = float(np.dot(y, y))
    return slope, (1.0 - float(np.sum((y - slope * x) ** 2)) / total) if total > 0 else float("nan")


def joint_r2(x1: np.ndarray, x2: np.ndarray, y: np.ndarray) -> float:
    keep = np.isfinite(x1) & np.isfinite(x2) & np.isfinite(y)
    design = np.column_stack([x1[keep], x2[keep]])
    coef, *_ = np.linalg.lstsq(design, y[keep], rcond=None)
    total = float(np.dot(y[keep], y[keep]))
    return 1.0 - float(np.sum((y[keep] - design @ coef) ** 2)) / total


def eligible(plates: list[Plate]) -> list[Plate]:
    """Plates in the separable range, with colonies, in states holding at least two such plates."""
    kept = [p for p in plates if np.isfinite(p.capture) and p.capture >= SEPARABLE and p.count >= 1
            and np.isfinite(p.median_radius) and p.median_radius > 0]
    states = {}
    for p in kept:
        states.setdefault(p.state, []).append(p)
    return [p for p in kept if len(states[p.state]) >= 2]


def run(plates: list[Plate]) -> dict:
    """Every fit of the test, the commonality split, and the counts behind them."""
    used = eligible(plates)
    states = np.array([p.state for p in used])
    y = _center(np.log10([p.median_radius for p in used]), states)
    columns = {"HCE": np.log10([p.hce if p.hce and p.hce > 0 else np.nan for p in used]),
               "Colony count": np.log10([p.count for p in used]),
               "Growth coverage": np.array([p.growth for p in used], float)}
    fits = {}
    for name, raw in columns.items():
        x = _center(raw, states)
        slope, r2 = origin_fit(x, y)
        fits[name] = Fit(name, "plate", int(np.sum(np.isfinite(x) & np.isfinite(y))), slope, r2, x, y)
    # colony level: crowding score against each colony's log radius, centered within state
    radius, score, state_of = [], [], []
    for p in used:
        if p.crowding.size and p.crowding.size == p.radii.size:
            keep = np.isfinite(p.crowding) & (p.radii > 0)
            radius.append(np.log10(p.radii[keep]))
            score.append(p.crowding[keep] / 100.0)
            state_of += [p.state] * int(keep.sum())
    if radius:
        state_of = np.array(state_of)
        cx = _center(np.concatenate(score), state_of)
        cy = _center(np.concatenate(radius), state_of)
        slope, r2 = origin_fit(cx, cy)
        fits["Crowding score"] = Fit("Crowding score", "colony", int(cx.size), slope, r2, cx, cy)
    count, growth = fits["Colony count"], fits["Growth coverage"]
    both = joint_r2(count.x, growth.x, y)
    commonality = {"Colony count alone": count.r2, "Growth coverage alone": growth.r2,
                   "Colony count and growth coverage together": both,
                   "Colony count, beyond growth coverage": both - growth.r2,
                   "Growth coverage, beyond colony count": both - count.r2,
                   "Shared between the two": count.r2 + growth.r2 - both}
    by_project = {}
    for p in used:
        by_project[p.project] = by_project.get(p.project, 0) + 1
    return {"fits": fits, "commonality": commonality, "plates": len(used), "states": len(set(states.tolist())),
            "plate_states": states.tolist(), "plate_projects": [p.project for p in used],
            "colony_states": list(state_of) if radius else [],
            "by_project": by_project, "colonies": fits["Crowding score"].n if "Crowding score" in fits else 0,
            "all_plates": {proj: sum(1 for p in plates if p.project == proj) for proj in {p.project for p in plates}}}
