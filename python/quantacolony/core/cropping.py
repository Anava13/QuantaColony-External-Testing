"""Cropping model (Qt-free): plate layouts, the regions they produce, proposals and the index board.

A photo's layout describes the plate: a round dish (circle plus cut angles)
or a rectangular plate (rotated rectangle plus a rows x columns grid). A grid's
sections are its cells, or round spots centered in the cells (molded spot plates,
wells of agar), all spots one diameter. Each section of the layout becomes one
region, stored by the DataManager as an assignment of one index (one level of
every variable).

Cropping works with the design variables only. When the project has a replicate
variable, sections that share a combination of levels (on one photo or on several)
are interchangeable plates of one index, not duplicates: the DataManager numbers
them, and the index board counts the plates in each cell (replicates=True below).

Angles in layouts are degrees clockwise from the top of the photo (12 o'clock).
Region geometry handed to the DataManager uses data.regions' convention
(degrees clockwise from +x), so the conversion lives here, in one place.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field, replace

import numpy as np

CIRCULAR, RECTANGULAR = "circular", "rectangular"
CELLS, SPOTS = "cells", "spots"                  # what the sections of a grid are
SPOT_DEFAULT, SPOT_MIN = 0.8, 0.1                # spot diameter as a fraction of the shorter cell side
PHOTO, SECTION = "photo", "section"            # where a variable's level is set
ROWS, COLUMNS, PAGES = "rows", "columns", "pages"


# =========================================================================
# layouts
# =========================================================================
@dataclass(frozen=True)
class DishLayout:
    """Round dish: circle (photo pixels) and cut angles (degrees clockwise from the top)."""

    cx: float
    cy: float
    r: float
    cuts: tuple[float, ...]
    kind: str = CIRCULAR

    @classmethod
    def template(cls, cx: float, cy: float, r: float, sections: int, rotation: float = 0.0) -> "DishLayout":
        cuts = () if sections <= 1 else tuple((rotation + k * 360.0 / sections) % 360 for k in range(sections))
        return cls(cx, cy, r, tuple(sorted(cuts)))

    @property
    def section_count(self) -> int:
        return max(1, len(self.cuts))

    def spans(self) -> list[tuple[float, float]]:
        """(start, end) of each section, clockwise from the top; end may exceed 360."""
        if len(self.cuts) <= 1:
            return [(0.0, 360.0)]
        cuts = sorted(self.cuts)
        return [(a, b if b > a else b + 360) for a, b in zip(cuts, cuts[1:] + cuts[:1])]

    def rotated(self, delta: float) -> "DishLayout":
        return replace(self, cuts=tuple(sorted((c + delta) % 360 for c in self.cuts)))

    def moved(self, cx: float, cy: float) -> "DishLayout":
        return replace(self, cx=cx, cy=cy)

    def resized(self, r: float) -> "DishLayout":
        return replace(self, r=max(5.0, r))

    def with_cut(self, index: int, angle: float) -> "DishLayout":
        cuts = list(self.cuts)
        cuts[index] = angle % 360
        return replace(self, cuts=tuple(cuts))          # order kept while dragging; normalized on release

    def normalized(self) -> "DishLayout":
        return replace(self, cuts=tuple(sorted(c % 360 for c in self.cuts)))

    def with_added_cut(self, angle: float) -> "DishLayout":
        cuts = list(self.cuts)
        if not cuts:                                    # a whole dish needs two cuts to make two sections
            cuts = [angle % 360, (angle + 180) % 360]
        else:
            cuts.append(angle % 360)
        return replace(self, cuts=tuple(sorted(cuts)))

    def without_cut(self, index: int) -> "DishLayout":
        cuts = [c for i, c in enumerate(self.cuts) if i != index]
        return replace(self, cuts=tuple(cuts) if len(cuts) > 1 else ())

    def section_at(self, x: float, y: float) -> int:
        if math.hypot(x - self.cx, y - self.cy) > self.r:
            return -1
        angle = clock_angle(x - self.cx, y - self.cy)
        for i, (a, b) in enumerate(self.spans()):
            if a <= angle < b or a <= angle + 360 < b:
                return i
        return 0

    def label_point(self, index: int) -> tuple[float, float]:
        a, b = self.spans()[index]
        if self.section_count == 1:
            return self.cx, self.cy
        return point_at(self.cx, self.cy, (a + b) / 2, self.r * 0.62)

    def region(self, index: int) -> tuple[str, dict]:
        a, b = self.spans()[index]
        if self.section_count == 1:
            a, b = 0.0, 360.0
        return "sector", {"cx": self.cx, "cy": self.cy, "radius": self.r,
                          "start_deg": (a - 90) % 360,
                          "end_deg": (b - 90) % 360 if b - a < 360 else (a - 90) % 360 + 360}

    def to_dict(self) -> dict:
        return {"kind": CIRCULAR, "cx": self.cx, "cy": self.cy, "r": self.r, "cuts": list(self.cuts)}


@dataclass(frozen=True)
class GridLayout:
    """Rectangular plate: rotated rectangle (center, size, angle in degrees) cut into an even grid.
    cells: CELLS (each cell is a section) or SPOTS (a round spot centered in each cell is the section;
    spot is its diameter as a fraction of the shorter cell side)."""

    cx: float
    cy: float
    width: float
    height: float
    angle: float
    rows: int
    cols: int
    kind: str = RECTANGULAR
    cells: str = CELLS
    spot: float = SPOT_DEFAULT

    @property
    def section_count(self) -> int:
        return self.rows * self.cols

    @property
    def spots(self) -> bool:
        return self.cells == SPOTS

    def _to_photo(self, u: float, v: float) -> tuple[float, float]:
        """Plate coordinates (u right, v down, from the center) to photo pixels."""
        t = math.radians(self.angle)
        return self.cx + u * math.cos(t) - v * math.sin(t), self.cy + u * math.sin(t) + v * math.cos(t)

    def _to_plate(self, x: float, y: float) -> tuple[float, float]:
        t = math.radians(self.angle)
        dx, dy = x - self.cx, y - self.cy
        return dx * math.cos(t) + dy * math.sin(t), -dx * math.sin(t) + dy * math.cos(t)

    def corners(self) -> np.ndarray:
        w, h = self.width / 2, self.height / 2
        return np.array([self._to_photo(u, v) for u, v in ((-w, -h), (w, -h), (w, h), (-w, h))])

    def _cell_center_uv(self, index: int) -> tuple[float, float]:
        row, col = divmod(index, self.cols)
        return (-self.width / 2 + (col + 0.5) * self.width / self.cols,
                -self.height / 2 + (row + 0.5) * self.height / self.rows)

    def cell(self, index: int) -> np.ndarray:
        row, col = divmod(index, self.cols)
        u0 = -self.width / 2 + col * self.width / self.cols
        v0 = -self.height / 2 + row * self.height / self.rows
        u1, v1 = u0 + self.width / self.cols, v0 + self.height / self.rows
        return np.array([self._to_photo(u, v) for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))])

    def cell_center(self, index: int) -> tuple[float, float]:
        return self._to_photo(*self._cell_center_uv(index))

    @property
    def spot_radius(self) -> float:
        return self.spot * min(self.width / self.cols, self.height / self.rows) / 2

    def spot_handle(self) -> tuple[float, float]:
        """Point on the right edge (plate coordinates) of the first spot, where its diameter is dragged."""
        u, v = self._cell_center_uv(0)
        return self._to_photo(u + self.spot_radius, v)

    def grid_lines(self) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        w, h = self.width / 2, self.height / 2
        xs = [-w + c * self.width / self.cols for c in range(1, self.cols)]
        ys = [-h + r * self.height / self.rows for r in range(1, self.rows)]
        lines = [(self._to_photo(u, -h), self._to_photo(u, h)) for u in xs]
        lines += [(self._to_photo(-w, v), self._to_photo(w, v)) for v in ys]
        return lines

    def section_at(self, x: float, y: float) -> int:
        """The cell under a point (with spots too: a click anywhere in a cell picks its spot)."""
        u, v = self._to_plate(x, y)
        if abs(u) > self.width / 2 or abs(v) > self.height / 2:
            return -1
        col = min(self.cols - 1, int((u + self.width / 2) / self.width * self.cols))
        row = min(self.rows - 1, int((v + self.height / 2) / self.height * self.rows))
        return row * self.cols + col

    def label_point(self, index: int) -> tuple[float, float]:
        return self.cell_center(index)

    def region(self, index: int) -> tuple[str, dict]:
        if self.spots:
            cx, cy = self.cell_center(index)
            return "sector", {"cx": round(cx, 2), "cy": round(cy, 2), "radius": round(self.spot_radius, 2),
                              "start_deg": 0.0, "end_deg": 360.0}
        return "polygon", {"points": self.cell(index).round(2).tolist()}

    def moved(self, cx: float, cy: float) -> "GridLayout":
        return replace(self, cx=cx, cy=cy)

    def rotated(self, delta: float) -> "GridLayout":
        return replace(self, angle=(self.angle + delta) % 360)

    def corner_to(self, x: float, y: float) -> "GridLayout":
        """Resize symmetrically so a corner sits at (x, y)."""
        u, v = self._to_plate(x, y)
        return replace(self, width=max(10.0, 2 * abs(u)), height=max(10.0, 2 * abs(v)))

    def with_grid(self, rows: int, cols: int) -> "GridLayout":
        return replace(self, rows=max(1, min(24, rows)), cols=max(1, min(24, cols)))

    def with_cells(self, cells: str) -> "GridLayout":
        return replace(self, cells=SPOTS if cells == SPOTS else CELLS)

    def with_spot(self, fraction: float) -> "GridLayout":
        return replace(self, spot=min(1.0, max(SPOT_MIN, float(fraction))))

    def spot_to(self, x: float, y: float) -> "GridLayout":
        """Resize every spot so the first spot's edge passes through (x, y)."""
        u0, v0 = self._cell_center_uv(0)
        u, v = self._to_plate(x, y)
        side = min(self.width / self.cols, self.height / self.rows)
        return self.with_spot(2 * math.hypot(u - u0, v - v0) / side)

    def to_dict(self) -> dict:
        return {"kind": RECTANGULAR, "cx": self.cx, "cy": self.cy, "width": self.width, "height": self.height,
                "angle": self.angle, "rows": self.rows, "cols": self.cols, "cells": self.cells,
                "spot": self.spot}


