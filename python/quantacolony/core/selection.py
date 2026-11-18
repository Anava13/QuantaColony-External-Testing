"""Matching plates on colony count: one plate per group, the one whose colony count lies closest
to a shared target (Qt-free). A port of the MATLAB CountDatasetGenerator.

Colony count is what carries the crowding effect on colony size, and how many colonies establish
depends on the strain, so plates are compared after growth, at similar colony counts.

Built on replicates. The replicate plates of an index are interchangeable, so they are always the
pool: by default every design variable is matched within, each group is one index, and the index
keeps the replicate plate whose colony count is closest to the target. Leaving a design variable out
of Match within chooses across its levels too: with Density left out, each Genotype x Passage x
Series group keeps the plate closest to the target across every density tier and its replicates
(the count-matching rule of CountDatasetGenerator, which matched within Genotype x Passage).
Each group (a combination of levels of the variables matched within) keeps one plate:

    pool      the plates of the group that pass every filter:
                - not left out by hand
                - no hidden level on any other variable, and no level marked "not chosen from"
                  (hidden levels play the role of the MATLAB incubation-time and source filters;
                  "not chosen from" lets the pool be narrower than the plates drawn, as
                  CountDatasetGenerator picked from Standard A/B plates only while QC.m drew more)
                - inside the separable range: capture ratio >= SEPARABLE_CAPTURE_RATIO (a plate
                  with no capture ratio is outside it)
                - a colony count inside the colony window: at least its minimum (1 unless set, the
                  MATLAB rule) and at most its maximum (no limit unless set)
    target    the median colony count over every plate passing those filters, in every group
              (the MATLAB default); the least-deviation target (LEAST_DEVIATION): the count that makes
              the sum, over every group, of |chosen plate's count - target| smallest; or a fixed number. The window and the target are separate
              choices: with a fixed target, moving the window changes which plates can be chosen
              and nothing else
    choice    the pool plate whose count is closest to the target; a tie goes to the plate first
              in project order (lowest index id), as MATLAB's min() takes the first in composite order

Groups are the combinations of shown levels that hold at least one plate (a design need not cross
every variable, so a combination nobody plated is not a group). A group whose plates all fail a
filter is reported, not skipped. Every plate of a group is checked against every filter
independently, and each failure is recorded (the MATLAB diagnoseEmptyPool), so an empty group says
exactly what kept each of its plates out.

A person can pin a plate for a group (it is used whatever the target, even one failing a filter,
and is marked so) or leave a group empty on purpose; these replace the MATLAB per-cell overrides.

Colony count is the plate's: when several indices share one photo, their counts add up, since
crowding acts on the whole dish.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

from .growth import SEPARABLE_CAPTURE_RATIO

SELECTED, PINNED, MISS, SKIPPED = "selected", "pinned", "miss", "skipped"
STATUS_TEXT = {SELECTED: "selected", PINNED: "pinned by hand", MISS: "no eligible plate",
               SKIPPED: "left empty by choice"}


@dataclass(frozen=True)
class Candidate:
    """One plate of a group and every filter it fails ('' when it passes them all)."""

    index_id: int
    label: str
    count: float
    capture_ratio: float
    failures: tuple[str, ...]
    kinds: tuple[str, ...] = ()                # the filter each failure belongs to

    @property
    def eligible(self) -> bool:
        return not self.failures


@dataclass(frozen=True)
class Group:
    key: tuple                                 # level values of the matched variables
    label: str
    status: str                                # SELECTED, PINNED, MISS or SKIPPED
    chosen: Candidate | None
    pool_size: int
    candidates: tuple[Candidate, ...]          # every plate of the group, in project order

    def distance(self, target: float) -> float:
        return abs(self.chosen.count - target) if self.chosen is not None else np.nan

    def why_empty(self) -> str:
        """For an empty group: what kept each plate out."""
        if self.status == SKIPPED:
            return "left empty by choice"
        if not self.candidates:
            return "no plate has this combination"
        return "; ".join(f"{c.label}: {', '.join(c.failures)}" for c in self.candidates)


@dataclass(frozen=True)
class Selection:
    variables: tuple[str, ...]                 # display names of the matched variables
    target: float
    target_rule: str
    eligible: int                              # plates passing every filter, over all groups
    groups: tuple[Group, ...]
    cutoff: float = SEPARABLE_CAPTURE_RATIO
    across: tuple[str, ...] = ()               # display names of the design variables chosen across
    plates: int = 0                            # plates in the groups (shown levels)
    replicates: bool = False                   # the project numbers replicate plates within its indices
    window: tuple = (1, None)                  # colony window (minimum, maximum or None)

    @property
    def chosen(self) -> set[int]:
        return {g.chosen.index_id for g in self.groups if g.chosen is not None}

    def counts(self) -> dict[str, int]:
        tally = {SELECTED: 0, PINNED: 0, MISS: 0, SKIPPED: 0}
        for g in self.groups:
            tally[g.status] += 1
        return tally

    def summary(self) -> str:
        c = self.counts()
        filled = c[SELECTED] + c[PINNED]
        text = f"One plate per {' × '.join(self.variables)} ({self.pool_text()}): colony count closest to " \
               f"{self.target:g} ({self.target_rule}), among plates with {window_text(self.window)}; " \
               f"{filled} of {len(self.groups)} " \
               f"{'indices' if not self.across else 'groups'} filled from {self.plates} plates"
        extras = []
        if c[PINNED]:
            extras.append(f"{c[PINNED]} pinned by hand")
        if c[MISS]:
            extras.append(f"{c[MISS]} with no eligible plate")
        if c[SKIPPED]:
            extras.append(f"{c[SKIPPED]} left empty by choice")
        return text + (f" ({', '.join(extras)})" if extras else "") + "."

    def pool_text(self) -> str:
        """What a group chooses among: its replicate plates, and the levels of any variable chosen across."""
        plates = " among its replicate plates" if self.replicates else ""
        if not self.across:
            return "each index" + (", chosen" + plates if plates else "")
        return f"chosen across {' and '.join(self.across)}" + (" and the replicate plates" if self.replicates else "")

    def diagnostics(self) -> str:
        """For every empty group, what kept each of its plates out (the MATLAB diagnoseEmptyPool):
        each plate with every filter it fails, how many plates fail each filter, and how many fail only
        one (the ones a change of filter would let in)."""
        blocks = []
        for g in self.groups:
            if g.status != MISS:
                continue
            if not g.candidates:
                blocks.append(f"{g.label}: no plate has this combination.")
                continue
            n = len(g.candidates)
            lines = [f"{g.label}: {n} plate{'s' if n != 1 else ''}, none eligible."]
            lines += [f"    {c.label} ({c.count:g} colonies): {', '.join(c.failures)}" for c in g.candidates]
            tally: dict[str, int] = {}
            for c in g.candidates:
                for kind in set(c.kinds):
                    tally[kind] = tally.get(kind, 0) + 1
            lines.append("    failing: " + ", ".join(f"{kind} {n}" for kind, n in sorted(tally.items())))
            single = sum(1 for c in g.candidates if len(set(c.kinds)) == 1)
            lines.append(f"    failing only one filter: {single}")
            blocks.append("\n".join(lines))
        return "\n".join(blocks)

    def rows(self) -> list[dict]:
        """The selection as a table: one row per group (Save table)."""
        rows = []
        for g in self.groups:
            c = g.chosen
            rows.append({"group": g.label, "status": STATUS_TEXT[g.status],
                         "plate": c.label if c else "", "index_id": c.index_id if c else "",
                         "colonies": c.count if c else "", "from_target": g.distance(self.target) if c else "",
                         "capture_ratio": "" if c is None or not np.isfinite(c.capture_ratio) else c.capture_ratio,
                         "pool": g.pool_size, "filters_failed": ", ".join(c.failures) if c else "",
                         "why_empty": "" if c else g.why_empty()})
        return rows


@dataclass(frozen=True)
class PlateTable:
    """What selection reads: one entry per index."""

    index_ids: np.ndarray
    levels: dict                               # variable name -> level value per index
    counts: np.ndarray                         # colonies on the plate (photo) the index is on
    capture_ratios: np.ndarray
    labels: tuple[str, ...]                    # how a plate is named in the table (its photo)
    plates: tuple[str, ...] = ()               # each plate's replicate number in its index ('' or () when none)


def window_text(window: tuple) -> str:
    """A colony window in words: '50 to 200 colonies', 'at least 50 colonies', 'any colony count'."""
    low, high = window
    if high is None:
        return "at least 1 colony" if low <= 1 else f"at least {low:g} colonies"
    return f"{low:g} to {high:g} colonies"


def group_key_text(key: tuple) -> str:
    """A group's key as text (saved pins and skips)."""
    return " | ".join(str(v) for v in key)


