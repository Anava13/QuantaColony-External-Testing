"""User study model (Qt-free): participant answers, plate order, the bundle of plates, and the tutorial.

    QUESTIONS           the participant form: key, question, choices (the name is typed)
    Participant         the answers, their problems, and the record stored with the results
    presentation_order  the scored plates in a random order drawn from a recorded seed
    Bundle              the plates shipped with the study (manifest.json): photo, scale, dish circle,
                        the practice plate's tutorial target and the concept figures
    Facts               what the tutorial checks read: recorder events, the target mass, the practice saved
    STEPS               the tutorial on the practice plate; each step is met by Facts or read and passed
    target_resolved     whether a merged mass of the practice plate is measured as its separate colonies
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

STUDY_VERSION = 1                     # changes when plates, tutorial or questions change
PRACTICE = "practice"                 # level value of the practice plate

QUESTIONS = (
    ("background", "Science background", ("None", "Coursework only", "Works in a lab")),
    ("experience", "Experience measuring colonies", ("Never", "By hand (ruler or ImageJ)", "With colony software")),
    ("used_before", "Used QuantaColony before", ("No", "Yes")),
    ("device", "Pointing device for this session", ("Mouse", "Trackpad")),
)


# ======================================================================================
# participant
# ======================================================================================
@dataclass
class Participant:
    name: str = ""
    answers: dict = field(default_factory=dict)          # question key -> chosen text

    def problems(self) -> list[str]:
        """What is missing before the study can start (empty when complete)."""
        missing = [] if self.name.strip() else ["Enter a name."]
        unanswered = [question for key, question, _ in QUESTIONS if self.answers.get(key) is None]
        if unanswered:
            missing.append("Still to answer: " + ", ".join(unanswered) + ".")
        return missing

    def file_stem(self) -> str:
        """The name as a file name part: letters, digits, '-' and '_' only."""
        return re.sub(r"[^A-Za-z0-9_-]+", "_", self.name.strip()).strip("_") or "participant"

    def record(self) -> dict:
        return {"name": self.name.strip(), **{key: self.answers.get(key) for key, _, _ in QUESTIONS}}


def presentation_order(names: list[str], seed: int) -> list[str]:
    """The scored plates in the order this participant sees them."""
    return [names[i] for i in np.random.default_rng(seed).permutation(len(names))]


def plate_levels(order: list[str]) -> list[tuple[str, str]]:
    """(level value, display) for the plate variable: the practice plate, then Plate 1, 2, ... in order.
    The display hides the plate's file name, which would reveal whether it is synthetic."""
    return [(PRACTICE, "Practice")] + [(name, f"Plate {k}") for k, name in enumerate(order, start=1)]


# ======================================================================================
# the bundle
# ======================================================================================
@dataclass(frozen=True)
class BundlePlate:
    name: str                          # 'practice', 'study_01', ...
    file: str                          # photo file in the bundle's photos folder
    role: str                          # 'practice' or 'scored'
    px_per_mm: float                   # imported scale
    dish: tuple[float, float, float] | None   # dish circle (cx, cy, r) in photo pixels: the plate's region;
                                              # None: the whole photo (a window cut from a larger plate)


@dataclass(frozen=True)
class Target:
    """The merged mass the tutorial asks to measure as separate colonies (practice photo pixels)."""

    colony_ids: tuple[int, ...]        # true colonies of the mass (practice label image values)
    center: tuple[float, float]
    radius: float                      # a circle around the whole mass, for the ring drawn on the photo


@dataclass(frozen=True)
class Concept:
    file: str                          # figure in the bundle's concepts folder
    title: str
    caption: str


@dataclass(frozen=True)
class Bundle:
    folder: Path
    version: int
    plates: tuple[BundlePlate, ...]
    target: Target | None
    concepts: tuple[Concept, ...]
    marks: dict = field(default_factory=dict)     # name -> Target: the places the guide rings on the practice
                                                  # plate (colony_ids empty for objects that are not colonies)

    @property
    def practice(self) -> BundlePlate:
        return next(p for p in self.plates if p.role == "practice")

    @property
    def scored(self) -> list[BundlePlate]:
        return [p for p in self.plates if p.role == "scored"]

    def plate(self, name: str) -> BundlePlate:
        return next(p for p in self.plates if p.name == name)

    def photo_path(self, plate: BundlePlate) -> Path:
        return self.folder / "photos" / plate.file

    def labels_path(self) -> Path:
        return self.folder / "practice_labels.png"

    def artifacts_path(self) -> Path:
        """The practice plate's objects that are not colonies (kind codes, study/score.py KIND_NAMES)."""
        return self.folder / "practice_artifacts.png"

    def truth_path(self) -> Path:
        """The practice plate's truth table (required and optional colonies, study/score.py)."""
        return self.folder / "practice_truth.csv"