Layout = DishLayout | GridLayout


def layout_from_dict(data: dict) -> Layout:
    if data.get("kind") == RECTANGULAR:
        return GridLayout(data["cx"], data["cy"], data["width"], data["height"], data["angle"],
                          data["rows"], data["cols"], cells=SPOTS if data.get("cells") == SPOTS else CELLS,
                          spot=float(data.get("spot", SPOT_DEFAULT)))
    return DishLayout(data["cx"], data["cy"], data["r"], tuple(data.get("cuts", ())))


def clock_angle(dx: float, dy: float) -> float:
    """Degrees clockwise from 12 o'clock for a photo-pixel offset (y points down)."""
    return math.degrees(math.atan2(dx, -dy)) % 360


def point_at(cx: float, cy: float, angle: float, radius: float) -> tuple[float, float]:
    a = math.radians(angle)
    return cx + radius * math.sin(a), cy - radius * math.cos(a)


def default_layout(image: np.ndarray, plate: dict) -> Layout:
    """First layout for a photo: dish found automatically, or a centered grid."""
    rows, cols = image.shape[:2]
    if plate.get("type") == RECTANGULAR:
        return GridLayout(cols / 2, rows / 2, cols * 0.8, rows * 0.8, 0.0,
                          int(plate.get("rows", 2)), int(plate.get("cols", 2)),
                          cells=SPOTS if plate.get("cells") == SPOTS else CELLS)
    cx, cy, r = fit_dish(image)
    return DishLayout.template(cx, cy, r, int(plate.get("sections", 4)))


