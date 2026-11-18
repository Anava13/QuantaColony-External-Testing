"""New Project model (Qt-free): the draft a user builds in the wizard, its checks, and the
specifications the DataManager creates the project from.

Steps: Photos (name and ordered photos), Plate (round dish with its diameter and 1 to 8
sections, or a rectangular grid of cells or spots), Variables (kind, preset, levels, control),
Annotations, Review.

The scale is set per photo in Cropping (core.scale). The plate only sets where Cropping starts:
round dishes from the dish edge at this diameter, rectangular plates from a scale bar drawn on
the photo, since rectangular plates come in too many shapes to assume a size.

Nominal and ordinal variables are the design: each combination of their levels is one index.
A replicate variable (one at most) is not crossed with them: its count is how many interchangeable
plates each index is expected to hold, and the plates are numbered 1, 2, ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

STEPS = ("Photos", "Plate", "Variables", "Annotations", "Review")
KINDS = ("nominal", "ordinal", "replicate")
KIND_NAMES = {"nominal": "Nominal", "ordinal": "Ordinal", "replicate": "Replicate"}

# kind -> [(preset, unit, level n -> default text)]
PRESETS = {
    "nominal": [("Strain / genotype", "", lambda i: f"Strain {i}"), ("Medium", "", lambda i: f"Medium {i}"),
                ("Treatment", "", lambda i: f"Treatment {i}"), ("Custom", "", lambda i: f"Group {i}")],
    "ordinal": [("Generations", "gen", lambda i: str(i)), ("Temperature", "°C", lambda i: str(25 + (i - 1) * 3)),
                ("Time", "h", lambda i: str((i - 1) * 24)), ("Additive", "", lambda i: str(i)),
                ("Custom", "", lambda i: str(i))],
    # A replicate variable is not a factor: it numbers the interchangeable plates of each index, so its
    # "levels" are plate numbers and their count is how many plates each index is expected to hold.
    "replicate": [("Biological replicate", "", lambda i: str(i)), ("Technical replicate", "", lambda i: str(i))],
}
DISH_SIZES = (("90", "90 mm standard"), ("140", "140 mm large"))
MAX_SECTIONS = 8
MAX_LEVELS = 200                     # most levels (or plates per index) one variable can have


# The name a template gives the variable when picked from the name box (default: the template's own label)
TEMPLATE_NAMES = {"Strain / genotype": "Genotype"}


def template_name(label: str) -> str:
    return TEMPLATE_NAMES.get(label, label)


def name_suggestions(kind: str) -> list[tuple[str, str]]:
    """(name shown, template label) for the name box of a variable of this kind. Custom is not listed:
    typing any other name is the custom case."""
    return [(template_name(label), label) for label, _, _ in PRESETS[kind] if label != "Custom"]


def _all_default_levels(position: int) -> set[str]:
    """Every level text a template writes at this position (1-based), for any kind."""
    return {make(position) for presets in PRESETS.values() for _, _, make in presets}


def preset_of(kind: str, name: str):
    return next((p for p in PRESETS[kind] if p[0] == name), PRESETS[kind][0])


def preset_example(kind: str, name: str) -> str:
    label, unit, make = preset_of(kind, name)
    return ", ".join(make(i) for i in (1, 2, 3)) + (f" {unit}" if unit else " + units" if label == "Additive" else "")


@dataclass
class VariableDraft:
    name: str
    kind: str = "nominal"
    preset: str = "Custom"
    values: list[str] = field(default_factory=lambda: ["Group 1", "Group 2"])
    control: int = -1                  # index of the control level, -1 for none
    unit: str = ""
    additive: str = ""                 # what is added, for the Additive preset

    def regenerate(self, kind: str, preset: str, count: int) -> None:
        """New kind or preset: default level names, the preset's unit, the control kept when it still fits."""
        label, unit, make = preset_of(kind, preset)
        self.kind, self.preset = kind, label
        if label != "Additive":
            self.unit = unit
        self.values = [make(i + 1) for i in range(count)]
        self.control = -1 if kind == "replicate" or self.control >= count else self.control

    def level_is_default(self, index: int) -> bool:
        """True while level `index` still holds text a template wrote (the user has not typed over it)."""
        return self.values[index].strip() in _all_default_levels(index + 1)

    def name_is_default(self) -> bool:
        """True while the name is empty, a numbered placeholder ('Variable 2') or a template's name."""
        name = self.name.strip()
        names = {template_name(label) for presets in PRESETS.values() for label, _, _ in presets}
        return not name or name in names or (name.startswith("Variable ") and name[9:].isdigit())

    def apply_template(self, preset: str) -> None:
        """Picked from the name box: the template's name, its unit, and its example levels in place of
        levels the user has not typed over."""
        label, unit, make = preset_of(self.kind, preset)
        self.preset = label
        self.name = template_name(label)
        if label != "Additive":
            self.unit = unit
        self.values = [make(i + 1) if self.level_is_default(i) else value for i, value in enumerate(self.values)]

    def change_kind(self, kind: str) -> None:
        """Nominal / ordinal / replicate: the kind's first template fills in what the user has not typed."""
        if kind == self.kind:
            return
        label, unit, make = PRESETS[kind][0]
        rename = self.name_is_default()
        self.kind, self.preset = kind, label
        if rename:
            self.name = template_name(label)
        if label != "Additive":
            self.unit = unit
        self.values = [make(i + 1) if self.level_is_default(i) else value for i, value in enumerate(self.values)]
        if kind == "replicate":
            self.control = -1
            self.values = [str(i + 1) for i in range(len(self.values))]

    def set_count(self, count: int) -> None:
        count = max(1, count)
        make = preset_of(self.kind, self.preset)[2]
        while len(self.values) < count:
            self.values.append(make(len(self.values) + 1))
        del self.values[count:]
        if self.control >= count:
            self.control = -1