def load_bundle(folder: Path) -> Bundle:
    data = json.loads((Path(folder) / "manifest.json").read_text(encoding="utf-8"))
    plates = tuple(BundlePlate(p["name"], p["file"], p["role"], float(p["px_per_mm"]),
                               tuple(map(float, p["dish"])) if p.get("dish") else None) for p in data["plates"])
    t = data.get("target")
    target = Target(tuple(int(i) for i in t["colony_ids"]), tuple(map(float, t["center"])), float(t["radius"])) \
        if t else None
    concepts = tuple(Concept(c["file"], c["title"], c["caption"]) for c in data.get("concepts", ()))
    marks = {name: Target(tuple(int(i) for i in m["colony_ids"]), tuple(map(float, m["center"])), float(m["radius"]))
             for name, m in data.get("marks", {}).items()}
    return Bundle(Path(folder), int(data["version"]), plates, target, concepts, marks)


def manifest(version: int, plates: list[BundlePlate], target: Target | None, concepts: list[Concept],
             marks: dict | None = None) -> dict:
    """The manifest.json content for a bundle (written by tools/study/build_bundle.py)."""
    return {"version": version, "plates": [asdict(p) for p in plates],
            "target": asdict(target) if target else None, "concepts": [asdict(c) for c in concepts],
            "marks": {name: asdict(m) for name, m in (marks or {}).items()}}


# ======================================================================================
# the tutorial
# ======================================================================================
@dataclass(frozen=True)
class Facts:
    """What the tutorial's checks read. Event kinds come from the image's recorder (core/benchmark.py)."""

    plate: str = ""                    # level value of the plate shown
    modes: frozenset = frozenset()     # modes entered
    settings: frozenset = frozenset()  # settings changed (field names: 'level', 'sense', ...)
    split_changed: bool = False        # a detection ran with Split touching colonies changed from the first
    deletions: int = 0
    additions: int = 0
    undos: int = 0
    target_resolved: bool = False
    practice_saved: bool = False


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    text: str
    task: str = ""                     # what to do; empty for a reading step
    anchor: str = ""                   # the control the spotlight points at (StudyView.anchor)
    check: object = None               # Facts -> bool; None for a reading step

    def met(self, facts: Facts) -> bool:
        return self.check is not None and bool(self.check(facts))


GOAL = ("Draw one outline for every colony you can tell apart, on the colony's outer edge. Touching colonies are "
        "separate colonies: a mass of three touching colonies is three colonies, each with its own outline. Skip a "
        "mass of five or more colonies that you cannot tell apart. Plate numbers, bubbles, condensation, dust and "
        "shadows are not colonies. Some plates show part of a dish: measure every colony in view, including "
        "colonies cut by the edge.")

# The note over the whole window once the practice plate is saved: each point is checked before the
# first scored plate opens.
START_NOTE = (
    "The accuracy test starts now. It asks whether typical users, like you, agree on colony measurements "
    "once they know the software.",
    "Measure every colony you can tell apart on each plate, each with its own outline on its outer edge. "
    "Skip masses of five or more colonies that you cannot tell apart.",
    "Watch for false detections: delete any outline on something that is not a colony (plate numbers, bubbles, "
    "condensation, dust), or that covers more than one colony.",
    "The three plates take about 15 minutes from here. Take the time to measure them carefully.",
)

STEPS = (
    Step("plate", "The practice plate",
         "This plate is for practice and is not part of the results. The green outlines are the program's "
         "automatic detection. Your task on every plate is to correct them until they match the colonies. "
         + GOAL),
    Step("settings", "Adjust automatic detection",
         "Sensitivity and Brightness threshold decide what the program counts as colony. Changing either one "
         "detects the whole plate again.",
         "Change Sensitivity or Brightness threshold, then watch the outlines change.", "settings",
         lambda f: bool(f.settings & {"level", "sense"})),
    Step("split", "Split touching colonies",
         "When this switch is on, the program cuts masses of touching colonies at their narrow necks. It "
         "separates many masses, though not all of them.",
         "Turn Split touching colonies off and on again, then look at the masses.", "split",
         lambda f: f.split_changed),
    Step("delete", "Delete false detections",
         "An outline on agar, on a mark or around a whole mass is wrong. Choose Delete colonies, click the "
         "outlines to remove, then press Delete.",
         "Delete at least one outline.", "delete", lambda f: f.deletions > 0),
    Step("add", "Add missed colonies",
         "Choose Add colonies and drag across a colony from edge to edge: the drag is its diameter. Press "
         "Add colonies to keep the circles you drew.",
         "Add at least one colony.", "add", lambda f: f.additions > 0),
    Step("undo", "Undo",
         "Every change can be undone with the arrow at the top or Ctrl+Z, and redone with Ctrl+Y.",
         "Undo one change.", "undo", lambda f: f.undos > 0),
    Step("mass", "Measure a merged mass as its colonies",
         "The ringed mass on the photo is three touching colonies. Measure it as three colonies: delete any "
         "outline that covers more than one of them, then add one circle per colony.",
         "Give each of the three colonies in the ring its own outline.", "ring",
         lambda f: f.target_resolved),
    Step("finish", "Finish the plate",
         "Finish image saves the plate and opens the next one. After the practice plate, three plates follow. "
         "Work on each until its outlines match its colonies, then press Finish image.",
         "Press Finish image to start the study.", "finish", lambda f: f.practice_saved),
)