def fit_dish(image: np.ndarray) -> tuple[float, float, float]:
    """Dish circle (cx, cy, r) in photo pixels from the strongest large circle; centered guess if none."""
    from skimage.feature import canny
    from skimage.transform import hough_circle, hough_circle_peaks, rescale

    rows, cols = image.shape[:2]
    gray = image[..., :3].mean(axis=2) / 255.0 if image.ndim == 3 else image / 255.0
    scale = min(1.0, 300.0 / max(rows, cols))
    small = rescale(gray, scale, anti_aliasing=True) if scale < 1 else gray
    edges = canny(small, sigma=2.0)
    short = min(small.shape)
    radii = np.arange(int(short * 0.30), int(short * 0.52), 2)
    if edges.any() and len(radii):
        accumulator = hough_circle(edges, radii)
        _, x, y, r = hough_circle_peaks(accumulator, radii, total_num_peaks=1)
        if len(r):
            return float(x[0] / scale), float(y[0] / scale), float(r[0] / scale)
    return cols / 2, rows / 2, min(rows, cols) * 0.45


# =========================================================================
# levels, proposals and duplicates
# =========================================================================
@dataclass(frozen=True)
class VariableLevels:
    """One variable as Cropping sees it: name, display name and its levels (values and display text)."""

    name: str
    display_name: str
    values: tuple[str, ...]
    displays: tuple[str, ...]
    kind: str = ""                   # 'nominal', 'ordinal', 'replicate'

    def display(self, value: str) -> str:
        return self.displays[self.values.index(value)] if value in self.values else value


