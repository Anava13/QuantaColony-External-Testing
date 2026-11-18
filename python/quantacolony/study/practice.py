"""The practice plate's guide and training check (Qt-free), shared by the browser study (study/web.py) and the
Colony Detection tour of the desktop program (gui/detection_guide.py).

PracticeGuide is a mixin. Its host provides what is on screen:
    session, recorder        the open image's DetectionSession and DetectionRecorder
    mode                     the tool in use (gui/modes.py keys)
    roi, candidates          Detect in a region: the box (x, y, width, height) and its outlines
    selection_rect           Delete a region: the box (x0, y0, x1, y1)
    contour                  Snap to colony edge is on
    _record(event, **data)   adds an event to the image's benchmark record
    _save_attempt(summary)   keeps a training check's result (the study record, or nothing)
and calls load_practice(bundle) once.

    load_practice       the practice plate's truth: labels, objects that are not colonies, required colonies
    tutorial            the current step: tasks with their checks, the arrow or corner dots, the settings to
                        set back, whether Add colonies waits
    pass_step           the next step, once every task is checked
    training check      _training_check scores the plate (study/score.py); a failed check returns its
                        shortfall and a few places to look again
"""

from __future__ import annotations

import csv

import numpy as np
from PIL import Image

from . import guide as gd
from . import model as sm
from . import project as sp
from . import score as sc

REGION_MODE = "AddManyColonies"    # settings apply inside the region only


class PracticeGuide:
    def load_practice(self, bundle) -> None:
        self.labels = np.asarray(Image.open(bundle.labels_path())).astype(int)
        self.kinds = np.asarray(Image.open(bundle.artifacts_path())) if bundle.artifacts_path().exists() else None
        rows = list(csv.DictReader(open(bundle.truth_path(), encoding="utf-8"))) if bundle.truth_path().exists() \
            else []
        self.truth = {int(r["colony_id"]): (float(r["x_px"]), float(r["y_px"]), float(r["true_radius_px"]))
                      for r in rows}
        self.required = sc.required_colonies(rows)
        self.feedback = None                       # the last failed training check
        self.attempts = 0
        self.step = 0                              # the guide's current step (guide.STEPS)
        self.estimate_pressed = False              # Estimate settings pressed, even when nothing changed
        self.skipped: list[str] = []
        self.practice_done = False

    def _save_attempt(self, summary: dict) -> None:
        """A training check's result in the study record (the browser study's project)."""
        manager = getattr(self, "manager", None)
        if manager is None:
            return
        record = manager.study_record()
        record.setdefault("training_attempts", []).append(dict(summary, time=sp.now()))
        manager.save_study_record(record)

    def _adding_blocked(self) -> bool:
        """The Snap step holds Add colonies until its right-click task is done, so a participant who adds the
        snapped circle early does not lose the step."""
        if not self._is_practice() or self.practice_done or gd.STEPS[self.step].key != "add_tools":
            return False
        events = self.recorder.log.events if self.recorder is not None else []
        return not any(e.kind == "draw_removed" and e.data.get("how") == "right" for e in events)

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
        self._save_attempt(summary)
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

    desktop = False                                # the desktop tour: tasks that open page sections are left out

    def _needs(self, step) -> list:
        return [n for n in step.needs if not (self.desktop and getattr(n.check, "web_only", False))]

    def tutorial(self, open_sections=()) -> dict:
        """The guide's current step with a checkmark per bullet, or done once the practice plate is saved.
        open_sections: ids of the sections open on the page."""
        if self.practice_done:
            return {"done": True, "goal": sm.GOAL, "colors": gd.COLORS, "tasks": list(gd.PLATE_TASKS)}
        facts = self._guide_facts(open_sections)
        step = gd.STEPS[self.step]
        bullets = [{"text": n.text, "anchor": n.anchor, "done": n.met(facts), "info": n.check is None}
                   for n in self._needs(step)]
        met = all(n.met(facts) for n in self._needs(step))
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
        facts = self._guide_facts(open_sections)
        if all(n.met(facts) for n in self._needs(step)) and self.step < len(gd.STEPS) - 1:
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
