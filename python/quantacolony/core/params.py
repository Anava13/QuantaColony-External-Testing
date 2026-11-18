"""Detection parameters and the limits of the spinners that edit them."""

from __future__ import annotations

from dataclasses import dataclass, replace

IRREGULAR = "Irregular"
CIRCULAR = "Circular"


@dataclass(frozen=True)
class DetectionParams:
    """One complete set of detection settings.

    Frozen: a change produces a new object, so a command can keep the old
    settings for undo without copying anything.
    """

    level: float = 0.45          # brightness threshold; the sign sets polarity
    sense: float = 0.9           # Hough sensitivity (circular) or max eccentricity (irregular)
    rmin: float = 5.0            # minimum colony radius, px
    rmax: float = 8.0            # maximum colony radius, px
    maxpix: float = 2000.0       # components with this many pixels or more are ignored
    detection_type: str = IRREGULAR
    split: bool = False          # split touching colonies (irregular detection; see detection.split_blob)
    split_elongation: float = 0.6    # blobs more elongated than this (eccentricity) are tried for splitting
    split_pinch: float = 0.15    # a blob is cut where it narrows by at least this share of its widest point

    def with_value(self, field_name: str, value: float | str) -> "DetectionParams":
        return replace(self, **{field_name: value})


# New projects split touching colonies; projects made before splitting existed keep it off
# until the user turns it on, so their results do not change.
def new_project_detection() -> DetectionParams:
    return DetectionParams(split=True)


@dataclass(frozen=True)
class ParamSpec:
    """Spinner limits for one parameter (configureSpinnerProperties)."""

    label: str
    minimum: float
    maximum: float
    step: float
    decimals: int


# Order matches the spinner stack in the MATLAB parameter panel
PARAM_SPECS: dict[str, ParamSpec] = {
    "level": ParamSpec("Brightness threshold", -1.0, 1.0, 0.05, 3),
    "maxpix": ParamSpec("Maximum colony area (px)", 0.0, 10000.0, 250.0, 0),
    "sense": ParamSpec("Sensitivity", 0.01, 1.0, 0.025, 3),
    "rmin": ParamSpec("Minimum colony radius (px)", 1.0, 100.0, 1.0, 0),
    "rmax": ParamSpec("Maximum colony radius (px)", 0.0, 200.0, 1.0, 0),
    "split_elongation": ParamSpec("Split above elongation", 0.05, 0.99, 0.05, 2),
    "split_pinch": ParamSpec("Minimum pinch", 0.05, 0.9, 0.05, 2),
}


# The stored level carries the colony color in its sign (see detection.prepare_image). The
# interface shows the color as a toggle and the cutoff as the actual brightness (0 black, 1 white).
DARK, LIGHT = "dark", "light"
THRESHOLD_MIN, THRESHOLD_MAX, THRESHOLD_STEP = 0.01, 0.99, 0.01


def threshold_from_level(level: float) -> tuple[str, float]:
    """(colony color, brightness cutoff) for a stored level.
    Dark colonies keep pixels at or below the cutoff; light colonies keep pixels at or above it."""
    if level >= 0:
        return DARK, round(min(max(level, THRESHOLD_MIN), THRESHOLD_MAX), 4)
    return LIGHT, round(min(max(1.0 - abs(level), THRESHOLD_MIN), THRESHOLD_MAX), 4)


def level_from_threshold(polarity: str, threshold: float) -> float:
    """The stored level for a colony color and a brightness cutoff (inverse of threshold_from_level)."""
    threshold = min(max(threshold, THRESHOLD_MIN), THRESHOLD_MAX)
    return round(threshold if polarity == DARK else -(1.0 - threshold), 4)


def validate_detection_parameters(params: DetectionParams) -> str | None:
    """Return an error message, or None when the parameters can be used."""
    if params.rmin >= params.rmax:
        return "Minimum colony radius must be smaller than maximum colony radius."
    if params.detection_type not in (IRREGULAR, CIRCULAR):
        return f"Unknown detection type: {params.detection_type}"
    return None