Levels = dict                       # variable name -> level value, one per variable


def index_key(levels: Levels, variables: list[VariableLevels]) -> tuple:
    return tuple(levels.get(v.name) for v in variables)


def complete(levels: Levels | None, variables: list[VariableLevels]) -> bool:
    return levels is not None and all(levels.get(v.name) in v.values for v in variables)


def step_photo_levels(levels: Levels, photo_variables: list[VariableLevels]) -> Levels:
    """Next combination of the whole-photo variables, the last one changing fastest (an odometer)."""
    stepped = dict(levels)
    for variable in reversed(photo_variables):
        current = stepped.get(variable.name)
        position = variable.values.index(current) if current in variable.values else -1
        position = (position + 1) % len(variable.values)
        stepped[variable.name] = variable.values[position]
        if position != 0:
            break
    return stepped


def propose_sections(previous: list[Levels | None], photo_variables: list[VariableLevels],
                     repeat: bool = False) -> list[Levels | None]:
    """The previous photo's section levels with the whole-photo variables stepped to their next combination.
    repeat: keep them instead (the previous photo's index still expects more replicate plates)."""
    first = next((lv for lv in previous if lv), None)
    if first is None or not photo_variables or repeat:
        return [dict(lv) if lv else None for lv in previous]
    stepped = step_photo_levels(first, photo_variables)
    wide = {v.name: stepped[v.name] for v in photo_variables}
    return [dict(lv, **wide) if lv else None for lv in previous]


def duplicate_sections(sections: list[Levels | None], variables: list[VariableLevels],
                       elsewhere: dict[tuple, tuple[int, int]], replicates: bool = False) -> dict[int, str]:
    """Sections whose index is used twice on this photo or already sits on another photo -> reason.
    With replicates, a repeated combination is one more plate of that index, so nothing is a duplicate."""
    problems: dict[int, str] = {}
    if replicates:
        return problems
    seen: dict[tuple, int] = {}
    for i, levels in enumerate(sections):
        if not complete(levels, variables):
            continue
        key = index_key(levels, variables)
        if key in seen:
            problems[i] = f"Section {seen[key] + 1} has the same index."
            problems.setdefault(seen[key], f"Section {i + 1} has the same index.")
        else:
            seen[key] = i
        if key in elsewhere:
            photo, section = elsewhere[key]
            problems[i] = f"Already on photo {photo}, section {section + 1}."
    return problems


