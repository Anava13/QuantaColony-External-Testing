"""Scoring automatic detection against a corrected result (Qt-free).

A corrected result (the colonies a person kept, deleted and added in Colony Detection) is the
reference. Automatic detection is run again with the image's settings, and each detected
colony is matched to at most one reference colony:

    a detected colony matches a reference colony when its center lies within
    MATCH_TOLERANCE x the reference colony's radius of the reference center;
    pairs are taken closest first, each colony used once.

    matched    detections that correspond to a reference colony
    missed     reference colonies no detection matched (a person had to add them)
    extra      detections that match nothing (a person had to delete them)
    corrections = missed + extra: the edits automatic detection leaves for a person

A merged pair detected as one colony has its center between the two, so it usually counts as one
extra and two missed: three corrections, as it costs a person in Colony Detection.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .colony_set import ColonySet

MATCH_TOLERANCE = 0.5


@dataclass(frozen=True)
class Score:
    reference: int
    detected: int
    matched: int

    @property
    def missed(self) -> int:
        return self.reference - self.matched

    @property
    def extra(self) -> int:
        return self.detected - self.matched

    @property
    def corrections(self) -> int:
        return self.missed + self.extra

    @property
    def recall(self) -> float:
        return self.matched / self.reference if self.reference else np.nan

    @property
    def precision(self) -> float:
        return self.matched / self.detected if self.detected else np.nan

    @property
    def f1(self) -> float:
        total = self.reference + self.detected
        return 2 * self.matched / total if total else np.nan

    def __add__(self, other: "Score") -> "Score":
        return Score(self.reference + other.reference, self.detected + other.detected, self.matched + other.matched)


def match(detected: ColonySet, reference: ColonySet, tolerance: float = MATCH_TOLERANCE) -> list[tuple[int, int]]:
    """(detected row, reference row) pairs, closest first, each colony used once."""
    if not len(detected) or not len(reference):
        return []
    d = detected.centers[:, None, :] - reference.centers[None, :, :]
    distance = np.hypot(d[..., 0], d[..., 1])
    allowed = distance <= tolerance * np.maximum(reference.radii[None, :], 1.0)
    rows, cols = np.nonzero(allowed)
    order = np.argsort(distance[rows, cols], kind="stable")
    used_d, used_r, pairs = set(), set(), []
    for k in order:
        i, j = int(rows[k]), int(cols[k])
        if i not in used_d and j not in used_r:
            used_d.add(i)
            used_r.add(j)
            pairs.append((i, j))
    return pairs


def score(detected: ColonySet, reference: ColonySet, tolerance: float = MATCH_TOLERANCE) -> Score:
    return Score(len(reference), len(detected), len(match(detected, reference, tolerance)))
