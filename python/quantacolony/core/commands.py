"""Edits as command objects: each one knows how to apply and revert itself.

Every command stores the colony set, parameters and baseline from before
and after the edit. ColonySet and DetectionParams are immutable, so storing
them costs no copying, and undo is exact. The baseline is the automatic
result the manual edits start from; a whole-image detection replaces it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .colony_set import ColonySet
from .params import DetectionParams

DETECTION = "Detection"
DELETION = "Deletion"
ADDITION = "Addition"


@dataclass(frozen=True)
class SnapshotCommand:
    """An edit described by the state before and after it."""

    kind: str                       # DETECTION, DELETION or ADDITION
    count: int                      # colonies detected, deleted or added
    before_colonies: ColonySet
    after_colonies: ColonySet
    before_params: DetectionParams
    after_params: DetectionParams
    before_baseline: ColonySet
    after_baseline: ColonySet

    def apply(self, session) -> None:
        session.set_state(self.after_colonies, self.after_params, self.after_baseline)

    def revert(self, session) -> None:
        session.set_state(self.before_colonies, self.before_params, self.before_baseline)

    def changed_ids(self) -> tuple[int, ...]:
        """Colonies this edit detected, added or removed."""
        before, after = set(self.before_colonies.ids.tolist()), set(self.after_colonies.ids.tolist())
        if self.kind == DELETION:
            return tuple(sorted(before - after))
        if self.kind == ADDITION:
            return tuple(sorted(after - before))
        return tuple(sorted(after))


def make_detection_command(session, params: DetectionParams, detected: ColonySet) -> SnapshotCommand:
    """New automatic result at new parameters. Replaces every colony and the baseline, as in MATLAB
    (captureInitialState runs after each whole-image detection)."""
    return SnapshotCommand(DETECTION, len(detected), session.colonies, detected,
                           session.params, params, session.initial_state, detected)


def make_deletion_command(session, colony_ids) -> SnapshotCommand:
    """Remove the listed colonies (DeletionProcessing)."""
    after = session.colonies.without(colony_ids)
    return SnapshotCommand(DELETION, len(session.colonies) - len(after), session.colonies, after,
                           session.params, session.params, session.initial_state, session.initial_state)


def make_addition_command(session, new_colonies: ColonySet) -> SnapshotCommand:
    """Append colonies drawn by hand or found in a region (AddSelectedColoniesButtonPushed)."""
    after = session.colonies.concat(new_colonies)
    return SnapshotCommand(ADDITION, len(new_colonies), session.colonies, after,
                           session.params, session.params, session.initial_state, session.initial_state)