ANNOTATION_KINDS = (("number", "Number"), ("choice", "Choice"), ("text", "Note"))


@dataclass
class AnnotationDraft:
    """One per-plate annotation: recorded with the plates, never indexed on."""

    name: str = ""
    kind: str = "number"               # 'number', 'choice', 'text'
    unit: str = ""
    choices: list = field(default_factory=list)
    values: dict = field(default_factory=dict)      # photo path (str) -> value text

    def value_problem(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""
        if self.kind == "number" and not _is_number(text):
            return f"{self.name or 'This annotation'} takes a number."
        if self.kind == "choice" and text not in [c.strip() for c in self.choices if c.strip()]:
            return f"{self.name or 'This annotation'} takes one of: " + ", ".join(
                c.strip() for c in self.choices if c.strip())
        return ""


@dataclass
class ProjectDraft:
    name: str = ""
    taken: tuple[str, ...] = ()        # names already in the library
    photos: list[Path] = field(default_factory=list)
    plate: str = "circular"            # 'circular' or 'rectangular'
    sections: int = 4
    rows: int = 2
    cols: int = 2
    cells: str = "cells"               # rectangular grid sections: 'cells' or 'spots'
    dish_mm: str = "90"                # round dish diameter, the first scale Cropping proposes
    variables: list[VariableDraft] = field(default_factory=list)
    annotations: list[AnnotationDraft] = field(default_factory=list)
    record_benchmark: bool = False     # developer mode: the project records benchmark data (core/benchmark.py)

    # ----- derived values ------------------------------------------------------
    @property
    def section_count(self) -> int:
        return self.sections if self.plate == "circular" else self.rows * self.cols

    @property
    def single_image(self) -> bool:
        return len(self.photos) == 1 and self.section_count == 1

    @property
    def index_count(self) -> int:
        """Combinations of the design variables' levels (a replicate variable is not a factor)."""
        count = 1
        for v in self.variables:
            if v.kind != "replicate":
                count *= len(v.values)
        return count

    @property
    def plates_per_index(self) -> int:
        """Plates each index is expected to hold: the replicate variable's count, else one."""
        return next((len(v.values) for v in self.variables if v.kind == "replicate"), 1)

    @property
    def plate_count(self) -> int:
        return self.index_count * self.plates_per_index

    def name_problem(self) -> str:
        name = self.name.strip()
        if not name:
            return "The project needs a name."
        if name in self.taken:
            return f"A project named {name} already exists in your library."
        return ""

    def suggested_name(self) -> str:
        base = self.name.strip() or "Project"
        k = 1
        while f"{base}_{k}" in self.taken:
            k += 1
        return f"{base}_{k}"

    def variable_error(self, index: int) -> str:
        v = self.variables[index]
        values = [x.strip() for x in v.values]
        if not v.name.strip():
            return "The variable needs a name."
        if any(i != index and o.name.strip().lower() == v.name.strip().lower() for i, o in enumerate(self.variables)):
            return "Another variable has this name."
        if v.kind == "replicate":
            if any(o.kind == "replicate" for o in self.variables[:index]):
                return "A project has one replicate variable: it numbers the plates of each index."
            return ""
        if "" in values:
            return f"Level {values.index('') + 1} is empty."
        lower = [x.lower() for x in values]
        for i, x in enumerate(lower):
            if lower.index(x) != i:
                return f'Level {i + 1} repeats "{values[i]}". Each level needs its own name.'
        if v.kind == "ordinal" and not all(_is_number(x) for x in values):
            return "Ordinal levels are numbers, so plots can space them."
        if v.preset == "Additive" and (not v.additive.strip() or not v.unit.strip()):
            return "Name the additive and its units."
        return ""

    def bad_levels(self, index: int) -> set[int]:
        v = self.variables[index]
        lower = [x.strip().lower() for x in v.values]
        return {i for i, x in enumerate(lower) if not x or lower.count(x) > 1}

    def variables_ok(self) -> bool:
        if not self.variables:
            return self.single_image
        return all(not self.variable_error(i) for i in range(len(self.variables)))

    def step_ok(self, step: int) -> bool:
        return [not self.name_problem() and bool(self.photos), self.plate_ok(), self.variables_ok(),
                self.annotations_ok(), True][step]

    def annotations_ok(self) -> bool:
        return not self.annotation_problem()

    def annotation_problem(self) -> str:
        names = [a.name.strip() for a in self.annotations]
        if any(not n for n in names):
            return "Every annotation needs a name."
        if len(set(n.lower() for n in names)) != len(names):
            return "Two annotations share a name."
        for a in self.annotations:
            if a.kind == "choice" and len([c for c in a.choices if c.strip()]) < 2:
                return f"{a.name.strip()}: a choice annotation needs at least two choices."
            for text in a.values.values():
                problem = a.value_problem(text)
                if problem:
                    return problem
        return ""

    def plate_ok(self) -> bool:
        """A round dish needs its diameter (the scale Cropping starts from); a rectangular plate needs nothing."""
        return self.plate != "circular" or (_is_number(self.dish_mm) and float(self.dish_mm) > 0)

    def block_text(self, step: int) -> str:
        if step == 0:
            return "Add photos to continue." if not self.photos else "Choose a free project name to continue."
        if step == 1:
            return "Enter the dish diameter in millimeters to continue."
        if step == 2:
            return "Add a variable to continue." if not self.variables else "Fix the marked levels to continue."
        if step == 3:
            return self.annotation_problem()
        return ""

    # ----- text for the steps, summary and review ----------------------------
    def plate_text(self) -> str:
        if self.plate == "circular":
            return f"Round dish, {self.dish_mm} mm, {self.sections} section{'s' if self.sections != 1 else ''}"
        return f"Rectangular, {self.rows} × {self.cols} {'spots' if self.cells == 'spots' else 'cells'}"

    def scale_text(self) -> str:
        """Where Cropping's scale starts (each photo's scale can be changed there)."""
        if self.plate == "circular":
            return f"From the dish edge ({self.dish_mm} mm), set per photo in Cropping"
        return "A scale bar drawn on each photo in Cropping"

    def step_subtitles(self) -> list[str]:
        n = len(self.photos)
        return [f"{n} photo{'s' if n != 1 else ''}", self.plate_text(),
                f"{len(self.variables)} variable{'s' if len(self.variables) != 1 else ''} · {self.index_count} "
                f"{'index' if self.index_count == 1 else 'indices'}",
                f"{len(self.annotations)} annotation{'s' if len(self.annotations) != 1 else ''}",
                "Create the file"]

    def index_formula(self) -> str:
        if not self.variables:
            return "one Default level"
        design = [v for v in self.variables if v.kind != "replicate"]
        text = " × ".join(f"{len(v.values)} {(v.name.strip() or 'unnamed').lower()}" for v in design) or "one index"
        replicate = next((v for v in self.variables if v.kind == "replicate"), None)
        if replicate is not None:
            n = len(replicate.values)
            text += f", {n} plate{'s' if n != 1 else ''} each"
        return text

    def checks(self) -> list[tuple[str, str]]:
        """(state 'ok' / 'note' / 'bad', text) for the Before creating list."""
        slots = len(self.photos) * self.section_count
        name = self.name_problem()
        items = [("bad", "Project name is already used" if self.name.strip() else "Project needs a name") if name
                 else ("ok", "Project name is free"),
                 ("ok", f"{len(self.photos)} photos in order") if self.photos else ("bad", "Add at least one photo"),
                 ("ok", "Every level is named and unique") if self.variables_ok()
                 else ("bad", "A variable needs attention")]
        plates = self.plate_count
        each = "plate" if self.plates_per_index > 1 else "index"
        if plates == slots:
            items.append(("ok", f"One {each} per plate section"))
        elif plates < slots:
            items.append(("note", f"{slots - plates} plate sections beyond the {plates} {each}"
                                  f"{'s' if plates != 1 else ''} planned"
                                  + (" (extra plates of an index are fine)" if self.plates_per_index > 1 else "")))
        else:
            items.append(("note", f"{plates - slots} more {each}{'s' if plates - slots != 1 else ''} planned than "
                                  "plate sections; add photos or sections in Cropping"))
        return items

    def review_rows(self) -> list[tuple[str, int, str]]:
        """(title, step to edit, text)."""
        variables = "\n".join(
            f"{v.name} (replicate, {len(v.values)} interchangeable plate{'s' if len(v.values) != 1 else ''} "
            "per index)" if v.kind == "replicate" else
            f"{v.name} ({v.kind}, {len(v.values)} levels{', ' + v.unit if v.unit else ''})"
            for v in self.variables) or "None: one Default level"
        annotations = "\n".join(
            f"{a.name.strip()} ({dict(ANNOTATION_KINDS)[a.kind].lower()}"
            + (f", {a.unit.strip()}" if a.kind == "number" and a.unit.strip() else "")
            + (": " + ", ".join(c.strip() for c in a.choices if c.strip()) if a.kind == "choice" else "") + ")"
            for a in self.annotations) or "None"
        return [("Project", 0, f"{self.name.strip()}\nKept in your QuantaColony library"),
                ("Photos", 0, f"{len(self.photos)} photos, in the order set"),
                ("Plate", 1, self.plate_text()), ("Variables", 2, variables),
                ("Annotations", 3, annotations), ("Scale", 1, self.scale_text()),
                ("Indices", 2, f"{self.index_count} {'index' if self.index_count == 1 else 'indices'}"
                 + (f" × {self.plates_per_index} plates" if self.plates_per_index > 1 else "")
                 + ", placed on plate regions in Cropping")]

    # ----- photos ----------------------------------------------------------------
    def add_photos(self, paths) -> None:
        known = {p.resolve() for p in self.photos}
        for path in map(Path, paths):
            if path.resolve() not in known:
                self.photos.append(path)
                known.add(path.resolve())

    def sort_photos(self) -> None:
        """By the numbers in the file names (Plate_2 before Plate_10)."""
        self.photos.sort(key=lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)])

    def move_photo(self, index: int, step: int) -> int:
        j = index + step
        if 0 <= index < len(self.photos) and 0 <= j < len(self.photos):
            self.photos[index], self.photos[j] = self.photos[j], self.photos[index]
            return j
        return index

    # ----- output ---------------------------------------------------------------
    def specifications(self):
        """(VariableSpec list, plate dict, extra project settings) for DataManager.create_project."""
        from ..data.packets import LevelSpec, VariableSpec
        variables = self.variables or [VariableDraft("Sample", "nominal", "Custom", ["Default"])]
        specs, details, used = [], {}, set()
        for v in variables:
            key = _slug(v.name, used)
            if v.kind == "replicate":                     # plate numbers; more are added as plates need them
                levels = tuple(LevelSpec(str(i + 1), str(i + 1)) for i in range(len(v.values)))
            else:
                levels = tuple(LevelSpec(x.strip(), x.strip(), i == v.control) for i, x in enumerate(v.values))
            specs.append(VariableSpec(key, v.kind, levels, v.name.strip()))
            details[key] = {"preset": v.preset, "unit": v.unit.strip(), "additive": v.additive.strip()}
        plate = {"type": self.plate}
        if self.plate == "circular":
            plate.update(sections=self.sections, dish_mm=float(self.dish_mm))
        else:
            plate.update(rows=self.rows, cols=self.cols, cells=self.cells)
        extra = {"variable_details": details}
        if any(v.kind == "replicate" for v in variables):
            extra["plates_per_index"] = self.plates_per_index
        return specs, plate, extra

    def annotation_specs(self):
        """AnnotationSpec list for DataManager.create_project."""
        from ..data.packets import AnnotationSpec
        specs, used = [], set()
        for a in self.annotations:
            key = _slug(a.name, used)
            specs.append(AnnotationSpec(key, a.kind, a.name.strip(), a.unit.strip(),
                                        tuple(c.strip() for c in a.choices if c.strip())))
        return specs

    def photo_annotation_values(self) -> dict:
        """{photo path (str): {annotation key: value}} for the photos still in the project."""
        keys, used = {}, set()
        for a in self.annotations:
            keys[id(a)] = _slug(a.name, used)
        values: dict = {}
        paths = {str(p) for p in self.photos}
        for a in self.annotations:
            for path, text in a.values.items():
                if str(path) in paths and str(text).strip():
                    values.setdefault(str(path), {})[keys[id(a)]] = str(text).strip()
        return values


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _slug(name: str, used: set) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_") or "variable"
    key, k = base, 2
    while key in used:
        key, k = f"{base}_{k}", k + 1
    used.add(key)
    return key


def example_draft(taken=()) -> ProjectDraft:
    """The wizard's starting variables: one strain variable and one replicate variable."""
    genotype = VariableDraft("Genotype", "nominal", "Strain / genotype", ["Strain 1", "Strain 2"], control=0)
    replicate = VariableDraft("Replicate", "replicate", "Biological replicate", ["1", "2"])
    return ProjectDraft(taken=tuple(taken), variables=[genotype, replicate])
