"""DetectionSession: the working copy for one image.

Holds the image, the current parameters and colonies, the baseline and the
undo and redo history. Views never change it directly: the controller builds
a command and calls execute(). Listeners are told after every change.

Nothing here is saved until the controller asks for result() and hands it to
the DataManager. A crash loses only this session.
"""

from __future__ import annotations

import time
from typing import Callable

import numpy as np

from .autoparams import estimate_parameters
from .colony_set import KIND_MANUAL, ColonySet
from .commands import (SnapshotCommand, make_addition_command, make_deletion_command,
                       make_detection_command)
from .detection import detection_version, filter_valid_colonies, perform_detection
from .params import DetectionParams
from .record import DetectionResult, EditRecord, ImageRecord, SavedDetection

Listener = Callable[[str], None]   # receives an event name


class DetectionSession:
    MAX_UNDO_HISTORY = 25

    def __init__(self, record: ImageRecord, params: DetectionParams):
        self.record = record                    # ImageRecord from the DataManager
        self.image: np.ndarray = record.image
        self.mask: np.ndarray = record.mask
        self.px_per_mm: float = record.px_per_mm
        self._params = params
        self._colonies = ColonySet.empty()
        self._baseline = ColonySet.empty()      # latest whole-image automatic result
        self.loaded_revision: int | None = None # revision of the saved result this session started from
        self._applied: list[SnapshotCommand] = []   # every edit in effect, oldest first (the edit log)
        self._undo: list[SnapshotCommand] = []
        self._redo: list[SnapshotCommand] = []
        self._next_id = 1
        self._listeners: list[Listener] = []
        self.start_source = ""                  # where the starting settings came from (set by whoever builds the session)
        self.last_compute_ms = 0.0              # time the last detection (whole image or region) took

    # ----- read access --------------------------------------------------
    @property
    def params(self) -> DetectionParams:
        return self._params

    @property
    def colonies(self) -> ColonySet:
        return self._colonies

    @property
    def initial_state(self) -> ColonySet:
        """The baseline: automatic result the manual edits start from (InitialState)."""
        return self._baseline

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_depth(self) -> int:
        return len(self._undo)

    @property
    def last_edit(self) -> EditRecord | None:
        """The most recent edit in effect, as the edit log will store it."""
        if not self._applied:
            return None
        command = self._applied[-1]
        return EditRecord(command.kind, command.count, command.changed_ids(), command.after_params)

    # ----- observers ----------------------------------------------------
    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def _notify(self, event: str) -> None:
        for listener in self._listeners:
            listener(event)

    # ----- state changes (only commands and the methods below use these) -
    def set_state(self, colonies: ColonySet, params: DetectionParams, baseline: ColonySet) -> None:
        self._colonies = colonies
        self._params = params
        self._baseline = baseline

    def _assign_ids(self, colonies: ColonySet) -> ColonySet:
        numbered = colonies.with_ids(self._next_id)
        self._next_id += len(colonies)
        return numbered

    def execute(self, command: SnapshotCommand) -> None:
        command.apply(self)
        self._applied.append(command)
        self._undo.append(command)
        del self._undo[:-self.MAX_UNDO_HISTORY]
        self._redo.clear()
        self._notify("changed")

    def undo(self) -> SnapshotCommand | None:
        if not self._undo:
            return None
        command = self._undo.pop()
        self._applied.pop()
        command.revert(self)
        self._redo.append(command)
        self._notify("changed")
        return command

    def redo(self) -> SnapshotCommand | None:
        if not self._redo:
            return None
        command = self._redo.pop()
        command.apply(self)
        self._applied.append(command)
        self._undo.append(command)
        self._notify("changed")
        return command

    # ----- operations the controller requests ---------------------------
    def initialize_detection(self) -> int:
        """First automatic detection at the starting parameters (not undoable)."""
        detected = self._assign_ids(self._detect(self._params))
        self.set_state(detected, self._params, detected)
        self._notify("changed")
        return len(detected)

    def load_saved(self, saved: SavedDetection) -> int:
        """Start from the committed result (not undoable); new colonies get ids above the saved ones."""
        self.set_state(saved.colonies, saved.params, saved.baseline)
        self.loaded_revision = saved.revision
        known = np.concatenate([saved.colonies.ids, saved.baseline.ids])
        self._next_id = int(known.max()) + 1 if len(known) else 1
        self._notify("changed")
        return len(saved.colonies)

    def mark_committed(self, revision: int) -> None:
        """The DataManager stored the result: it becomes the new starting point.
        History restarts, so the next commit logs only the edits made after this one."""
        self.loaded_revision = revision
        self._applied.clear()
        self._undo.clear()
        self._redo.clear()
        self._notify("changed")

    def result(self) -> DetectionResult:
        """The working copy as a result for the DataManager ("Finish image")."""
        edits = tuple(EditRecord(c.kind, c.count, c.changed_ids(), c.after_params) for c in self._applied)
        return DetectionResult(self.record.index_id, self._params, self._colonies, self._baseline,
                               edits, detection_version(self._params))

    def detect_colonies(self, params: DetectionParams) -> int:
        """Rerun detection at new parameters (DetectColonies). Undoable."""
        detected = self._assign_ids(self._detect(params))
        self.execute(make_detection_command(self, params, detected))
        return len(detected)

    def preview_region_detection(self, params: DetectionParams, roi) -> ColonySet:
        """Detection inside a rectangle for Add Many Colonies. Changes nothing."""
        candidates = self._detect(params, roi)
        return filter_valid_colonies(candidates, self._colonies)

    def _detect(self, params: DetectionParams, roi=None) -> ColonySet:
        """perform_detection on this image, timed (last_compute_ms)."""
        started = time.perf_counter()
        found = perform_detection(self.image, params, roi=roi, region_mask=self.mask)
        self.last_compute_ms = 1000 * (time.perf_counter() - started)
        return found

    def delete_colonies(self, colony_ids) -> int:
        command = make_deletion_command(self, colony_ids)
        if command.count:
            self.execute(command)
        return command.count

    def estimated_parameters(self) -> DetectionParams:
        """Settings estimated from this image (changes nothing; detect_colonies applies them)."""
        return estimate_parameters(self.image, self.mask, self._params)

    def add_colonies(self, new_colonies: ColonySet, skip_overlapping: bool = True) -> int:
        """Add colonies. Detected candidates that duplicate existing colonies are skipped; hand-drawn
        colonies (skip_overlapping=False) are added as drawn, even where they overlap others."""
        valid = filter_valid_colonies(new_colonies, self._colonies) if skip_overlapping else new_colonies
        if len(valid) == 0:
            return 0
        self.execute(make_addition_command(self, self._assign_ids(valid)))
        return len(valid)

    @staticmethod
    def circles_to_colonies(circles: list[tuple[float, float, float]]) -> ColonySet:
        """Hand-drawn circles (x, y, radius) as manual colonies."""
        if not circles:
            return ColonySet.empty()
        array = np.asarray(circles, dtype=float)
        return ColonySet.from_columns(array[:, :2], array[:, 2], kinds=[KIND_MANUAL] * len(array))


def session_from_packet(packet) -> DetectionSession:
    """A session on a checked-out index: its saved result, or a fresh automatic detection.

    A new index starts from the settings of the last image finished in the project; before
    any image is finished, a project that asks for it estimates them from this image."""
    if packet.saved:
        session = DetectionSession(packet.record, packet.saved.params)
        session.load_saved(packet.saved)
        session.start_source = "saved"
        return session
    params = packet.defaults
    if packet.estimate:
        params = estimate_parameters(packet.record.image, packet.record.mask, params)
    session = DetectionSession(packet.record, params)
    session.start_source = packet.start_source or ("estimate" if packet.estimate else "last image")
    session.initialize_detection()
    return session
