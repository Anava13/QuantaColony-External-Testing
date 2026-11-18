"""The user study in a browser (Pyodide): Colony Detection's rules without Qt, called by the web page.

The page draws and handles the mouse; this module holds every decision Colony Detection makes, with the
desktop's own code: the session (core/session.py: detection, deletion, addition, undo), the drawn circles
(core/drawing.py), the recorder (core/benchmark.py) and the project file (data/, study/project.py). The
rules follow gui/controller.py method for method (named in each docstring), so a plate measured here is
measured as on the desktop, and the results file is the same .qcproj with the same benchmark tables.

    WebStudy.start          form sent: the participant's project, the plate order
    WebStudy.plate          the plate shown: label, number, image size
    WebStudy.view           everything the canvas and the controls show
    WebStudy.<edits>        modes, settings, selections, circles, regions, undo, finish
    WebStudy.tutorial       the practice plate's guide (study/guide.py STEPS)
    WebStudy.unadded        drawn circles or region outlines not added yet (checked before Finish image)
    WebStudy.review         every scored plate as finished, for the participant to confirm or reopen
    WebStudy.results        the zip to upload: <name>.qcproj, the first-pass snapshot, participant.json

The study record keeps every save in order ("processing": plate, its position, first pass or review,
seconds open), so accuracy can be compared across a participant's positions in the sequence.

Every method returns plain values (dicts, lists, numbers, bytes) for the page.
"""

from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ..core.benchmark import POINTER_EVERY_MS, SETTING_EVERY_MS, DetectionRecorder, params_data, reference_start
from ..core.autoparams import maxpix_for
from ..core.colony_set import ColonySet
from ..core.drawing import DrawnCircle, drawn_to_colonies, hit_test
from ..core.messages import compose_message
from ..core.params import (DetectionParams, PARAM_SPECS, level_from_threshold, threshold_from_level,
                           validate_detection_parameters)
from ..core.session import DetectionSession, session_from_packet
from . import guide as gd
from . import model as sm
from . import project as sp
from . import score as sc

SELECTION_TOLERANCE = 2.0          # gui/controller.py: click within 2 radii of a center
MODES = ("ModifyGlobalParameters", "DeleteIndividualColonies", "DeleteManyColonies", "AddIndividualColonies",
         "AddManyColonies")
REGION_MODE = "AddManyColonies"    # settings apply inside the region only