def current_step(facts: Facts, passed: set[str]) -> int:
    """The first step neither met nor passed (len(STEPS) when every step is done)."""
    for i, step in enumerate(STEPS):
        if step.key not in passed and not step.met(facts):
            return i
    return len(STEPS)


def facts_from_events(events, plate: str, target: bool, saved: bool) -> Facts:
    """Facts from recorder events (core.benchmark.Event: kind, data). Setting events are kept at most
    once a second, so the split check reads the detections, which record the settings each one used."""
    kinds = [(e.kind, e.data) for e in events]
    splits = [bool(d.get("params", {}).get("split")) for k, d in kinds if k == "detection"]
    return Facts(plate=plate,
                 modes=frozenset(d.get("mode") for k, d in kinds if k == "mode"),
                 settings=frozenset(d.get("field") for k, d in kinds if k == "setting"),
                 deletions=sum(1 for k, d in kinds if k == "edit" and d.get("kind") == "Deletion"),
                 additions=sum(1 for k, d in kinds if k == "edit" and d.get("kind") == "Addition"),
                 undos=sum(1 for k, _ in kinds if k == "undo"),
                 split_changed=any(s != splits[0] for s in splits[1:]),
                 target_resolved=target, practice_saved=saved)


# ======================================================================================
# the merged mass
# ======================================================================================
def detection_mask(center, radius: float, outline, window: tuple[int, int, int, int]) -> np.ndarray:
    """Pixels of one detection inside a window (x0, y0, x1, y1) of the photo: its outline, else its circle."""
    from skimage.draw import disk, polygon
    x0, y0, x1, y1 = window
    mask = np.zeros((y1 - y0, x1 - x0), bool)
    if outline is not None and len(outline) > 2:
        xy = np.asarray(outline, float)
        rr, cc = polygon(xy[:, 1] - y0, xy[:, 0] - x0, mask.shape)
    else:
        rr, cc = disk((center[1] - y0, center[0] - x0), radius, shape=mask.shape)
    mask[rr, cc] = True
    return mask


def target_status(colonies, labels: np.ndarray, target: Target) -> tuple[bool, bool]:
    """(no detection covers at least half of two colonies of the mass (the scorer's MERGED rule,
    score_quantacolony.py), every colony of the mass has exactly one detection centered on it).
    colonies: a ColonySet in photo pixels; labels: the practice label image (0 = agar)."""
    cx, cy = target.center
    pad = target.radius * 1.5
    rows, cols = labels.shape
    window = (max(0, int(cx - pad)), max(0, int(cy - pad)), min(cols, int(cx + pad) + 1), min(rows, int(cy + pad) + 1))
    x0, y0, x1, y1 = window
    patch = labels[y0:y1, x0:x1]
    sizes = {i: int(np.sum(patch == i)) for i in target.colony_ids}
    centered = {i: 0 for i in target.colony_ids}
    merged = False
    for k in range(len(colonies)):
        (x, y), r = colonies.centers[k], float(colonies.radii[k])
        if not (x0 - r <= x <= x1 + r and y0 - r <= y <= y1 + r):
            continue
        xi, yi = int(round(x)), int(round(y))
        owner = int(labels[yi, xi]) if 0 <= yi < rows and 0 <= xi < cols else 0
        if owner in centered:
            centered[owner] += 1
        mask = detection_mask((x, y), r, colonies.boundaries[k], window)
        covered = sum(1 for i in target.colony_ids if sizes[i] and np.sum(mask & (patch == i)) >= sizes[i] / 2)
        merged = merged or covered >= 2
    return not merged, not merged and all(count == 1 for count in centered.values())


def target_resolved(colonies, labels: np.ndarray, target: Target) -> bool:
    """True when every colony of the mass has exactly one detection centered on it and no detection
    covers at least half of two of them."""
    return target_status(colonies, labels, target)[1]