def failures(plates: PlateTable, row: int, variables, matched: set[str], hidden: dict, cutoff: float,
             not_chosen: dict | None = None, by_hand=frozenset(), window: tuple = (1, None)) -> tuple:
    """Every filter one plate fails, each checked on its own: ((filter, text), ...)."""
    found = []
    if int(plates.index_ids[row]) in by_hand:
        found.append(("by hand", "left out by hand"))
    for v in variables:
        if v.name in matched:
            continue
        value = plates.levels[v.name][row]
        if value in set(hidden.get(v.name, ())):
            found.append((v.display_name, f"{v.display_name} {v.display(value)} (hidden)"))
        elif value in set((not_chosen or {}).get(v.name, ())):
            found.append((v.display_name, f"{v.display_name} {v.display(value)} (not chosen from)"))
    ratio = plates.capture_ratios[row]
    if not np.isfinite(ratio):
        found.append(("separable", "no capture ratio measured"))
    elif ratio < cutoff:
        found.append(("separable", f"capture ratio {ratio:.3f} below {cutoff:.2f}"))
    count = plates.counts[row]
    low, high = window
    if not np.isfinite(count) or count < 1:
        found.append(("colonies", "no colonies"))
    elif count < low:
        found.append(("colonies", f"{count:g} colonies, below {low:g}"))
    elif high is not None and count > high:
        found.append(("colonies", f"{count:g} colonies, above {high:g}"))
    return tuple(found)


