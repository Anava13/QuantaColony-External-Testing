"""Scoring a participant's outlines against a synthetic plate's truth (Qt-free): the training check on the
practice plate, with the same rules as tools' score_quantacolony.py.

Required and optional colonies (what a trained user measures)
    required    a colony in a mass of 1 to 4 touching colonies, with at least half its own area visible
    optional    a colony in a mass of 5 or more, or less than half visible; skipping it costs nothing,
                and an outline on it counts as correct when it is right

Each outline is then
    correct     it belongs to one true colony (the colony under its center, else the one with most
                pixels inside it), covers less than half of every other colony, and is that colony's
                only outline
    merged      it covers at least half of two or more colonies
    split       its colony has two or more outlines
    false       it covers no colony; named by the object under it (marker, bubble, condensation,
                rim_shadow, debris) or "agar"

    found       share of required colonies with a correct outline
    correct     share of outlines that are correct
    passed      found >= FOUND_MIN and correct >= CORRECT_MIN

    required_colonies   the required/optional rule on truth rows (group_size, true and uncut areas)
    score_plate         the score of a set of outlines (photo pixels) against labels and object kinds
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from skimage.draw import disk, polygon

MASS_LIMIT = 5               # masses of this many colonies or more are optional
VISIBLE_MIN = 0.5            # colonies with less of their own area visible are optional
FOUND_MIN = 0.90             # training: share of required colonies found
CORRECT_MIN = 0.95           # training: share of outlines correct
KIND_NAMES = {1: "marker", 2: "bubble", 3: "condensation", 4: "rim_shadow", 5: "debris"}


def required_colonies(rows) -> dict:
    """{colony id: True when required} from truth rows (synthetic truth table columns)."""
    out = {}
    for r in rows:
        size = int(r["group_size"])
        uncut = float(r["uncut_area_mm2"]) or 1e-12
        out[int(r["colony_id"])] = size < MASS_LIMIT and float(r["true_area_mm2"]) / uncut >= VISIBLE_MIN
    return out


@dataclass
class PlateScore:
    required_total: int
    required_found: int
    outlines: int
    correct: int
    verdicts: list                         # one per outline: correct, merged, split, false:<kind>
    missed: list                           # required colony ids without a correct outline
    false_kinds: dict = field(default_factory=dict)

    @property
    def found_share(self) -> float:
        return self.required_found / self.required_total if self.required_total else 1.0

    @property
    def correct_share(self) -> float:
        return self.correct / self.outlines if self.outlines else 0.0

    @property
    def passed(self) -> bool:
        return self.found_share >= FOUND_MIN and self.correct_share >= CORRECT_MIN


def outline_pixels(boundary, center, radius, shape):
    if boundary is not None and len(boundary) > 2:
        b = np.asarray(boundary)
        return polygon(b[:, 1], b[:, 0], shape)
    return disk((center[1], center[0]), max(radius, 0.5), shape=shape)


def score_plate(colonies, labels: np.ndarray, kinds: np.ndarray | None, required: dict) -> PlateScore:
    """colonies: a ColonySet in photo pixels. labels: true colony ids (0 = none). kinds: object codes."""
    true_area = np.bincount(labels.ravel(), minlength=int(labels.max()) + 1)
    owners, merged, false_kind = [], [], []
    for k in range(len(colonies)):
        center = colonies.centers[k]
        rr, cc = outline_pixels(colonies.boundaries[k], center, colonies.radii[k], labels.shape)
        ids, counts = np.unique(labels[rr, cc], return_counts=True)
        counts = np.where(ids > 0, counts, 0)
        covered = [i for i, n in zip(ids, counts) if i > 0 and n >= 0.5 * true_area[i]]
        merged.append(len(covered) > 1)
        cy = min(max(int(round(center[1])), 0), labels.shape[0] - 1)
        cx = min(max(int(round(center[0])), 0), labels.shape[1] - 1)
        owner = int(labels[cy, cx])
        if owner == 0 and counts.max(initial=0) > 0:
            owner = int(ids[np.argmax(counts)])
        owners.append(owner)
        name = "agar"
        if owner == 0 and kinds is not None:
            code = int(kinds[cy, cx])
            if code == 0 and len(rr):
                tally = np.bincount(kinds[rr, cc], minlength=6)
                tally[0] = 0
                code = int(tally.argmax()) if tally.max() > 0 else 0
            name = KIND_NAMES.get(code, "agar")
        false_kind.append(name)
    belonging: dict[int, list] = {}
    for k, owner in enumerate(owners):
        if owner > 0:
            belonging.setdefault(owner, []).append(k)
    verdicts, false_kinds = [], {}
    for k, owner in enumerate(owners):
        if owner == 0:
            verdicts.append(f"false:{false_kind[k]}")
            false_kinds[false_kind[k]] = false_kinds.get(false_kind[k], 0) + 1
        elif merged[k]:
            verdicts.append("merged")
        elif len(belonging[owner]) > 1:
            verdicts.append("split")
        else:
            verdicts.append("correct")
    found = [i for i, need in required.items() if need and len(belonging.get(i, [])) == 1
             and verdicts[belonging[i][0]] == "correct"]
    missed = [i for i, need in required.items() if need and i not in set(found)]
    return PlateScore(required_total=sum(required.values()), required_found=len(found), outlines=len(owners),
                      correct=verdicts.count("correct"), verdicts=verdicts, missed=missed, false_kinds=false_kinds)
