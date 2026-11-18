"""ColonySet: every colony on one image, stored as parallel columns.

Coordinates follow one convention everywhere in the program: 0-based pixel
coordinates, x = column and y = row, with the center of pixel (r, c) at
(x=c, y=r). MATLAB coordinates are 1 larger in both axes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

KIND_IRREGULAR = "irregular"
KIND_CIRCULAR = "circular"
KIND_MANUAL = "manual"
KIND_SPLIT = "split"             # automatic, cut from a blob of touching colonies


@dataclass(frozen=True)
class ColonySet:
    """Immutable table of colonies. Each row is one colony.

    ids            unique integer per colony, assigned by the session
    centers        (N, 2) float, x and y
    radii          (N,) float, pixels
    boundaries     N outlines, each (M, 2) x/y points, or None for circles
    kinds          'irregular', 'circular' or 'manual'
    eccentricities (N,) float, NaN where not measured
    """

    ids: np.ndarray
    centers: np.ndarray
    radii: np.ndarray
    boundaries: tuple
    kinds: tuple
    eccentricities: np.ndarray

    # ----- construction -------------------------------------------------
    def __post_init__(self) -> None:
        for array in (self.ids, self.centers, self.radii, self.eccentricities):
            array.setflags(write=False)
        n = len(self.radii)
        if not (len(self.ids) == len(self.centers) == len(self.boundaries)
                == len(self.kinds) == len(self.eccentricities) == n):
            raise ValueError("ColonySet columns must all have the same length")

    @classmethod
    def empty(cls) -> "ColonySet":
        return cls(np.zeros(0, dtype=np.int64), np.zeros((0, 2)), np.zeros(0),
                   tuple(), tuple(), np.zeros(0))

    @classmethod
    def from_columns(cls, centers, radii, boundaries=None, kinds=None,
                     eccentricities=None, ids=None) -> "ColonySet":
        centers = np.asarray(centers, dtype=float).reshape(-1, 2)
        radii = np.asarray(radii, dtype=float).reshape(-1)
        n = len(radii)
        return cls(
            ids=np.asarray(ids if ids is not None else np.full(n, -1), dtype=np.int64),
            centers=centers,
            radii=radii,
            boundaries=tuple(boundaries) if boundaries is not None else (None,) * n,
            kinds=tuple(kinds) if kinds is not None else (KIND_MANUAL,) * n,
            eccentricities=(np.asarray(eccentricities, dtype=float)
                            if eccentricities is not None else np.full(n, np.nan)),
        )

    # ----- basic queries ------------------------------------------------
    def __len__(self) -> int:
        return len(self.radii)

    def index_of(self, colony_ids) -> np.ndarray:
        """Row positions of the given ids (ids not present are skipped)."""
        return np.flatnonzero(np.isin(self.ids, np.asarray(colony_ids)))

    def nearest(self, point) -> tuple[int, float]:
        """Row index of the colony center nearest to point, and the distance."""
        if len(self) == 0:
            return -1, float("inf")
        distances = np.hypot(self.centers[:, 0] - point[0], self.centers[:, 1] - point[1])
        idx = int(np.argmin(distances))
        return idx, float(distances[idx])

    def ids_in_rect(self, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
        """Ids of colonies whose centers lie inside the rectangle (findColoniesInRectangle)."""
        xmin, xmax = sorted((x0, x1))
        ymin, ymax = sorted((y0, y1))
        x, y = self.centers[:, 0], self.centers[:, 1]
        inside = (x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax)
        return self.ids[inside]

    # ----- derived sets (each returns a new ColonySet) ------------------
    def subset(self, rows) -> "ColonySet":
        rows = np.asarray(rows)
        if rows.dtype == bool:
            rows = np.flatnonzero(rows)
        return ColonySet(
            ids=self.ids[rows].copy(),
            centers=self.centers[rows].copy(),
            radii=self.radii[rows].copy(),
            boundaries=tuple(self.boundaries[i] for i in rows),
            kinds=tuple(self.kinds[i] for i in rows),
            eccentricities=self.eccentricities[rows].copy(),
        )

    def without(self, colony_ids) -> "ColonySet":
        keep = ~np.isin(self.ids, np.asarray(colony_ids))
        return self.subset(keep)

    def concat(self, other: "ColonySet") -> "ColonySet":
        return ColonySet(
            ids=np.concatenate([self.ids, other.ids]),
            centers=np.concatenate([self.centers, other.centers]),
            radii=np.concatenate([self.radii, other.radii]),
            boundaries=self.boundaries + other.boundaries,
            kinds=self.kinds + other.kinds,
            eccentricities=np.concatenate([self.eccentricities, other.eccentricities]),
        )

    def translated(self, dx: float, dy: float) -> "ColonySet":
        """Copy shifted by (dx, dy): converts between crop and photo coordinates."""
        shift = np.array([dx, dy])
        return ColonySet(
            ids=self.ids.copy(),
            centers=self.centers + shift,
            radii=self.radii.copy(),
            boundaries=tuple(None if b is None else b + shift for b in self.boundaries),
            kinds=self.kinds,
            eccentricities=self.eccentricities.copy(),
        )

    def with_ids(self, first_id: int) -> "ColonySet":
        """Copy with consecutive ids starting at first_id."""
        return ColonySet(
            ids=np.arange(first_id, first_id + len(self), dtype=np.int64),
            centers=self.centers.copy(),
            radii=self.radii.copy(),
            boundaries=self.boundaries,
            kinds=self.kinds,
            eccentricities=self.eccentricities.copy(),
        )