LEAST_DEVIATION = "least_deviation"              # SelectionChoice.target: the target minimizing summed deviation


def least_deviation_target(pools: list[list[float]]) -> tuple[float, float]:
    """(target, X): the target t that minimizes X(t) = sum over groups of min |count - t| over the group's
    eligible counts, so the chosen plates sit as close to one shared count as the plates allow.

    X is piecewise linear with its lowest points at plate counts, so only those are tried; a tie goes to
    the smallest count."""
    pools = [list(p) for p in pools if len(p)]
    if not pools:
        raise ValueError("No eligible plates to set a count target: every plate fails a filter.")
    candidates = sorted({float(c) for pool in pools for c in pool})
    arrays = [np.asarray(pool, dtype=float) for pool in pools]
    best, best_x = candidates[0], np.inf
    for t in candidates:
        x = float(sum(np.min(np.abs(a - t)) for a in arrays))
        if x < best_x - 1e-9:
            best, best_x = t, x
    return best, best_x


def select_plates(plates: PlateTable, variables, matched: list[str], hidden: dict | None = None,
                  target: float | None = None, pins: dict | None = None, skips=(),
                  cutoff: float = SEPARABLE_CAPTURE_RATIO, not_chosen: dict | None = None,
                  by_hand=(), window: tuple = (1, None)) -> Selection:
    """One plate per group of the matched variables (see the module notes).

    variables: every StatsVariable of the project; matched: names of the variables matched within;
    hidden: {variable name: hidden level values}; pins: {group key text: index id}; skips: group
    key texts left empty; not_chosen: {variable name: level values shown but not chosen from} (the
    pool can be narrower than the plates drawn); by_hand: index ids left out by hand; window: the
    colony window (minimum, maximum or None for no limit)."""
    by_hand = frozenset(int(i) for i in by_hand)
    window = (max(1, window[0] or 1), window[1])
    hidden, pins, skips = hidden or {}, pins or {}, set(skips)
    by_name = {v.name: v for v in variables}
    matched = [name for name in matched if name in by_name]
    matched_set = set(matched)
    order = np.argsort(plates.index_ids, kind="stable")                 # project order
    checked = {int(r): failures(plates, int(r), variables, matched_set, hidden, cutoff, not_chosen, by_hand, window)
               for r in order}

    eligible = [plates.counts[r] for r, found in checked.items() if not found]
    shown_levels = [[x for x in by_name[name].values if x not in set(hidden.get(name, ()))] for name in matched]
    across = [v for v in variables if v.name not in matched_set and v.kind != "replicate"]
    held: dict[tuple, list[int]] = {}
    for r in checked:
        held.setdefault(tuple(plates.levels[name][r] for name in matched), []).append(r)
    if target is None:
        if not eligible:
            raise ValueError("No eligible plates to set a count target: every plate fails a filter.")
        target_value, rule = float(np.median(eligible)), f"median of {len(eligible)} eligible plates"
    elif target == LEAST_DEVIATION:
        # the groups the target chooses for: shown, not left empty, not pinned
        pools = []
        for key in itertools.product(*shown_levels):
            text = group_key_text(key)
            if text in skips or text in pins:
                continue
            pools.append([plates.counts[r] for r in held.get(key, []) if not checked[r]])
        target_value, total = least_deviation_target(pools)
        rule = (f"least total deviation over {sum(1 for p in pools if p)} groups: "
                f"chosen plates {total:g} colonies from the target in all")
    else:
        target_value, rule = float(target), "fixed"

    groups = []

    def label_of(r: int) -> str:
        """A candidate as its group shows it: the levels it is chosen across, its plate number, its photo."""
        parts = [v.display(plates.levels[v.name][r]) for v in across]
        number = plates.plates[r] if len(plates.plates) > r else ""
        if number:
            parts.append(f"plate {number}")
        parts.append(plates.labels[r])
        return " · ".join(parts)

    for key in itertools.product(*shown_levels):
        rows = held.get(key, [])
        if not rows:
            continue                                   # nobody plated this combination: not a group
        candidates = tuple(Candidate(int(plates.index_ids[r]), label_of(r), float(plates.counts[r]),
                                     float(plates.capture_ratios[r]), tuple(t for _, t in checked[r]),
                                     tuple(k for k, _ in checked[r])) for r in rows)
        label = " · ".join(by_name[name].display(value) for name, value in zip(matched, key))
        text = group_key_text(key)
        pool = [c for c in candidates if c.eligible]
        pinned = next((c for c in candidates if c.index_id == pins.get(text)), None)
        if text in skips:
            groups.append(Group(key, label, SKIPPED, None, len(pool), candidates))
        elif pinned is not None:
            groups.append(Group(key, label, PINNED, pinned, len(pool), candidates))
        elif not pool:
            groups.append(Group(key, label, MISS, None, 0, candidates))
        else:
            distances = np.array([abs(c.count - target_value) for c in pool])
            groups.append(Group(key, label, SELECTED, pool[int(np.argmin(distances))], len(pool), candidates))
    return Selection(tuple(by_name[name].display_name for name in matched), target_value, rule, len(eligible),
                     tuple(groups), cutoff, tuple(v.display_name for v in across),
                     sum(len(g.candidates) for g in groups), any(bool(x) for x in plates.plates), window)


