"""Do the mixture-component trajectories depend on which plate the selection returned? (Qt-free)

The faded plates are the plates of each genotype and passage that the selection did not choose. Each is
compared with the selected plate of its own cell and with the selected plates of every other cell.

    distance      from a plate to a reference plate: each of its components is matched to the nearest
                  component of the reference in log radius, the ratio of the two radii (>= 1) is taken,
                  and the ratios are averaged with the plate's component weights
    own / other   distance to the selected plate of its own cell / mean distance to the selected plates
                  of the other cells
    closer        own < other, counted per genotype
    permutation   the selected plates reassigned among the cells at random (2000 times, seed 0); the
                  median own distance of each reassignment against the true one
    size          Pearson r between the weighted mean component radius of each faded plate and that of
                  its selected plate
    major side    on faded plates with two components: whether the major (heavier) component is the
                  lower or the upper one, as on the selected plate (when the selected plate has one
                  component, its side is the faded component nearer to it); one-sided binomial test
                  against 1/2
    crowding      Spearman correlation of own distance with the colony-count difference from the
                  selected plate
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PlateComponents:
    index_id: int
    cell: tuple                                   # (genotype, passage)
    genotype: str
    count: int
    medians: np.ndarray                           # µm, sorted
    weights: np.ndarray
    selected: bool


def distance(plate: PlateComponents, reference: PlateComponents) -> float:
    if not plate.medians.size or not reference.medians.size:
        return float("nan")
    lp, lr = np.log(plate.medians), np.log(reference.medians)
    nearest = np.min(np.abs(lp[:, None] - lr[None, :]), axis=1)
    return float(np.sum(plate.weights * np.exp(nearest)) / np.sum(plate.weights))


def weighted_mean_radius(plate: PlateComponents) -> float:
    return float(np.sum(plate.weights * plate.medians) / np.sum(plate.weights))


def _major_side(medians: np.ndarray, weights: np.ndarray) -> str:
    return "upper" if int(np.argmax(weights)) == len(medians) - 1 else "lower"


def run(plates: list[PlateComponents], permutations: int = 2000, seed: int = 0) -> dict:
    from scipy import stats
    selected = {p.cell: p for p in plates if p.selected}
    faded = [p for p in plates if not p.selected and p.cell in selected and p.medians.size]
    cells = list(selected)
    own = np.array([distance(p, selected[p.cell]) for p in faded])
    other = np.array([np.mean([distance(p, selected[c]) for c in cells if c != p.cell]) for p in faded])
    closer = {}
    for p, o, t in zip(faded, own, other):
        hit, total = closer.get(p.genotype, (0, 0))
        closer[p.genotype] = (hit + int(o < t), total + 1)
    rng = np.random.default_rng(seed)
    observed = float(np.median(own))
    medians = []
    picks = [selected[c] for c in cells]
    for _ in range(permutations):
        order = rng.permutation(len(cells))
        shuffled = {cells[i]: picks[order[i]] for i in range(len(cells))}
        medians.append(float(np.median([distance(p, shuffled[p.cell]) for p in faded])))
    medians = np.array(medians)
    r = float(np.corrcoef([weighted_mean_radius(p) for p in faded],
                          [weighted_mean_radius(selected[p.cell]) for p in faded])[0, 1])
    same = total_two = 0
    for p in faded:
        if p.medians.size != 2:
            continue
        reference = selected[p.cell]
        if reference.medians.size >= 2:
            want = _major_side(reference.medians, reference.weights)
        else:
            near = int(np.argmin(np.abs(np.log(p.medians) - np.log(reference.medians[0]))))
            want = "upper" if near == 1 else "lower"
        total_two += 1
        same += int(_major_side(p.medians, p.weights) == want)
    binomial = float(stats.binomtest(same, total_two, 0.5, alternative="greater").pvalue) if total_two else float("nan")
    count_gap = np.array([abs(p.count - selected[p.cell].count) for p in faded])
    rho, rho_p = stats.spearmanr(own, count_gap)
    return {"faded": len(faded), "own_median": observed, "other_median": float(np.median(other)),
            "closer": sum(h for h, _ in closer.values()), "closer_by_genotype": closer,
            "permutation_range": (float(medians.min()), float(medians.max())),
            "permutation_as_close": int(np.sum(medians <= observed)), "permutations": permutations,
            "r_size": r, "two_component": total_two, "same_side": same, "binomial_p": binomial,
            "spearman": float(rho), "spearman_p": float(rho_p)}