class WebStudy:
    def __init__(self, bundle_dir: str, work_dir: str):
        self.bundle = sm.load_bundle(Path(bundle_dir))
        self.work = Path(work_dir)
        self.labels = np.asarray(Image.open(self.bundle.labels_path())).astype(int)
        self.kinds = np.asarray(Image.open(self.bundle.artifacts_path())) if self.bundle.artifacts_path().exists() \
            else None
        rows = list(csv.DictReader(open(self.bundle.truth_path(), encoding="utf-8"))) \
            if self.bundle.truth_path().exists() else []
        self.truth = {int(r["colony_id"]): (float(r["x_px"]), float(r["y_px"]), float(r["true_radius_px"]))
                      for r in rows}
        self.required = sc.required_colonies(rows)
        self.feedback = None                       # the last failed training check: flags and missed colonies
        self.attempts = 0
        self.manager = None
        self.participant = sm.Participant()
        self.session = None
        self.recorder = None
        self.mode = MODES[0]
        self.selected_ids: set[int] = set()
        self.selection_rect = None
        self.pending: list[DrawnCircle] = []
        self.contour = False
        self.roi = None
        self.roi_params = None
        self.candidates = None
        self.step = 0                              # the guide's current step (guide.STEPS)
        self.estimate_pressed = False              # Estimate settings pressed, even when nothing changed
        self.skipped: list[str] = []
        self.practice_done = False
        self.reviewing = False                     # a plate reopened from the review
        self.opened_at = 0.0                       # when the current plate opened (seconds, monotonic)
        self.status = ""

    # ===== what the reading pages show =================================================
    @staticmethod
    def questions() -> list:
        """The form: [key, question, [choices]] (study/model.py QUESTIONS)."""
        return [[key, question, list(choices)] for key, question, choices in sm.QUESTIONS]

    def concepts(self) -> list:
        """The concept figures: [path, title, caption]."""
        return [[str(self.bundle.folder / "concepts" / c.file), c.title, c.caption] for c in self.bundle.concepts]

    @staticmethod
    def goal() -> str:
        return sm.GOAL

    # ===== the study ==================================================================
    def start(self, name: str, answers: dict, seed: int) -> dict:
        """Form sent: make the participant's project and open the practice plate."""
        self.participant = sm.Participant(str(name), {str(k): str(v) for k, v in dict(answers).items()})
        problems = self.participant.problems()
        if problems:
            return {"ok": False, "problems": problems}
        order = list(self.bundle.order) or sm.presentation_order([p.name for p in self.bundle.scored], int(seed))
        self.manager = sp.open_manager(sp.work_folder(self.work))
        sp.create_project(self.manager, self.bundle, self.participant, order, int(seed))
        self.order = order
        return {"ok": True, "order": order}

    def mark(self, stage: str) -> None:
        """A stage time in the study record (concepts_read, info_opened, ...)."""
        if self.manager is not None:
            sp.stamp(self.manager, str(stage))

    def info_opened(self) -> None:
        """'What is this for?' opened: counted in the study record."""
        if self.manager is not None:
            record = self.manager.study_record()
            record["info_opened"] = int(record.get("info_opened", 0)) + 1
            self.manager.save_study_record(record)

    def _ordered_indices(self) -> list:
        """(index id, level value, revision) for every plate, in the order the participant sees them."""
        position = {value: k for k, (value, _) in enumerate(sm.plate_levels(self.order))}
        found = [(i.index_id, i.labels[0].value, i.revision) for i in self.manager.project_info().indices
                 if i.has_region]
        return sorted(found, key=lambda item: position.get(item[1], len(position)))

    def open_next(self) -> bool:
        """The first plate not saved yet (False when every plate is saved)."""
        waiting = next(((i, value) for i, value, revision in self._ordered_indices() if not revision), None)
        if waiting is None:
            return False
        packet = self.manager.checkout_image(waiting[0])
        if waiting[1] == sm.PRACTICE:                  # the guide starts from poorly tuned settings
            session = DetectionSession(packet.record, gd.POOR)
            session.start_source = "tutorial"
            session.initialize_detection()
        else:
            session = session_from_packet(packet)
        self._bind(session)
        return True

    def _bind(self, session) -> None:
        """gui/controller.py _bind and _start_recording."""
        self.session = session
        self._clear_pending()
        record = session.record
        initial = reference_start(record.image, record.mask, self.manager.detection_defaults())
        self.recorder = DetectionRecorder(record.index_id, record.image.shape[:2], int(record.mask.sum()),
                                          record.px_per_mm, session.start_source, session.params, initial)
        if not session.loaded_revision:
            self._record_detection("initial")
        self.mode = MODES[0]
        self.opened_at = time.monotonic()
        self._record("mode", mode=self.mode)
        self._say("DetectionResults", (len(session.colonies), session.params))

    def plate(self) -> dict:
        record = self.session.record
        value = record.labels[0].value
        number = self.order.index(value) + 1 if value in self.order else 0
        rows, cols = record.image.shape[:2]
        return {"value": value, "label": record.labels[0].display, "number": number, "total": len(self.order),
                "practice": value == sm.PRACTICE, "reviewing": self.reviewing, "width": cols, "height": rows}

    def image_rgba(self) -> bytes:
        """The plate's crop as RGBA bytes (row-major), black outside the region."""
        image = self.session.image
        rgba = np.concatenate([image[..., :3], np.full(image.shape[:2] + (1,), 255, np.uint8)], axis=2)
        return np.ascontiguousarray(rgba).tobytes()

    # ===== what the page shows ========================================================
    def view(self) -> dict:
        s = self.session
        colonies = []
        for k in range(len(s.colonies)):
            boundary = s.colonies.boundaries[k]
            colonies.append({"id": int(s.colonies.ids[k]), "x": float(s.colonies.centers[k, 0]),
                             "y": float(s.colonies.centers[k, 1]), "r": float(s.colonies.radii[k]),
                             "kind": str(s.colonies.kinds[k]),
                             "outline": [] if boundary is None else np.round(boundary, 1).ravel().tolist()})
        candidates = []
        if self.candidates is not None:
            for k in range(len(self.candidates)):
                boundary = self.candidates.boundaries[k]
                candidates.append({"x": float(self.candidates.centers[k, 0]), "y": float(self.candidates.centers[k, 1]),
                                   "r": float(self.candidates.radii[k]),
                                   "outline": [] if boundary is None else np.round(boundary, 1).ravel().tolist()})
        pending = []
        for c in self.pending:
            outline = c.contour.outline if c.contour is not None and c.contour.found else None
            pending.append({"x": c.x, "y": c.y, "r": c.r,
                            "outline": [] if outline is None else np.round(outline, 1).ravel().tolist()})
        params = self.roi_params if (self.mode == REGION_MODE and self.roi_params is not None) else s.params
        color, threshold = threshold_from_level(params.level)
        feedback = self.feedback if self._is_practice() and not self.practice_done else None
        present = {c["id"] for c in colonies}
        flags = {str(i): v for i, v in (feedback or {}).get("flags", {}).items() if i in present}
        return {"flags": flags, "missed": (feedback or {}).get("missed", []),
                "colonies": colonies, "selected": sorted(self.selected_ids), "pending": pending,
                "candidates": candidates, "roi": list(self.roi) if self.roi else None,
                "rect": list(self.selection_rect) if self.selection_rect else None, "mode": self.mode,
                "contour": self.contour, "count": len(s.colonies),
                "added": int(sum(1 for k in s.colonies.kinds if k == "manual")),
                "undo": s.undo_depth(), "redo": int(s.can_redo()), "status": self.status,
                "settings": {"color": color, "threshold": threshold, "sense": params.sense, "rmin": params.rmin,
                             "rmax": params.rmax, "maxpix": params.maxpix, "shape": params.detection_type,
                             "split": bool(params.split), "split_elongation": params.split_elongation,
                             "split_pinch": params.split_pinch}}

    def specs(self) -> dict:
        """Limits of the settings controls (core/params.py)."""
        return {k: {"label": v.label, "min": v.minimum, "max": v.maximum, "step": v.step, "decimals": v.decimals}
                for k, v in PARAM_SPECS.items()}

    def _say(self, key: str, value=None) -> None:
        message = compose_message(key, self.session.record.display_label if self.session else "", value)
        self.status = message.text + (f": {message.detail}" if message.kind and message.detail else "")

    # ===== recording ==================================================================
    def _record(self, event: str, every_ms: int = 0, **data) -> None:
        if self.recorder is not None:
            self.recorder.record(event, every_ms, **data)

    def _record_detection(self, trigger: str) -> None:
        s = self.session
        self._record("detection", trigger=trigger, params=params_data(s.params),
                     compute_ms=round(s.last_compute_ms, 1), colonies=len(s.colonies))

    def _record_edit(self) -> None:
        edit = self.session.last_edit
        if edit is not None:
            self._record("edit", kind=edit.kind, count=edit.count, colony_ids=list(edit.colony_ids), mode=self.mode)

    def pointer(self) -> None:
        self._record("pointer", POINTER_EVERY_MS)

    def focus(self, active: bool) -> None:
        """The page became visible or hidden (another tab or program in front)."""
        self._record("focus", active=bool(active))

    # ===== modes ======================================================================
    def set_mode(self, mode: str) -> None:
        """gui/controller.py select_dropdown_option."""
        if mode not in MODES or mode == self.mode:
            return
        self._clear_pending()
        self.mode = mode
        self._record("mode", mode=mode)

    def _clear_pending(self) -> None:
        self.selected_ids.clear()
        self.selection_rect = None
        self.pending.clear()
        self.roi = None
        self.roi_params = None
        self.candidates = None

    def clear(self) -> None:
        """Esc (clear_selection)."""
        self.selected_ids.clear()
        self.selection_rect = None
        if self.roi is not None:
            self.roi = None
            self.roi_params = None
            self.candidates = None

    # ===== settings ===================================================================
    def setting_changing(self, field: str) -> None:
        """A control is being changed (spinner_value_changing's record); the page detects once it settles."""
        self._record("setting", SETTING_EVERY_MS, field=str(field))

    def apply_settings(self, settings: dict, trigger: str = "settings") -> dict:
        """The controls' values, once they settle: detect the whole plate again, or (Detect in a region)
        preview the region (handle_backup_and_detection, update_region_detection)."""
        s = dict(settings)
        if str(s["shape"]) != "Circular":              # Largest object is a Circular control; it follows Max radius
            s["maxpix"] = maxpix_for(float(s["rmax"]))
        params = DetectionParams(level=level_from_threshold(s["color"], float(s["threshold"])), sense=float(s["sense"]),
                                 rmin=float(s["rmin"]), rmax=float(s["rmax"]), maxpix=float(s["maxpix"]),
                                 detection_type=str(s["shape"]), split=bool(s["split"]),
                                 split_elongation=float(s["split_elongation"]), split_pinch=float(s["split_pinch"]))
        if self.mode == REGION_MODE:
            self.roi_params = params
            self._update_region()
            return {"ok": True}
        if params == self.session.params:
            return {"ok": True}
        error = validate_detection_parameters(params)
        if error:
            self._say("ParameterError", error)
            return {"ok": False, "error": error}
        self._clear_pending()
        count = self.session.detect_colonies(params)
        self._record_detection(trigger)
        self._record_edit()
        self._say("DetectionResults", (count, params))
        return {"ok": True}

    def estimate(self) -> None:
        """Estimate settings (estimate_settings)."""
        self.estimate_pressed = True
        params = self.session.estimated_parameters()
        if params == self.session.params:
            self._say("SettingsAlreadyEstimated")
            return
        self._clear_pending()
        count = self.session.detect_colonies(params)
        self._record_detection("estimate")
        self._record_edit()
        self._say("SettingsEstimated", (count, params))

    # ===== deleting ===================================================================
    def delete_click(self, x: float, y: float) -> None:
        """handle_delete_click."""
        colonies = self.session.colonies
        index, distance = colonies.nearest((x, y))
        if index < 0 or distance > SELECTION_TOLERANCE * colonies.radii[index]:
            self._say("ClickNotCloseEnough")
            return
        colony_id = int(colonies.ids[index])
        if colony_id in self.selected_ids:
            self.selected_ids.discard(colony_id)
            self._say("ColonyDeselected", colonies.centers[index])
        else:
            self.selected_ids.add(colony_id)
            self._say("ColonySelected", colonies.centers[index])
        self._record("select", how="click", count=len(self.selected_ids))

    def select_rect(self, x0: float, y0: float, x1: float, y1: float) -> None:
        """highlight_colonies_in_rect."""
        ids = self.session.colonies.ids_in_rect(x0, y0, x1, y1)
        self.selected_ids = {int(i) for i in ids}
        self.selection_rect = (x0, y0, x1, y1)
        self._record("select", how="rectangle", count=len(ids))
        self._say("RectangleSelectionCount", len(ids))

    def delete_selected(self) -> None:
        """delete_selected_colonies_button_pushed."""
        if not self.selected_ids:
            self._say("NoColoniesSelected")
            return
        deleted = self.session.delete_colonies(sorted(self.selected_ids))
        if deleted:
            self._record_edit()
        self.selected_ids.clear()
        self.selection_rect = None
        self._say("ColoniesDeleted", (deleted, len(self.session.colonies)))

    # ===== drawing circles ============================================================
    def hit_circle(self, x: float, y: float, tolerance: float) -> list:
        """[index, part] of a drawn circle under the pointer ('inside' / 'edge'), or [-1, '']."""
        index, part = hit_test(self.pending, x, y, tolerance)
        return [int(index), str(part or "")]

    def add_circle(self, x: float, y: float, r: float) -> int:
        """add_new_circle."""
        self.pending.append(DrawnCircle(float(x), float(y), float(r)))
        self._record("draw", circles=len(self.pending))
        return len(self.pending) - 1

    def update_circle(self, index: int, x: float, y: float, r: float, final: bool) -> None:
        """update_circle."""
        circle = self.pending[index]
        circle = circle.moved_to(x, y) if (x, y) != (circle.x, circle.y) else circle
        circle = circle.resized_to(r) if r != circle.r else circle.without_contour()
        if final and self.contour:
            circle = self._contour(circle)
        if final:
            snapped = circle.contour is not None and circle.contour.found
            self._record("draw", circles=len(self.pending), snapped=bool(snapped))
        self.pending[index] = circle

    def remove_circle(self, index: int, how: str = "right") -> None:
        """A drawn circle removed: a right-click on it ('right'), or a click too short to draw ('click')."""
        del self.pending[index]
        self._record("draw_removed", how=str(how), circles=len(self.pending))
        if how == "right":
            self._say("CircleRemoved")

    def set_contour(self, enabled: bool) -> None:
        """Snap to colony edge (controller_contour_circle)."""
        self.contour = bool(enabled)
        self._say("ContourOn" if enabled else "ContourOff")
        self.pending = [self._contour(c) if enabled else c.without_contour() for c in self.pending]

    def _contour(self, circle: DrawnCircle) -> DrawnCircle:
        fitted = circle.with_contour(self.session.image, self.session.mask)
        if not fitted.contour.found:
            self._say("ContourFailed", fitted.contour.reason)
        return fitted

    # ===== detect in a region =========================================================
    def start_region(self, x0: float, y0: float, x1: float, y1: float) -> None:
        """start_region_detection."""
        self.roi = (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
        self.roi_params = self.roi_params or self.session.params
        self._update_region()

    def _update_region(self) -> None:
        if self.roi is None:
            return
        self.candidates = self.session.preview_region_detection(self.roi_params, self.roi)
        self._record("region_preview", roi=[round(float(v), 1) for v in self.roi], params=params_data(self.roi_params),
                     compute_ms=round(self.session.last_compute_ms, 1), candidates=len(self.candidates))
        self._say("RegionCandidates", len(self.candidates))

    def _adding_blocked(self) -> bool:
        """The Snap step holds Add colonies until its right-click task is done, so a participant who adds the
        snapped circle early does not lose the step."""
        if not self._is_practice() or self.practice_done or gd.STEPS[self.step].key != "add_tools":
            return False
        events = self.recorder.log.events if self.recorder is not None else []
        return not any(e.kind == "draw_removed" and e.data.get("how") == "right" for e in events)

    def add_pending(self) -> None:
        """Add colonies: the drawn circles, or the region's candidates (add_selected_colonies_button_pushed)."""
        if self.mode != REGION_MODE and self._adding_blocked():
            self.status = "Right-click the circle to remove it first; Add colonies comes after."
            return
        if self.mode == REGION_MODE:
            new = self.candidates if self.candidates is not None else ColonySet.empty()
        else:
            new = drawn_to_colonies(self.pending)
        if len(new) == 0:
            self._say("NothingToAdd")
            return
        added = self.session.add_colonies(new, skip_overlapping=self.mode == REGION_MODE)
        if added:
            self._record_edit()
        self.pending.clear()
        self.candidates = None
        self.roi = None
        self._say("ColoniesAdded", (added, len(new) - added, len(self.session.colonies)))

    def unadded(self) -> dict:
        """Outlines drawn or found but not added to the colonies: {drawn, region}."""
        return {"drawn": len(self.pending), "region": 0 if self.candidates is None else len(self.candidates)}

    def resolve_unadded(self, choice: str, before: str) -> None:
        """The participant's answer to the not-added warning before Finish image or a tool change
        (before: 'finish' / 'mode'): 'add' adds them, 'discard' drops them; recorded either way."""
        waiting = self.unadded()
        self._record("unadded", choice=str(choice), before=str(before), **waiting)
        if choice == "add":
            self.add_pending()
        elif choice == "discard":
            self._clear_pending()

    # ===== undo, redo, finish =========================================================
    def undo(self) -> None:
        self._clear_pending()
        command = self.session.undo()
        if command is None:
            self._say("NoUndoActions")
            return
        self._record("undo", kind=command.kind, count=command.count)
        self._say("Undo", f"{command.kind.lower()} of {command.count}")

    def redo(self) -> None:
        self._clear_pending()
        command = self.session.redo()
        if command is None:
            self._say("NoRedoActions")
            return
        self._record("redo", kind=command.kind, count=command.count)
        self._say("Redo", f"{command.kind.lower()} of {command.count}")

    def finish_image(self) -> dict:
        """Finish image (im_satisfied_pushed): save the plate with its record, open the next one. On the
        practice plate the outlines are checked first (study/score.py); a plate that fails the check stays
        open with feedback: {done: False, training: summary}."""
        check = None
        if self._is_practice() and not self.practice_done and self.truth:
            check = self._training_check()
            if not check["passed"]:
                return {"done": False, "training": check}
        benchmark = self.recorder.finish(self.session.params) if self.recorder is not None else None
        revision = self.manager.commit_detection(self.session.result(), benchmark)
        self.session.mark_committed(revision)
        value = self.session.record.labels[0].value
        self._log_save(value)
        if value == sm.PRACTICE and not self.practice_done:
            self.practice_done = True
            sp.stamp(self.manager, "practice_saved", tutorial_skipped=list(self.skipped))
        if self.reviewing:                             # back to the review
            self.reviewing = False
            return {"done": True}
        more = self.open_next()
        if not more and not sp.first_pass_path(self.manager).exists():
            self.manager.copy_open_project(sp.first_pass_path(self.manager))
            sp.stamp(self.manager, "first_pass_done")
        return {"done": not more, "training": check} if check else {"done": not more}

    def _log_save(self, value: str) -> None:
        """One entry per save in the study record's "processing" list, in the order saved."""
        record = self.manager.study_record()
        stage = "practice" if value == sm.PRACTICE else "review" if self.reviewing else "first"
        record.setdefault("processing", []).append({
            "plate": value, "position": self.order.index(value) + 1 if value in self.order else 0,
            "pass": stage, "saved": sp.now(), "seconds_open": round(time.monotonic() - self.opened_at, 1),
            "colonies": len(self.session.colonies)})
        self.manager.save_study_record(record)

    # ===== the review before sending =================================================
    def review(self) -> list:
        """Every scored plate as saved, in the participant's order: [{value, number, count, image (JPEG)}]."""
        record = sp.stamp(self.manager, "review_opened")
        record["review_views"] = int(record.get("review_views", 0)) + 1
        self.manager.save_study_record(record)
        plates = []
        for index_id, value, _ in self._ordered_indices():
            if value == sm.PRACTICE:
                continue
            packet = self.manager.checkout_image(index_id)
            colonies = packet.saved.colonies if packet.saved is not None else ColonySet.empty()
            plates.append({"value": value, "number": self.order.index(value) + 1, "count": len(colonies),
                           "image": review_jpeg(packet.record.image, colonies)})
        return plates

    def reopen(self, value: str) -> None:
        """Open a saved plate again from the review; Finish image saves it and returns to the review."""
        index_id = next(i for i, v, _ in self._ordered_indices() if v == value)
        self._bind(session_from_packet(self.manager.checkout_image(index_id)))
        self.reviewing = True
        record = self.manager.study_record()
        record.setdefault("reopened", []).append({"plate": value, "time": sp.now()})
        self.manager.save_study_record(record)

    def confirm_review(self) -> None:
        sp.stamp(self.manager, "review_confirmed")

    # ===== the note before the scored plates =========================================
    @staticmethod
    def start_note() -> list:
        return list(sm.START_NOTE)

    def note_accepted(self) -> None:
        sp.stamp(self.manager, "start_note_accepted")

    # ===== the training check (study/score.py) ======================================
    def _is_practice(self) -> bool:
        return self.session is not None and self.session.record.labels[0].value == sm.PRACTICE

    def _training_check(self) -> dict:
        """Score the practice plate's outlines; record the attempt; keep the feedback when it fails."""
        x0, y0 = sp.crop_offset(self.bundle.practice, self.labels.shape)
        colonies = self.session.colonies
        result = sc.score_plate(colonies.translated(x0, y0), self.labels, self.kinds, self.required)
        self.attempts += 1
        summary = {"attempt": self.attempts, "passed": result.passed, "found": result.required_found,
                   "required": result.required_total, "found_share": round(result.found_share, 4),
                   "correct": result.correct, "outlines": result.outlines,
                   "correct_share": round(result.correct_share, 4), "found_min": sc.FOUND_MIN,
                   "correct_min": sc.CORRECT_MIN, "false": dict(result.false_kinds),
                   "merged": result.verdicts.count("merged"), "split": result.verdicts.count("split"),
                   "missed": len(result.missed)}
        self._record("training_check", **{k: v for k, v in summary.items() if k not in ("found_min", "correct_min")})
        record = self.manager.study_record()
        record.setdefault("training_attempts", []).append(dict(summary, time=sp.now()))
        self.manager.save_study_record(record)
        if result.passed:
            self.feedback = None
            return summary
        wrong = [[float(colonies.centers[k, 0]), float(colonies.centers[k, 1]), float(colonies.radii[k]),
                  feedback_label(v)] for k, v in enumerate(result.verdicts) if v != "correct"]
        missed = [[self.truth[i][0] - x0, self.truth[i][1] - y0, self.truth[i][2], "a colony to measure that has no outline"]
                  for i in result.missed]
        return dict(summary, problems=few_problems(wrong, missed, self.attempts))

    def clear_feedback(self) -> None:
        """Clear problems: the check's marks leave the plate; the next Finish image checks again."""
        if self.feedback is not None:
            self.feedback = None
            self._record("training_marks_cleared", attempt=self.attempts)

    def _reference_colonies(self) -> list:
        """[x, y, r] in crop pixels of every required colony (automated checks only)."""
        x0, y0 = sp.crop_offset(self.bundle.practice, self.labels.shape)
        return [[x - x0, y - y0, r] for i, (x, y, r) in self.truth.items() if self.required.get(i)]

    # ===== the guide (study/guide.py) ================================================
    def _places(self) -> dict:
        """The checks on the ringed parts of the practice plate (guide.facts_from places)."""
        if not self._is_practice():
            return {}
        x0, y0 = sp.crop_offset(self.bundle.practice, self.labels.shape)
        photo = self.session.colonies.translated(x0, y0)
        marks = self.bundle.marks
        out = {}
        if self.bundle.target is not None:
            out["mass"] = sm.target_status(photo, self.labels, self.bundle.target)

        def clear(name):
            m = marks.get(name)
            if m is None or len(photo) == 0:
                return True
            d = np.hypot(photo.centers[:, 0] - m.center[0], photo.centers[:, 1] - m.center[1])
            return not bool(np.any(d < m.radius))
        out["marker_clear"] = clear("marker")
        out["condensation_clear"] = clear("condensation")
        if "faint" in marks:
            out["faint_added"] = sm.target_status(photo, self.labels, marks["faint"])[1]
        if "clump" in marks:
            out["clump_unmerged"] = sm.target_status(photo, self.labels, marks["clump"])[0]
        if "snap" in marks:
            out["snap_added"] = sm.target_status(photo, self.labels, marks["snap"])[1]
        corners = self.corners("condensation")
        if corners and self.selection_rect is not None:
            out["cond_boxed"] = holds(self.selection_rect, corners)
        dots = self.corners("bright")
        if dots and marks["bright"].colony_ids:
            ids = marks["bright"].colony_ids
            if self.roi is not None:
                rx, ry, rw, rh = self.roi
                out["dots_boxed"] = holds((rx, ry, rx + rw, ry + rh), dots)
            if self.candidates is not None and len(self.candidates):
                found = {int(self.labels[min(int(round(y + y0)), self.labels.shape[0] - 1),
                                         min(int(round(x + x0)), self.labels.shape[1] - 1)])
                         for x, y in self.candidates.centers}
                out["dots_found"] = out.get("dots_boxed", False) and set(ids) <= found
            out["added_dots"] = sm.target_status(photo, self.labels, sm.Target(ids, marks["bright"].center,
                                                                                marks["bright"].radius))[1]
        return out

    def corners(self, name: str) -> list:
        """The four corner dots of the box a step asks for, [x, y] in crop pixels: for 'bright', the box
        holding its pale colonies; for 'condensation', the box holding the droplets; otherwise the square
        around the mark's circle."""
        mark = self.bundle.marks.get(name)
        if mark is None or not self._is_practice():
            return []
        x0, y0 = sp.crop_offset(self.bundle.practice, self.labels.shape)
        if name == "bright" and mark.colony_ids:
            pts = [self.truth[i] for i in mark.colony_ids if i in self.truth]
            left = min(x - r for x, _, r in pts) - BOX_MARGIN
            right = max(x + r for x, _, r in pts) + BOX_MARGIN
            top = min(y - r for _, y, r in pts) - BOX_MARGIN
            bottom = max(y + r for _, y, r in pts) + BOX_MARGIN
        elif name == "condensation" and self.kinds is not None and np.any(self.kinds == 3):
            ys, xs = np.nonzero(self.kinds == 3)
            left, right = xs.min() - BOX_MARGIN, xs.max() + BOX_MARGIN
            top, bottom = ys.min() - BOX_MARGIN, ys.max() + BOX_MARGIN
        else:
            (cx, cy), r = mark.center, mark.radius
            left, right, top, bottom = cx - r, cx + r, cy - r, cy + r
        return [[float(x) - x0, float(y) - y0] for x, y in ((left, top), (right, top), (right, bottom), (left, bottom))]

    def _guide_facts(self, open_sections) -> gd.GuideFacts:
        events = self.recorder.log.events if self.recorder is not None else []
        return gd.facts_from(events, self.session.params, list(open_sections or ()), self.mode, self._places(),
                             self.practice_done, self.estimate_pressed, self.contour)

    def tutorial(self, open_sections=()) -> dict:
        """The guide's current step with a checkmark per bullet, or done once the practice plate is saved.
        open_sections: ids of the sections open on the page."""
        if self.practice_done:
            return {"done": True, "goal": sm.GOAL, "colors": gd.COLORS, "tasks": list(gd.PLATE_TASKS)}
        facts = self._guide_facts(open_sections)
        step = gd.STEPS[self.step]
        bullets = [{"text": n.text, "anchor": n.anchor, "done": n.met(facts), "info": n.check is None}
                   for n in step.needs]
        met = step.met(facts)
        changes = gd.settings_changes(self.session.params) if step.tuned else []
        if changes:                                    # the lesson's settings first, each change named
            listed = "; ".join(f"{label} to {want} (now {now})" for label, now, want, _ in changes)
            text = f"First set back {listed}"
            if self.mode != "ModifyGlobalParameters":
                text = f"First choose Detect automatically, then set back {listed}"
            anchor = "mode-ModifyGlobalParameters" if self.mode != "ModifyGlobalParameters" else changes[0][3]
            bullets = [{"text": text, "anchor": anchor, "done": False, "restore": True}] + bullets
            met = False
        pointing = next((b["anchor"] for b in bullets if not b["done"]), "")
        return {"done": False, "number": self.step + 1, "total": len(gd.STEPS), "key": step.key, "title": step.title,
                "text": step.text, "bullets": bullets, "met": met, "anchor": pointing,
                "hide_outlines": step.hide_outlines, "ring": self.ring(step.ring), "ring_name": step.ring,
                "block_add": self._adding_blocked(),
                "dots": self.corners(step.ring) if step.ring in BOX_MARKS else [],
                "dots_capture": step.ring in BOX_MARKS,
                "arrow": self.arrow_target(step.ring) if step.ring and step.ring not in BOX_MARKS else []}

    def pass_step(self, open_sections=()) -> None:
        """The next step, only once every bullet of this one is checked (the page calls it by itself
        when a step with tasks is met, and from Next on a step without tasks)."""
        step = gd.STEPS[self.step]
        if step.met(self._guide_facts(open_sections)) and self.step < len(gd.STEPS) - 1:
            self.step += 1

    def skip_step(self) -> None:
        """Skip a step without meeting it (recorded); the last step (Finish image) cannot be skipped.
        The page offers no skip: participants do every step. Kept for automated checks."""
        if self.step < len(gd.STEPS) - 1:
            self.skipped.append(gd.STEPS[self.step].key)
            self.step += 1

    def ring(self, name: str = "triple") -> list:
        """[cx, cy, r] of a bundle mark in crop pixels ([] outside the practice plate or for no mark)."""
        mark = self.bundle.marks.get(name) or (self.bundle.target if name == "triple" else None)
        if mark is None or not self._is_practice():
            return []
        x0, y0 = sp.crop_offset(self.bundle.practice, self.labels.shape)
        return [mark.center[0] - x0, mark.center[1] - y0, mark.radius]

    def arrow_target(self, name: str) -> list:
        """[cx, cy, r] the step's arrow points at: the mark's circle, drawn tighter around a written number
        (whose circle holds the whole digit and the space beside it)."""
        ring = self.ring(name)
        if ring and name == "marker":
            ring[2] *= 0.55
        return ring

    def target_ring(self) -> list:
        return self.ring("triple")

    # ===== results ====================================================================
    def results(self, complete: bool = True) -> dict:
        """Close the project and return the results zip: {name, bytes, record}."""
        record = sp.stamp(self.manager, "finished" if complete else "stopped")
        path = sp.write_results(self.manager, self.participant, self.work)
        self.manager = None
        return {"name": path.name, "bytes": path.read_bytes(), "record": json.dumps(record)}


def describe_specs() -> str:
    """The settings' limits as JSON, for the page to build its controls before a plate opens."""
    return json.dumps({k: {"label": v.label, "min": v.minimum, "max": v.maximum, "step": v.step}
                       for k, v in PARAM_SPECS.items()})


BOX_MARGIN = 10                    # pixels between a marked box's contents and its corner dots
BOX_MARKS = ("bright", "condensation")   # steps that ask for a box: corner dots; other marks get an arrow


def holds(box, points) -> bool:
    """True when the box (x0, y0, x1, y1, either corner order) holds every point."""
    x0, y0, x1, y1 = box
    return all(min(x0, x1) <= x <= max(x0, x1) and min(y0, y1) <= y <= max(y0, y1) for x, y in points)


FEW_PROBLEMS = 3                   # places to look again after a failed check: a sample, never the full list


def few_problems(wrong: list, missed: list, attempt: int) -> list:
    """A few of the check's problems, alternating wrong outlines and missed colonies, a different sample
    on each attempt. Nothing is drawn on the plate: the page only moves the view to each place."""
    rng = np.random.default_rng(attempt)
    pools = [list(rng.permutation(len(wrong))), list(rng.permutation(len(missed)))]
    picked = []
    while len(picked) < FEW_PROBLEMS and any(pools):
        for source, pool in ((wrong, pools[0]), (missed, pools[1])):
            if pool and len(picked) < FEW_PROBLEMS:
                picked.append(source[int(pool.pop())])
    return picked


def feedback_label(verdict: str) -> str:
    """Plain words for an outline the training check marks (study/score.py verdicts)."""
    if verdict == "merged":
        return "one outline over several colonies"
    if verdict == "split":
        return "a colony with more than one outline"
    kind = verdict.split(":", 1)[1] if ":" in verdict else "agar"
    return {"marker": "an outline on the plate number", "bubble": "an outline on a bubble",
            "condensation": "an outline on condensation", "rim_shadow": "an outline on the dish edge's shadow",
            "debris": "an outline on dust", "agar": "an outline on bare agar"}.get(kind, "a false outline")


REVIEW_COLORS = {"manual": (123, 245, 1), "split": (156, 140, 255)}
DETECTED_COLOR = (46, 200, 187)


def review_jpeg(image: np.ndarray, colonies, max_side: int = 1000) -> bytes:
    """A plate with its colonies outlined in the page's colors, at most max_side pixels on a side (JPEG)."""
    rows, cols = image.shape[:2]
    scale = min(1.0, max_side / max(rows, cols))
    picture = Image.fromarray(np.ascontiguousarray(image[..., :3]))
    if scale < 1:
        picture = picture.resize((max(1, round(cols * scale)), max(1, round(rows * scale))), Image.LANCZOS)
    draw = ImageDraw.Draw(picture)
    for k in range(len(colonies)):
        color = REVIEW_COLORS.get(str(colonies.kinds[k]), DETECTED_COLOR)
        boundary = colonies.boundaries[k]
        if boundary is not None and len(boundary) > 2:
            points = [(float(x) * scale, float(y) * scale) for x, y in np.asarray(boundary)]
            draw.line(points + points[:1], fill=color, width=2)
        else:
            x, y, r = (float(v) * scale for v in (colonies.centers[k, 0], colonies.centers[k, 1], colonies.radii[k]))
            draw.ellipse((x - r, y - r, x + r, y + r), outline=color, width=2)
    buffer = io.BytesIO()
    picture.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def png_bytes(rgb: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(rgb).save(buffer, format="PNG")
    return buffer.getvalue()