def default_matched(variables) -> list[str]:
    """A first choice of variables to match within: every design variable, so each group is one index and
    keeps one of its replicate plates. A replicate variable is never matched within: its plates are the
    pool. Leave a variable out to choose across its levels too (Density, for count matching)."""
    return [v.name for v in variables if v.kind != "replicate"]


@dataclass
class SelectionChoice:
    """What the person chose (saved with the project's Stats preferences)."""

    on: bool = False
    matched: list = field(default_factory=list)
    target: float | str | None = None                   # None: median; LEAST_DEVIATION; or a fixed count
    pins: dict = field(default_factory=dict)
    skips: list = field(default_factory=list)
    not_chosen: dict = field(default_factory=dict)      # variable name -> levels drawn but not chosen from
    count_min: int = 1                                  # colony window: plates with fewer colonies are not chosen
    count_max: int | None = None                        # and plates with more (None: no limit)

    @property
    def window(self) -> tuple:
        return (self.count_min, self.count_max)

    @classmethod
    def from_dict(cls, data: dict | None) -> "SelectionChoice":
        data = data or {}
        target = data.get("target")
        return cls(bool(data.get("on", False)), list(data.get("matched", [])),
                   float(target) if isinstance(target, (int, float)) else
                   LEAST_DEVIATION if target == LEAST_DEVIATION else None,
                   {str(k): int(v) for k, v in dict(data.get("pins", {})).items()},
                   [str(s) for s in data.get("skips", [])],
                   {str(k): [str(x) for x in v] for k, v in dict(data.get("not_chosen", {})).items()},
                   max(1, int(data.get("count_min") or 1)),
                   int(data["count_max"]) if isinstance(data.get("count_max"), (int, float)) else None)

    def to_dict(self) -> dict:
        return {"on": self.on, "matched": self.matched, "target": self.target, "pins": self.pins,
                "skips": self.skips, "not_chosen": self.not_chosen, "count_min": self.count_min,
                "count_max": self.count_max}