def all_index_keys(variables: list[VariableLevels]) -> list[tuple]:
    return list(itertools.product(*(v.values for v in variables)))


def describe_key(key: tuple, variables: list[VariableLevels]) -> str:
    return " · ".join(v.display(value) for v, value in zip(variables, key))


# =========================================================================
# index board: any number of variables on two axes, plus pages
# =========================================================================
@dataclass(frozen=True)
class BoardCell:
    text: str                        # count text when a variable is folded into the cell, else ""
    state: str                       # Cropping: 'empty', 'proposed', 'confirmed', 'partial', 'duplicate'
                                     # Colony Detection: 'none', 'waiting', 'detected', 'review', 'partial'
    label: str                       # accessible description
    target: tuple | None             # what a click opens: (photo position, section) or (index id,)
    current: bool = False            # the cell holds the image that is open


@dataclass(frozen=True)
class Board:
    header_rows: list[list[tuple[str, int]]]     # per header row: (text, columns spanned)
    row_labels: list[list[str]]                  # per board row: one label per row variable ('' when repeated)
    cells: list[list[BoardCell]]
    counted: str                                 # display name of the variable folded into counts, or ''
    rows: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)


def default_view(variables: list[VariableLevels], scope: dict[str, str]) -> dict[str, str]:
    """Whole-photo variables on columns (one photo fills a column), section variables on rows."""
    return {v.name: COLUMNS if scope.get(v.name) == PHOTO else ROWS for v in variables}


def _layout_board(variables, view: dict[str, str], pages: dict[str, str], cell_for, max_columns: int) -> Board:
    """Rows, columns and pages of variables; cell_for(options, fixed, counted) makes each cell, where options
    holds one {variable: value} per index the cell covers (several when a variable is folded into counts)."""
    rows = [v for v in variables if view.get(v.name) == ROWS]
    cols = [v for v in variables if view.get(v.name) == COLUMNS]
    page_vars = [v for v in variables if view.get(v.name) == PAGES]
    span = lambda vs: math.prod(len(v.values) for v in vs)  # noqa: E731
    counted = None
    if span(cols) > max_columns and len(cols) > 1:
        counted, cols = cols[-1], cols[:-1]

    header_rows = []
    for depth, variable in enumerate(cols):
        inner, outer = span(cols[depth + 1:]), span(cols[:depth])
        header_rows.append([(variable.display(value), inner) for _ in range(outer) for value in variable.values])

    row_combos = list(itertools.product(*(v.values for v in rows))) or [()]
    col_combos = list(itertools.product(*(v.values for v in cols))) or [()]
    row_labels, cells = [], []
    for ri, row_values in enumerate(row_combos):
        labels = []
        for depth, variable in enumerate(rows):
            inner = span(rows[depth + 1:])
            labels.append(variable.display(row_values[depth]) if ri % inner == 0 else "")
        row_labels.append(labels or [""])
        line = []
        for col_values in col_combos:
            fixed = {v.name: x for v, x in zip(rows, row_values)}
            fixed.update({v.name: x for v, x in zip(cols, col_values)})
            fixed.update({v.name: pages.get(v.name, v.values[0]) for v in page_vars})
            options = [dict(fixed, **{counted.name: x}) for x in counted.values] if counted else [fixed]
            line.append(cell_for(options, fixed, counted))
        cells.append(line)
    return Board(header_rows, row_labels, cells, counted.display_name if counted else "",
                 [v.display_name for v in rows], [v.display_name for v in cols], [v.display_name for v in page_vars])


