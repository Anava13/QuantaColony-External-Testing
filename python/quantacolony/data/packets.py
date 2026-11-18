"""Packets: the values the DataManager hands to modules and receives from them.

Modules see only these. The detection packets (ImageRecord, SavedDetection,
DetectionResult) live in core.record so Colony Detection does not depend on
this package.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.params import DetectionParams
from ..core.record import ImageRecord, LevelLabel, SavedDetection

__all__ = ["LevelSpec", "VariableSpec", "AnnotationSpec", "LevelInfo", "VariableInfo", "IndexInfo", "ProjectInfo",
           "Assignment", "ImagePacket", "DerivedTable", "LevelLabel"]


# ----- requests that build a project (project creation, cropping) ---------
@dataclass(frozen=True)
class LevelSpec:
    value: str
    display: str = ""                 # defaults to value
    is_control: bool = False


@dataclass(frozen=True)
class VariableSpec:
    name: str
    type: str                         # 'nominal', 'ordinal', 'replicate'
    levels: tuple[LevelSpec, ...]
    display_name: str = ""            # defaults to name


@dataclass(frozen=True)
class AnnotationSpec:
    """A fact recorded per plate without entering the design (variables index, annotations travel)."""

    name: str
    kind: str                         # 'number', 'choice', 'text'
    display_name: str = ""            # defaults to name
    unit: str = ""                    # for 'number'
    choices: tuple[str, ...] = ()     # for 'choice'


@dataclass(frozen=True)
class Assignment:
    """Cropping's decision for one index."""

    index_id: int
    photo_id: int
    region_kind: str                  # see data.regions.REGION_KINDS
    geometry: dict


# ----- what modules receive ------------------------------------------------
@dataclass(frozen=True)
class LevelInfo:
    level_id: int
    value: str
    display: str
    sort_order: int
    is_control: bool


@dataclass(frozen=True)
class VariableInfo:
    variable_id: int
    name: str
    display_name: str
    type: str
    levels: tuple[LevelInfo, ...]


@dataclass(frozen=True)
class IndexInfo:
    index_id: int
    labels: tuple[LevelLabel, ...]
    has_region: bool                  # cropping has assigned a photo region
    revision: int                     # 0 until Colony Detection commits
    needs_review: bool


@dataclass(frozen=True)
class ProjectInfo:
    project_id: str
    name: str
    schema_version: int
    variables: tuple[VariableInfo, ...]
    indices: tuple[IndexInfo, ...]
    detection_defaults: DetectionParams


@dataclass(frozen=True)
class ImagePacket:
    """Everything Colony Detection needs to open one index."""

    record: ImageRecord
    saved: SavedDetection | None      # None until the index is first committed
    defaults: DetectionParams         # starting parameters for a new index (the last finished image's)
    estimate: bool = False            # a new index estimates its parameters from its own image instead
    start_source: str = ""            # 'saved', 'last image', 'estimate' or 'defaults'


@dataclass(frozen=True)
class DerivedTable:
    """A derived result: computed on request or taken from a cache whose fingerprint matches."""

    name: str
    index_id: int
    algorithm_version: int
    columns: tuple[str, ...]
    rows: tuple[dict, ...] = field(default_factory=tuple)
    from_cache: bool = False


@dataclass(frozen=True)
class PhotoInfo:
    photo_id: int
    position: int                     # 1-based order in the project
    file_name: str
    width: int
    height: int
    px_per_mm: float | None           # None: the photo has no scale (sizes in pixels)
    scaling_method: str
    scale: dict = field(default_factory=dict)    # the scale's evidence (core.scale.Scale.to_dict)


@dataclass(frozen=True)
class CropPacket:
    """Everything Cropping needs: variables, plate settings, photos and their saved layouts."""

    project_name: str
    variables: tuple                  # core.cropping.VariableLevels of the design variables, in variable order
    plate: dict                       # type, sections or rows/cols and cells, dish_mm (the default dish size)
    photos: tuple[PhotoInfo, ...]
    layouts: dict                     # photo_id -> (layout dict, [levels or None per section])
    preferences: dict                 # cropping scope and board view
    replicate: str = ""               # name of the replicate variable ('' when the project has none); it is
                                      # not in variables: Cropping numbers the plates of an index itself
    plates_per_index: int = 1         # plates each index is expected to hold (proposals repeat the levels
                                      # of the previous photo until its index has them)


@dataclass(frozen=True)
class ProjectStatus:
    """Counts the project page shows for each step.

    A plate is one region with its own colonies (one stored index row). An index is one combination of
    the design variables' levels; with a replicate variable it holds every plate of that combination."""

    photos: int
    photos_cropped: int               # photos with a saved layout or a region
    plates: int                       # plates placed plus photos not cropped yet (at the plate's sections)
    plates_cropped: int               # plates with a region
    plates_detected: int
    plates_to_review: int
    colonies: int = 0                 # colonies counted over every detected plate
    indices: int = 0                  # indices in use: combinations of design levels holding a plate

    @property
    def plates_waiting(self) -> int:
        return self.plates_cropped - self.plates_detected
