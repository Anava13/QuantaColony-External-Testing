"""Photo scale (Qt-free): how many photo pixels make one millimeter, and the evidence behind it.

Every photo carries its own scale, set in Cropping, so one project can hold photos taken at several
camera distances or of several plate sizes. A scale is one of:

    dish   the dish circle of a round plate, with the dish's inner diameter in millimeters
    bar    a line drawn on the photo along a feature of known length (a ruler, a scale bar, a
           measured plate edge), with that length and its unit
    none   no scale: sizes from this photo stay in pixels
    fixed  a px/mm given when the photo was added (imported projects, sample data); Cropping shows it
           and replaces it when another method is chosen

The evidence (dish diameter, or the bar's end points in photo pixels with its length) is stored
with the photo, so a scale can be checked and edited later; px/mm is computed from it here, in
one place. Where the evidence is incomplete (a bar not drawn yet, no dish diameter), the photo has
no scale and Cropping says what is missing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

DISH, BAR, NONE, FIXED = "dish", "bar", "none", "fixed"
METHODS = (DISH, BAR, NONE, FIXED)
CHOICES = (DISH, BAR, NONE)                   # the methods Cropping offers
METHOD_NAMES = {DISH: "Dish edge", BAR: "Scale bar", NONE: "No scale", FIXED: "Imported"}
UNITS = {"mm": 1.0, "um": 0.001}              # millimeters per unit
UNIT_NAMES = {"mm": "mm", "um": "µm"}
MIN_BAR_PX = 4.0                               # shortest bar that sets a scale, photo pixels

Point = tuple[float, float]


@dataclass(frozen=True)
class Scale:
    method: str = NONE
    dish_mm: float | None = None               # inner diameter of the dish (method dish)
    bar: tuple[Point, Point] | None = None     # end points in photo pixels (method bar)
    length: float | None = None                # real length of the bar, in `unit`
    unit: str = "mm"
    value: float | None = None                 # px/mm (method fixed)

    # ----- the scale ---------------------------------------------------------
    def bar_px(self) -> float | None:
        if self.bar is None:
            return None
        (x0, y0), (x1, y1) = self.bar
        return math.hypot(x1 - x0, y1 - y0)

    def px_per_mm(self, layout=None) -> float | None:
        """Photo pixels per millimeter, or None while the evidence is incomplete or the method is none.
        layout: the photo's plate layout (method dish reads the dish radius from it)."""
        if self.method == DISH:
            radius = getattr(layout, "r", None) if getattr(layout, "kind", "") == "circular" else None
            if radius is None or not self.dish_mm or self.dish_mm <= 0:
                return None
            return 2.0 * float(radius) / float(self.dish_mm)
        if self.method == BAR:
            pixels = self.bar_px()
            if pixels is None or pixels < MIN_BAR_PX or not self.length or self.length <= 0:
                return None
            return pixels / (float(self.length) * UNITS.get(self.unit, 1.0))
        if self.method == FIXED and self.value and self.value > 0:
            return float(self.value)
        return None

    def problem(self, layout=None) -> str:
        """What keeps the chosen method from setting a scale ('' when it sets one, or for no scale)."""
        if self.method == DISH:
            if getattr(layout, "kind", "") != "circular":
                return "Dish edge needs a round dish layout. Draw a scale bar or choose No scale."
            if not self.dish_mm or self.dish_mm <= 0:
                return "Enter the dish diameter in millimeters."
        elif self.method == BAR:
            if self.bar is None or (self.bar_px() or 0) < MIN_BAR_PX:
                return "Drag along the scale bar on the photo."
            if not self.length or self.length <= 0:
                return "Enter the bar's length."
        return ""

    def describe(self, layout=None) -> str:
        """Short text for the toolbar: the px/mm and where it comes from."""
        value = self.px_per_mm(layout)
        if self.method == NONE:
            return "Sizes in pixels"
        if value is None:
            return ""
        if self.method == FIXED:
            return f"{value:.2f} px/mm (imported)"
        source = (f"{self.dish_mm:g} mm dish" if self.method == DISH
                  else f"{self.length:g} {UNIT_NAMES.get(self.unit, self.unit)} bar")
        return f"{value:.2f} px/mm from the {source}"

    # ----- edits ---------------------------------------------------------------
    def with_method(self, method: str) -> "Scale":
        return replace(self, method=method if method in METHODS else NONE)

    def with_dish_mm(self, dish_mm: float | None) -> "Scale":
        return replace(self, dish_mm=dish_mm)

    def with_bar(self, start: Point, end: Point) -> "Scale":
        """A drawn bar also chooses the bar method (drawing one is the user's choice of it)."""
        return replace(self, method=BAR, bar=((float(start[0]), float(start[1])), (float(end[0]), float(end[1]))))

    def with_length(self, length: float | None, unit: str | None = None) -> "Scale":
        return replace(self, length=length, unit=unit if unit in UNITS else self.unit)

    def carried_to(self, shape: tuple[int, int]) -> "Scale":
        """The scale proposed for the next photo, of shape (rows, columns): everything kept, so a fixed
        camera needs no new bar; a bar that would leave the photo is dropped (its length stays)."""
        if self.bar is None:
            return self
        rows, cols = shape[:2]
        inside = all(0 <= x <= cols and 0 <= y <= rows for x, y in self.bar)
        return self if inside else replace(self, bar=None)

    # ----- storage -------------------------------------------------------------
    def to_dict(self) -> dict:
        data = {"method": self.method}
        if self.dish_mm is not None:
            data["dish_mm"] = float(self.dish_mm)
        if self.bar is not None:
            data["bar"] = [list(self.bar[0]), list(self.bar[1])]
        if self.length is not None:
            data["length"] = float(self.length)
        if self.value is not None:
            data["value"] = float(self.value)
        data["unit"] = self.unit
        return data


def scale_from_dict(data: dict | None) -> Scale:
    """A stored scale; an empty or unreadable record is no scale."""
    if not data:
        return Scale()
    bar = data.get("bar")
    try:
        bar = ((float(bar[0][0]), float(bar[0][1])), (float(bar[1][0]), float(bar[1][1]))) if bar else None
    except (TypeError, ValueError, IndexError):
        bar = None
    return Scale(data.get("method") if data.get("method") in METHODS else NONE, _number(data.get("dish_mm")),
                 bar, _number(data.get("length")), data.get("unit") if data.get("unit") in UNITS else "mm",
                 _number(data.get("value")))


def default_scale(plate: dict) -> Scale:
    """The first photo's scale: the dish edge for round plates (with the project's dish diameter),
    a scale bar for rectangular plates, whose shapes vary too much to assume a size."""
    if plate.get("type") == "rectangular":
        return Scale(BAR)
    return Scale(DISH, dish_mm=_number(plate.get("dish_mm")))


def parse_number(text: str) -> float | None:
    """A positive number typed by the user, or None."""
    value = _number(text.strip().replace(",", ".")) if isinstance(text, str) else _number(text)
    return value if value is not None and value > 0 and math.isfinite(value) else None


def _number(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
