"""What Colony Detection receives from the DataManager and what it sends back.

These are plain, immutable values. The DataManager builds an ImageRecord and
a SavedDetection from the project file; on "Finish image" the session builds
a DetectionResult and the DataManager stores it. Colony Detection never reads
or writes project files itself.

Coordinates here are crop coordinates (pixels of record.image). The
DataManager converts to and from photo coordinates, where results are stored.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .colony_set import ColonySet
from .params import DetectionParams


@dataclass(frozen=True)
class LevelLabel:
    """One variable's level for an index, e.g. ('Ordinal', 'ordinal', 'v_25', '25')."""

    variable: str             # variable display name
    variable_type: str        # 'nominal', 'ordinal', 'replicate'
    value: str                # stored value
    display: str              # value shown to the user


@dataclass(frozen=True)
class ImageRecord:
    """Everything Colony Detection needs about one multivariable index."""

    index_id: int
    name: str                          # file-safe identifier, used in export names
    labels: tuple[LevelLabel, ...]     # one per variable, in variable order
    image: np.ndarray                  # (rows, cols, 3) uint8 crop; pixels outside the region are black
    mask: np.ndarray                   # (rows, cols) bool, True on pixels that belong to the index
    px_per_mm: float | None            # None: the photo has no scale, sizes stay in pixels

    @property
    def nominal_display(self) -> str:
        """Every nominal level, joined (shown in the image picker)."""
        parts = [label.display for label in self.labels if label.variable_type not in ("ordinal", "replicate")]
        return " · ".join(parts) or self.name

    @property
    def ordinal_display(self) -> str:
        """Ordinal levels with their variable names, then the replicate number (shown in the chip), e.g.
        'Passage 25 · Replicate 2'."""
        return " · ".join(f"{label.variable} {label.display}"
                          for label in self.labels if label.variable_type in ("ordinal", "replicate"))

    @property
    def display_label(self) -> str:
        """One line for messages: every level's display value."""
        return " ".join(label.display for label in self.labels) or self.name


@dataclass(frozen=True)
class SavedDetection:
    """The last committed result for an index."""

    params: DetectionParams
    colonies: ColonySet                # final colonies
    baseline: ColonySet                # automatic result the edits started from
    revision: int
    detection_version: int
    needs_review: bool = False
    review_reason: str = ""


@dataclass(frozen=True)
class EditRecord:
    """One confirmed edit, kept in the project's edit log."""

    kind: str                          # 'Detection', 'Deletion', 'Addition'
    count: int
    colony_ids: tuple[int, ...]        # colonies added, removed or detected
    params: DetectionParams            # parameters after the edit


@dataclass(frozen=True)
class DetectionResult:
    """What "Finish image" sends to the DataManager."""

    index_id: int
    params: DetectionParams
    colonies: ColonySet
    baseline: ColonySet
    edits: tuple[EditRecord, ...]
    detection_version: int
