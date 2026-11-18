"""Colony Detection's committed results: settings, colonies (final and baseline) and the edit log.

Colonies are stored in photo pixels, so a colony keeps its place on the plate
when a crop region changes.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import numpy as np

from ... import __version__
from ...core.colony_set import ColonySet
from ...core.params import DetectionParams
from ...core.record import SavedDetection
from ..codec import array_to_blob, blob_to_array, params_from_json, params_to_json
from .project_file import ProjectFile, now

REGION_CHANGED = "The crop region changed after these colonies were saved"
REGION_REMOVED = "The crop region was removed after these colonies were saved"


class Detections:
    def __init__(self, file: ProjectFile):
        self.file = file

    @property
    def db(self):
        return self.file.connection

    # ----- reading ----------------------------------------------------------
    def load(self, index_id: int) -> SavedDetection | None:
        """Committed result in photo coordinates, or None."""
        detection = self.db.execute("SELECT * FROM detection WHERE index_id = ?", (index_id,)).fetchone()
        if detection is None:
            return None
        rows = self.db.execute("SELECT * FROM colonies WHERE index_id = ? ORDER BY colony_id", (index_id,)).fetchall()
        params = DetectionParams(level=detection["level"], sense=detection["sense"], rmin=detection["rmin"],
                                 rmax=detection["rmax"], maxpix=detection["maxpix"],
                                 detection_type=detection["detection_type"], split=bool(detection["split"]),
                                 split_elongation=detection["split_elongation"], split_pinch=detection["split_pinch"])
        return SavedDetection(params, self._colonies([r for r in rows if r["in_final"]]),
                              self._colonies([r for r in rows if r["in_baseline"]]),
                              detection["revision"], detection["detection_version"],
                              bool(detection["needs_review"]), detection["review_reason"])

    @staticmethod
    def _colonies(rows) -> ColonySet:
        if not rows:
            return ColonySet.empty()
        return ColonySet.from_columns(
            centers=[(r["x"], r["y"]) for r in rows], radii=[r["radius_px"] for r in rows],
            boundaries=[blob_to_array(r["boundary"]) for r in rows], kinds=[r["kind"] for r in rows],
            eccentricities=[np.nan if r["eccentricity"] is None else r["eccentricity"] for r in rows],
            ids=[r["colony_id"] for r in rows])

    def states(self) -> dict[int, tuple[int, bool]]:
        """index id -> (revision, needs review) for every committed index."""
        return {r["index_id"]: (r["revision"], bool(r["needs_review"]))
                for r in self.db.execute("SELECT index_id, revision, needs_review FROM detection")}

    def provenance(self, index_id: int) -> tuple[int, int | str]:
        """(revision, detection version) of an index; (0, '') before its first commit."""
        row = self.db.execute("SELECT revision, detection_version FROM detection WHERE index_id = ?",
                              (index_id,)).fetchone()
        return (row["revision"], row["detection_version"]) if row else (0, "")

    def counts(self) -> tuple[int, int, int]:
        """(indices detected, indices to review, colonies counted), over indices that still have a region."""
        detected, review = self.db.execute(
            "SELECT COUNT(*), COALESCE(SUM(d.needs_review), 0) FROM detection d "
            "JOIN assignments a USING (index_id)").fetchone()
        colonies = self.db.execute("SELECT COUNT(*) FROM colonies c JOIN assignments a USING (index_id) "
                                   "WHERE c.in_final = 1").fetchone()[0]
        return detected, int(review), colonies

    def edit_log(self, index_id: int) -> list[dict]:
        return [{"revision": r["revision"], "step": r["step"], "kind": r["kind"], "count": r["count"],
                 "colony_ids": json.loads(r["colony_ids"]), "params": asdict(params_from_json(r["params"]))}
                for r in self.db.execute("SELECT * FROM edit_log WHERE index_id = ? ORDER BY revision, step",
                                         (index_id,))]

    # ----- writing (inside a transaction) -------------------------------------
    def write(self, index_id: int, params: DetectionParams, final: ColonySet, baseline: ColonySet,
              edits, detection_version: int) -> int:
        """Store a finished result (photo coordinates) as the new truth. Returns the new revision."""
        self.file.require_transaction()
        rows: dict[int, list] = {}
        for colonies, column in ((baseline, 8), (final, 9)):       # final geometry wins for shared ids
            for i in range(len(colonies)):
                colony_id = int(colonies.ids[i])
                flags = rows[colony_id][8:10] if colony_id in rows else [0, 0]
                flags[column - 8] = 1
                eccentricity = float(colonies.eccentricities[i])
                rows[colony_id] = [index_id, colony_id, float(colonies.centers[i, 0]), float(colonies.centers[i, 1]),
                                   float(colonies.radii[i]), None if np.isnan(eccentricity) else eccentricity,
                                   colonies.kinds[i], array_to_blob(colonies.boundaries[i]), *flags]
        previous = self.db.execute("SELECT revision FROM detection WHERE index_id = ?", (index_id,)).fetchone()
        revision = (previous["revision"] if previous else 0) + 1
        p = params
        self.db.execute("INSERT OR REPLACE INTO detection (index_id, revision, committed_at, level, sense, rmin, rmax, "
                        "maxpix, detection_type, split, split_elongation, split_pinch, detection_version, "
                        "software_version, needs_review, review_reason) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '')",
                        (index_id, revision, now(), p.level, p.sense, p.rmin, p.rmax, p.maxpix, p.detection_type,
                         int(bool(p.split)), float(p.split_elongation), float(p.split_pinch), detection_version,
                         __version__))
        self.db.execute("DELETE FROM colonies WHERE index_id = ?", (index_id,))
        self.db.executemany("INSERT INTO colonies (index_id, colony_id, x, y, radius_px, eccentricity, kind, boundary, "
                            "in_baseline, in_final) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", list(rows.values()))
        self.db.executemany("INSERT INTO edit_log (index_id, revision, step, kind, count, colony_ids, params) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            [(index_id, revision, step, edit.kind, edit.count, json.dumps(list(edit.colony_ids)),
                              params_to_json(edit.params)) for step, edit in enumerate(edits)])
        return revision

    def flag_review(self, index_id: int, reason: str) -> bool:
        """Mark a committed index for review. Returns False when the index has no committed result."""
        self.file.require_transaction()
        return bool(self.db.execute("UPDATE detection SET needs_review = 1, review_reason = ? WHERE index_id = ?",
                                    (reason, index_id)).rowcount)