def build_board(variables: list[VariableLevels], view: dict[str, str], pages: dict[str, str],
                placements: dict[tuple, list[tuple[int, int, bool]]], max_columns: int = 16,
                replicates: bool = False) -> Board:
    """Cropping: placements maps index key -> [(photo position, section, confirmed)] for every region placed
    or proposed. With replicates, several regions for one index are its plates (their number is shown in
    the cell); without, they are duplicates."""
    def cell_for(options, fixed, counted):
        hits = [placements.get(index_key(o, variables), []) for o in options]
        if replicates:
            confirmed = sum(1 for h in hits if h and all(c for _, _, c in h))
            duplicate = False
        else:
            confirmed = sum(1 for h in hits if len(h) == 1 and h[0][2])
            duplicate = any(len(h) > 1 for h in hits)
        proposed = any(any(not c for _, _, c in h) for h in hits)
        target = next(((h[0][0], h[0][1]) for h in hits if h), None)
        state = ("duplicate" if duplicate else "confirmed" if confirmed == len(options)
                 else "proposed" if proposed else "partial" if confirmed else "empty")
        name = " · ".join(v.display(fixed[v.name]) for v in variables if v is not counted)
        plates = sum(len(h) for h in hits)
        if counted:
            label = f"{name}: {confirmed} of {len(options)} {counted.display_name.lower()} levels confirmed"
            text = f"{confirmed}/{len(options)}"
        elif replicates and plates:
            where = "; ".join(f"photo {p + 1}, section {s + 1}" for p, s, _ in hits[0])
            label = f"{name}: {plates} plate{'s' if plates != 1 else ''} ({where})"
            text = f"×{plates}" if plates > 1 else ""
        else:
            label = f"{name}: " + (f"photo {target[0] + 1}, section {target[1] + 1}" if target else "not assigned")
            text = ""
        return BoardCell(text, state, label, target)
    return _layout_board(variables, view, pages, cell_for, max_columns)


def build_status_board(variables: list[VariableLevels], view: dict[str, str], pages: dict[str, str],
                       status: dict[tuple, list[tuple[str, int]]], current: tuple | None = None,
                       max_columns: int = 16) -> Board:
    """Colony Detection: status maps index key -> [(state 'waiting' / 'detected' / 'review', index id)] for every
    plate with a region (one entry per plate; several when the index holds replicate plates); an index without
    one is 'none'. A click opens the cell's first plate waiting or to review, else its first plate."""
    words = {"none": "no region", "waiting": "not detected yet", "detected": "detected", "review": "to review"}

    def cell_for(options, fixed, counted):
        keys = [index_key(o, variables) for o in options]
        found = [(k, entry) for k in keys for entry in status.get(k, ())]
        states = [state for _, (state, _) in found]
        filled = [k for k in keys if status.get(k)]
        done = [k for k in filled if all(s == "detected" for s, _ in status[k])]
        state = ("none" if not found else "review" if "review" in states else
                 "detected" if len(done) == len(options) else "partial" if done or "detected" in states
                 else "waiting")
        first = next((index for _, (s, index) in found if s in ("waiting", "review")), None)
        target = (first if first is not None else found[0][1][1],) if found else None
        name = " · ".join(v.display(fixed[v.name]) for v in variables if v is not counted)
        plates = len(found)
        if counted:
            label = f"{name}: {len(done)} of {len(options)} {counted.display_name.lower()} levels detected"
            text = f"{len(done)}/{len(options)}"
        elif plates > 1:
            label = f"{name}: {states.count('detected')} of {plates} plates detected"
            if "review" in states:
                label += f", {states.count('review')} to review"
            text = f"{states.count('detected')}/{plates}"
        else:
            label = f"{name}: {words[states[0] if states else 'none']}"
            text = ""
        return BoardCell(text, state, label, target, current=current in keys)
    return _layout_board(variables, view, pages, cell_for, max_columns)


def default_scope(variables: list[VariableLevels]) -> dict[str, str]:
    """First guess at where levels are set: nominal variables per section, the rest per photo."""
    return {v.name: SECTION if v.kind == "nominal" else PHOTO for v in variables}
