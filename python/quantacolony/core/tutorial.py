"""The guided tours: each walks through one bundled project and draws the dissertation's figures (Qt-free).

A step explains why it is there, lists what it needs (one Need per control: the analysis, where
each variable goes, which levels are used, each setting), checks every need against what is on
screen, and once all are met shows what the result means. The tutorial panel lists the needs,
ticks each one as it is met and points at the control for the next one.

    TutorialState   what is on screen: the screen, the open project's tutorial marker, the Cropping
                    photo, the Colony Detection index, the Stats choices
    Need            one requirement: a kind, its arguments, an optional label; met(state)
    Glossary        the names the panel shows (analyses, slots, options, variables, levels)
    describe        a need's label and where its control is
    anchor_of       where the panel points for a need: (screen, kind, *arguments)
    Step            module, title, why, needs, finding; done when every need is met
                    (no needs: a reading step, done when the user moves on)
    Tour            one walkthrough: its steps and the tutorial project it runs on
    TOURS           every tour, in the order the picker lists them
"""

from __future__ import annotations

from dataclasses import dataclass, field

MODULE_SCREENS = ("project", "cropping", "detection", "stats")
SCREEN_NAMES = {"home": "Home", "project": "Project page", "cropping": "Cropping", "detection": "Colony Detection",
                "stats": "Stats"}


@dataclass(frozen=True)
class TutorialState:
    screen: str = ""                              # 'home', 'project', 'cropping', 'detection', 'stats', 'new'
    tutorial_open: bool = False                   # the open project is a tutorial project
    photo: int = -1                               # Cropping: position of the photo shown (0 is the first)
    index_values: tuple = ()                      # Colony Detection: level values of the index shown
    split: bool = False                           # Colony Detection: Split touching colonies is on
    growth: bool = False                          # Colony Detection: Show growth is on
    analysis: str = ""                            # Stats: the chosen analysis
    slots: dict = field(default_factory=dict)     # Stats: variable name -> slot key ('' pooled)
    measure: str = ""                             # Stats: the measure of an analysis with one row per index
    leave_out: bool = False                       # Stats: overplated plates left out
    project: str = ""                             # tutorial marker name of the open project ('' otherwise)
    options: dict = field(default_factory=dict)   # Stats: the chosen analysis's option values
    hidden: dict = field(default_factory=dict)    # Stats: variable name -> level values not used
    selection_on: bool = False                    # Stats: Plates set to one per index / group
    matched: tuple = ()                           # Stats: variables matched within
    window: tuple = (1, None)                     # Stats: the colony window of the selection
    target: float | str | None = None             # Stats: the target (None: median; 'least_deviation'; a count)
    not_chosen: dict = field(default_factory=dict)  # Stats: variable name -> levels drawn but not chosen from
    by_hand: frozenset = frozenset()              # Stats: photo names of the plates left out by hand
    color_by: str = ""                            # Stats: the Color by variable
    levels: dict = field(default_factory=dict)    # Stats: variable name -> every level value
    scheme: str = "raw"                           # Stats: the normalization


# ======================================================================================
# Needs
# ======================================================================================
@dataclass(frozen=True)
class Need:
    """One requirement of a step.

    kinds and arguments:
        project (name)                 the tutorial project is open
        screen (screen)                that module is open
        photo (position, name)         Cropping shows that photo
        index (values, name)           Colony Detection shows that index
        split / growth ()              the switch is on
        analysis (analysis)            the chosen analysis
        slot (analysis, variable, key) a variable in one part of the plot ('' pooled)
        levels (variable, used)        exactly these levels of a variable are used (() every level)
        color (variable)               Color by
        leave_out (on)                 Overplated: Leave out
        selection (on)                 Plates: One per index
        matched (variables)            Selection: match within exactly these
        window (low, high)             Selection: the colony window
        fit ()                         Selection: the target is the least total deviation
        by_hand (photo prefixes)       exactly these plates left out by hand (() none)
        option (analysis, key, value)  an analysis setting
        measure (key)                  Measure
    """

    kind: str
    args: tuple = ()
    text: str = ""                                # a label written by hand (else describe builds one)

    def met(self, s: TutorialState) -> bool:
        return bool(_CHECKS[self.kind](s, *self.args))


def _used(s: TutorialState, variable: str, used: tuple) -> bool:
    """Exactly these levels are used: every other level of the variable is switched off."""
    hidden = set(s.hidden.get(variable, ()))
    if not used:
        return not hidden
    everything = set(s.levels.get(variable, ())) or (hidden | set(used))
    return hidden == everything - set(used)


def _by_hand(s: TutorialState, prefixes: tuple) -> bool:
    if not prefixes:
        return not s.by_hand
    return (all(any(n.startswith(p) for n in s.by_hand) for p in prefixes)
            and all(any(n.startswith(p) for p in prefixes) for n in s.by_hand))


_CHECKS = {
    "project": lambda s, name: s.project == name and s.screen in MODULE_SCREENS,
    "screen": lambda s, screen: s.screen == screen,
    "photo": lambda s, position, _name: s.screen == "cropping" and s.photo == position,
    "index": lambda s, values, _name: s.screen == "detection" and tuple(s.index_values) == tuple(values),
    "split": lambda s: s.screen == "detection" and s.split,
    "growth": lambda s: s.screen == "detection" and s.growth,
    "analysis": lambda s, analysis: s.screen == "stats" and s.analysis == analysis,
    "slot": lambda s, analysis, variable, key: (s.screen == "stats" and s.analysis == analysis
                                                and s.slots.get(variable, "") == key),
    "levels": lambda s, variable, used: s.screen == "stats" and _used(s, variable, used),
    "color": lambda s, variable: s.screen == "stats" and s.color_by == variable,
    "leave_out": lambda s, on: s.screen == "stats" and s.leave_out == on,
    "selection": lambda s, on: s.screen == "stats" and s.selection_on == on,
    "matched": lambda s, variables: s.screen == "stats" and set(s.matched) == set(variables),
    "window": lambda s, low, high: s.screen == "stats" and tuple(s.window) == (low, high),
    "fit": lambda s: s.screen == "stats" and s.target == "least_deviation",
    "by_hand": lambda s, prefixes: s.screen == "stats" and _by_hand(s, prefixes),
    "option": lambda s, analysis, key, value: (s.screen == "stats" and s.analysis == analysis
                                               and str(s.options.get(key)) == str(value)),
    "measure": lambda s, key: s.screen == "stats" and s.measure == key,
    "scheme": lambda s, scheme: s.screen == "stats" and s.scheme == scheme,
}


@dataclass
class Glossary:
    """The names the panel shows; the interface fills it from the analyses and the open project."""

    analyses: dict = field(default_factory=dict)  # analysis id -> (title, section)
    slots: dict = field(default_factory=dict)     # (analysis id, slot key) -> title
    options: dict = field(default_factory=dict)   # (analysis id, option key) -> (title, {value: label})
    variables: dict = field(default_factory=dict)  # variable name -> (display name, {value: display})
    measures: dict = field(default_factory=lambda: {"colony_count": "Colonies counted",
                                                   "capture_ratio": "Capture ratio"})
    projects: dict = field(default_factory=lambda: {"crowding": "Crowding", "rescue": "Rescue",
                                                   "anbalagan": "Anbalagan", "historical": "Historical"})

    def variable(self, name: str) -> str:
        return self.variables.get(name, (name.replace("_", " ").capitalize(), {}))[0]

    def level(self, name: str, value: str) -> str:
        return self.variables.get(name, ("", {}))[1].get(value, value)

    def analysis(self, key: str) -> str:
        return self.analyses.get(key, (key.replace("_", " ").capitalize(), ""))[0]

    def slot(self, analysis: str, key: str) -> str:
        return self.slots.get((analysis, key), key.capitalize())

    def option(self, analysis: str, key: str, value: str) -> tuple[str, str]:
        title, labels = self.options.get((analysis, key), (key.capitalize(), {}))
        if value in labels:
            return title, labels[value]
        if value in self.variables:               # a variable choice (Option.variables_of)
            return title, self.variables[value][0]
        parts = []                                # a level choice (Option.levels_of): level values joined by ' | '
        for part in str(value).split(" | "):
            parts.append(next((levels[part] for _, levels in self.variables.values() if part in levels), part))
        return title, " · ".join(parts)


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def describe(need: Need, g: Glossary) -> tuple[str, str]:
    """(label, where): what the need asks for, and where its control is on screen."""
    kind, a = need.kind, need.args
    where = {
        "project": "Home: the Navarro Dissertation Projects folder",
        "screen": "Project page: the module's button",
        "photo": "Cropping: the arrows beside the photo name, or the photo list",
        "index": "Colony Detection: the plate name in the top bar opens the board of plates",
        "split": "Colony Detection: Detect automatically, in the panel on the right",
        "growth": "Colony Detection: the summary card, in the panel on the right",
        "analysis": "Stats: the analysis list on the left" + (f", group {g.analyses[a[0]][1]}" if a and a[0] in
                                                              g.analyses and g.analyses[a[0]][1] else ""),
        "slot": "Stats: Compare section" + (": click its lit chip to turn it off" if a and not a[-1] else ""),
        "color": "Stats: Compare section, Color by",
        "levels": "Stats: Plates section, Levels used: the variable's button",
        "leave_out": "Stats: Plates section, Plate rules",
        "selection": "Stats: Plates section, Plate rules",
        "matched": "Stats: Plates section, Selection… (Match within)",
        "window": "Stats: Plates section, Selection… (Colonies)",
        "fit": "Stats: Plates section, Selection… (Target)",
        "by_hand": "Stats: Plates section, Plates… (the Use column)",
        "option": "Stats: Drawing section",
        "measure": "Stats: Drawing section, Measure",
        "scheme": "Stats: Drawing section, Normalization",
    }[kind]
    if need.text:
        return need.text, where
    if kind == "project":
        label = f"Open the {g.projects.get(a[0], a[0])} project"
    elif kind == "screen":
        label = f"Open {SCREEN_NAMES.get(a[0], a[0])}"
    elif kind == "photo":
        label = f"Show photo {a[0] + 1} ({a[1]})"
    elif kind == "index":
        label = f"Show plate {a[1]}"
    elif kind == "split":
        label = "Split touching colonies: on"
    elif kind == "growth":
        label = "Show growth on the image: on"
    elif kind == "analysis":
        label = f"Analysis: {g.analysis(a[0])}"
    elif kind == "slot":
        analysis, variable, key = a
        label = (f"{g.variable(variable)}: pooled (no chip lit)" if not key else
                 f"{g.variable(variable)} in {g.slot(analysis, key)}")
    elif kind == "levels":
        variable, used = a
        label = (f"{g.variable(variable)}: every level used" if not used else
                 f"{g.variable(variable)}: only {_join([g.level(variable, v) for v in used])}")
    elif kind == "color":
        label = f"Color by: {g.variable(a[0]) if a[0] else 'Analysis colors'}"
    elif kind == "scheme":
        label = "Normalization: " + {"raw": "Raw data"}.get(a[0], a[0])
    elif kind == "leave_out":
        label = f"Overplated: {'Leave out' if a[0] else 'Include'}"
    elif kind == "selection":
        label = f"Plates: {'One per index' if a[0] else 'All'}"
    elif kind == "matched":
        label = f"Match within: only {_join([g.variable(v) for v in a[0]])}"
    elif kind == "window":
        low, high = a
        label = f"Colonies: {low} or more" + (f", up to {high}" if high else " (no upper limit)")
    elif kind == "fit":
        label = "Target: Least total deviation"
    elif kind == "by_hand":
        label = ("Every plate ticked under Use" if not a[0] else f"Untick Use for {_join(list(a[0]))}")
    elif kind == "option":
        title, value = g.option(*a)
        label = f"{title}: {value}"
    elif kind == "measure":
        label = f"Measure: {g.measures.get(a[0], a[0])}"
    else:
        label = kind
    return label, where


def anchor_of(need: Need, g: Glossary) -> tuple:
    """Where the panel points: (screen, kind, *arguments); the interface finds the widget."""
    kind, a = need.kind, need.args
    if kind == "project":
        return ("home", "project", a[0])
    if kind == "screen":
        return ("project", "module", a[0])
    if kind == "photo":
        return ("cropping", "photo")
    if kind == "index":
        return ("detection", "index")
    if kind in ("split", "growth"):
        return ("detection", kind)
    if kind == "analysis":
        return ("stats", "analysis", a[0])
    if kind == "slot":
        analysis, variable, key = a
        return ("stats", "role", g.slot(analysis, key), g.variable(variable)) if key else \
            ("stats", "lit", g.variable(variable))
    if kind == "levels":
        return ("stats", "levels", g.variable(a[0]))
    if kind == "color":
        return ("stats", "color")
    if kind == "leave_out":
        return ("stats", "rule", "Overplated")
    if kind == "selection":
        return ("stats", "rule", "Plates")
    if kind in ("matched", "window", "fit"):
        return ("selection", {"matched": "match", "window": "window", "fit": "target"}[kind])
    if kind == "by_hand":
        return ("plates", a[0][0] if a[0] else "")
    if kind == "option":
        return ("stats", "setting", g.option(*a)[0])
    if kind == "measure":
        return ("stats", "setting", "Measure")
    if kind == "scheme":
        return ("stats", "setting", "Normalization")
    return ()


# ======================================================================================
# Steps and tours
# ======================================================================================
@dataclass(frozen=True)
class Step:
    key: str
    module: str
    title: str
    why: str                                      # what the step is for, shown with its needs
    needs: tuple = ()                             # Need: every one met marks the step done
    finding: str = ""                             # what the result shows, once done
    figure: str = ""                              # the dissertation figure it draws
    reasons: dict = field(default_factory=dict)   # need_key -> why this change, for this experiment

    @property
    def reading(self) -> bool:
        return not self.needs


@dataclass(frozen=True)
class Tour:
    """One guided walkthrough: the steps, and the tutorial project it runs on."""

    key: str
    title: str
    description: str
    project: str                                  # tutorial marker name of its project
    steps: tuple[Step, ...]
    kind: str = "figures"                         # 'guide': learning QuantaColony; 'figures': a dissertation chapter


KINDS = (("guide", "Learn QuantaColony", "From photos to figures, one module at a time."),
         ("figures", "Regenerate the dissertation figures", "Each tour draws one chapter's figures from its "
                                                              "project."))


def need_key(need: Need) -> str:
    """The key a step's reasons use: 'analysis', 'slot:genotype', 'levels:series', 'option:axis', 'leave_out'..."""
    if need.kind == "slot":
        return f"slot:{need.args[1]}"
    if need.kind == "levels":
        return f"levels:{need.args[0]}"
    if need.kind == "option":
        return f"option:{need.args[1]}"
    return need.kind


ANALYSIS_WHY = {
    "box": "A box plot shows each group's colony sizes as a median line, a box holding the middle half of the "
           "colonies, and whiskers for the rest. It compares whole distributions, not only averages.",
    "plate_photos": "The photos let you check by eye what the numbers mean: the outlines are the colonies the "
                    "program measured.",
    "plate_metrics": "Plate metrics gives one number per plate (colony count, capture ratio, mean size), so "
                     "you compare plates as units.",
    "fidelity": "Measurement fidelity grades how precisely each colony's size can be read from the photo. Small "
                "colonies span few pixels, so their sizes carry more error.",
    "density": "A density curve is a smoothed histogram of colony sizes. Its peaks show size classes that a "
               "single average hides.",
    "plate_statistic": "Per-plate statistic computes one number per plate (a median, a CV, a colony count) and "
                       "follows it across passages.",
    "shape_classifier": "The shape classifier asks whether the shape of a plate's size distribution (its CV and "
                        "skew) is enough to tell genotypes apart, with no look at the labels.",
    "component_locations": "The mixture model splits each plate's sizes into a few bell-shaped components, one "
                           "per colony class, and plots where each component sits.",
    "threshold_share": "Share beyond a size gives each plate one number: the fraction of its colonies smaller "
                       "than a cutoff. It counts how much of a plate the small class has taken over.",
    "plate_distributions": "Per-plate distributions draws one glyph per plate, so you see each plate's own "
                           "spread of sizes beside the others.",
}
SLOT_WHY = {
    "x": "Putting {var} on the X axis lines its levels up left to right, so you read the change across {var}.",
    "groups": "Groups gives each {var} level its own color beside the others at every X position, so they "
              "compare directly.",
    "rows": "Each {var} level gets its own row of the grid.",
    "columns": "Each {var} level gets its own column of the grid.",
    "panels": "Each {var} level gets its own panel, so the curves of different levels do not overlap.",
    "curves": "Each {var} level gets its own curve on one plot, so their shapes overlay.",
    "classes": "The classifier tells the {var} levels apart; they are the classes it learns.",
    "clusters": "Each {var} level gets its own block of the figure.",
    "levels": "Each {var} level gets its own column inside every block.",
}
OPTION_WHY = {
    ("axis", "log"): "Colony radii span from a few hundred to a few thousand micrometers. On a log axis the "
                     "same fold change looks the same size anywhere, so small and large colonies stay readable.",
    ("numbers", "show"): "The colony count and median above each box let you read the exact values the figure "
                         "reports.",
    ("view", "separate"): "Separate panels keep each group's boxes apart, so a crowded figure stays legible.",
    ("view", "together"): "Together puts every group on one axis, so the lineages compare at a glance.",
    ("values", "count_ratio_hce"): "Colony count, capture ratio and HCE under each photo tie the picture to the "
                                   "numbers that judge whether the plate is overplated.",
    ("values", "count_median_ratio"): "Colony count, median radius and capture ratio under each photo tie what "
                                      "you see to the size the figure reports.",
    ("bar", "5"): "A 5 mm scale bar gives every photo the same physical scale reference.",
    ("bar", "10"): "A 10 mm scale bar gives every photo the same physical scale reference.",
    ("cell", "colonies"): "Each cell grades the median colony over every colony of the cell's plates.",
    ("cell", "plates"): "Each cell averages the plates' own medians, so a plate with many colonies does not "
                        "outweigh the others.",
    ("scale", "log"): "Size classes separate by fold change, so the curves are drawn over log radius, where a "
                      "small and a large class become two clean peaks.",
    ("peaks", "show"): "Marking the peaks reads off the size of each colony class.",
    ("statistic", "Colony Count"): "Colony count per plate is the classic senescence readout: senescent cells "
                                   "found fewer colonies.",
    ("statistic", "Median"): "The median colony size per plate follows how fast cells divide, which count alone "
                             "does not show.",
    ("statistic", "Coefficient Of Variation"): "CV is the spread of sizes relative to their mean. A plate with "
                                               "one tight size class has a low CV.",
    ("band", "control"): "The band spans the wild-type lineages, so a mutant line outside it differs from "
                         "normal variation between wild-type lineages.",
    ("lines", "selected"): "The lines join the selected count-matched plates, the ones the comparison rests on.",
    ("others", "range"): "The other plates with 100 to 200 colonies show whether the selected plate is typical "
                         "of plates at similar crowding.",
    ("others", "none"): "Hiding the other plates leaves only the selected plates to compare.",
    ("layout", "panels"): "One panel per genotype keeps each genotype's plates readable.",
    ("layout", "together"): "One axis for both genotypes puts their curves side by side.",
    ("layout", "plates"): "One column per plate shows each plate's own components.",
    ("reference", "first"): "Every plate's components are compared with the wild-type selected plate, the "
                            "baseline for normal colony sizes.",
    ("reference", "none"): "No reference plate: this figure follows components over time.",
    ("overlap", "show"): "The overlap row gives one number per plate: how much its size distribution shares "
                         "with the wild-type reference (1 is identical, 0 is fully separate).",
    ("overlap", "hide"): "The overlap row is not part of this figure.",
    ("trajectory", "chosen"): "Lines through the selected plates follow each component from passage to passage.",
    ("cutoff", "450"): "450 µm sits between the small senescent class and the normal large class seen in the "
                       "density curves.",
    ("view", "together"): "Together puts every group on one axis.",
    ("space", "none"): "No spacer columns: the boxes sit side by side.",
    ("bar", "none"): "No scale bar: the photos are compared by their numbers.",
    ("values", "hide"): "No numbers over the bars: the points and bars carry the values.",
    ("peaks", "hide"): "Peak marks are not part of this figure.",
    ("lines", "means"): "The lines join the group means across passages.",
    ("others", "all"): "Every plate is drawn as a dot.",
    ("band", "none"): "No control band: the groups compare directly.",
    ("trajectory", "none"): "No trajectory lines: this figure compares plates at one passage.",
    ("counts", "show"): "The colony count under each glyph says how many colonies its shape rests on.",
    ("side", "below"): "Below counts the colonies smaller than the cutoff: the small class.",
    ("count", "colonies"): "Counting colonies needs no mixture fit: every colony below the cutoff counts.",
    ("view", "table"): "A table gives the exact value of every cell, the form the dissertation table reports.",
    ("view", "bars"): "Bars show the values as heights, so differences read at a glance.",
    ("values", "show"): "The number over each bar gives its exact value.",
    ("glyph", "auto50"): "A violin needs enough colonies to show a shape; below 50 colonies each colony is drawn "
                         "as a point.",
    ("target", "dbf4-1"): "Target is the class the classifier tries to pick out: dbf4-1.",
    ("reference", "wild_type"): "Reference is the class dbf4-1 is told apart from: wild type.",
}


def reason(need: Need, step: Step, g: Glossary) -> str:
    """Why the step makes this change, in terms of the experiment."""
    key = need_key(need)
    if key in step.reasons:
        return step.reasons[key]
    kind, a = need.kind, need.args
    if kind == "project":
        return "Each tour runs on one project; the steps that follow read this project's plates."
    if kind == "screen":
        return {"cropping": "Cropping finds each plate's dish, which sets the scale in micrometers.",
                "detection": "Colony Detection marks every colony; the analyses read these marks.",
                "stats": "Stats draws every figure from the colonies detected in the project."}.get(a[0], "")
    if kind == "analysis":
        return ANALYSIS_WHY.get(a[0], "This analysis draws the figure the step is after.")
    if kind == "slot":
        analysis, variable, slot = a
        if not slot:
            return ("Not part of this comparison. Pooling keeps a choice left from an earlier figure from "
                    "splitting this one.")
        return SLOT_WHY.get(slot, "This places {var} in the figure.").format(var=g.variable(variable))
    if kind == "levels":
        variable, used = a
        if not used:
            return "Every level enters this figure, after an earlier step narrowed it."
        return (f"Only {_join([g.level(variable, v) for v in used])} of {g.variable(variable)} belong to this "
                "comparison.")
    if kind == "scheme":
        return ("Raw data plots each measurement as measured. Normalizing to a control would divide it by the "
                "control's value, which this figure does not do.")
    if kind == "color" and not a[0]:
        return "Analysis colors give each group the analysis's own colors, the ones the figure uses."
    if kind == "color":
        return f"Coloring by {g.variable(a[0])} keeps each {g.variable(a[0]).lower()} the same color in every panel."
    if kind == "leave_out":
        return ("Plates below 25% capture ratio had most of their growth merge into lawn, so their counts and "
                "sizes are unreliable." if a[0] else "This figure shows every plate, the overplated one too.")
    if kind == "selection":
        return ("Colony size depends on crowding, so plates are compared at similar colony counts: one plate per "
                "genotype and passage, the one nearest a common target." if a[0] else
                "This figure uses every plate, not one per group.")
    if kind == "matched":
        return ("Each genotype and passage keeps one plate, chosen from its densities and replicates, so every "
                "cell of the comparison has exactly one plate.")
    if kind == "window":
        return (f"Plates with fewer than {a[0]} colonies carry too few colonies for a stable size distribution, "
                "so they are never chosen.")
    if kind == "fit":
        return ("The target is the colony count that keeps the chosen plates closest to one shared count: summed "
                "over every genotype and passage, the chosen plates' distances from it are as small as the plates "
                "allow. Every chosen plate then sits at nearly the same crowding.")
    if kind == "by_hand":
        return "Plates left out by hand stay out of every figure until ticked again."
    if kind == "option":
        return OPTION_WHY.get((a[1], str(a[2])), "This setting draws the figure the way the step needs.")
    if kind == "measure":
        return {"colony_count": "Colony count says how crowded each plate is.",
                "capture_ratio": "The capture ratio says how much of the plate's growth resolved into separate "
                                 "colonies; below 25% the plate is overplated."}.get(a[0], "")
    return ""


def is_done(step: Step, state: TutorialState) -> bool:
    return bool(step.needs) and all(need.met(state) for need in step.needs)


def unmet(step: Step, state: TutorialState) -> list[int]:
    """Positions of the needs not met yet."""
    return [i for i, need in enumerate(step.needs) if not need.met(state)]


VARIABLES = {"crowding": ("dilution_level",), "historical": ("genotype", "passage"),
             "anbalagan": ("genotype", "passage", "source_figure"),
             "rescue": ("genotype", "passage", "density", "series")}


def stats(project: str, analysis: str, slots: dict | None = None, levels: dict | None = None, color: str | None = None,
          leave_out: bool | None = None, selection: bool | None = None, matched: tuple | None = None,
          window: tuple | None = None, fit: bool = False, by_hand: tuple | None = None,
          options: dict | None = None, measure: str = "") -> tuple:
    """The needs of a Stats step, in the order the screen offers the controls: the analysis, then Compare
    (where each variable goes, Color by), then Plates (levels used, plate rules, Selection…, Plates…),
    then Drawing (settings, Measure). Every variable of the project that the step does not place is pooled,
    so a choice left from an earlier step cannot split the figure."""
    needs = [Need("screen", ("stats",)), Need("analysis", (analysis,))]
    needs += [Need("slot", (analysis, variable, key)) for variable, key in (slots or {}).items()]
    needs += [Need("slot", (analysis, variable, "")) for variable in VARIABLES.get(project, ())
              if variable not in (slots or {})]
    if color is not None:
        needs.append(Need("color", (color,)))
    needs += [Need("levels", (variable, tuple(used))) for variable, used in (levels or {}).items()]
    if leave_out is not None:
        needs.append(Need("leave_out", (leave_out,)))
    if selection is not None:
        needs.append(Need("selection", (selection,)))
    if matched is not None:
        needs.append(Need("matched", (tuple(matched),)))
    if window is not None:
        needs.append(Need("window", tuple(window)))
    if fit:
        needs.append(Need("fit"))
    if by_hand is not None:
        needs.append(Need("by_hand", (tuple(by_hand),)))
    needs += [Need("option", (analysis, key, value)) for key, value in (options or {}).items()]
    if measure:
        needs.append(Need("measure", (measure,)))
    return tuple(needs)


# ======================================================================================
# Crowding: the wild-type dilution series (chapter 10, figures 10.1 to 10.3)
# ======================================================================================
DILUTION = "dilution_level"


def _crowding_steps() -> tuple[Step, ...]:
    p = "crowding"
    return (
        Step("open", "Projects", "Open the Crowding project",
             "Every tour runs on one project of the Navarro Dissertation Projects folder. Crowding holds ten "
             "plates from one wild-type culture, plated at dilutions from 100X to 128000X.",
             (Need("project", (p,)),),
             "The project page lists the three steps of every project: Cropping, Colony Detection and Stats. "
             "All ten plates here are cropped and detected, so each module shows a finished result. Every "
             "plate was incubated and photographed with the others; only the dilution differs."),
        Step("dish", "Cropping", "The dish sets the scale",
             "Colony sizes come out in micrometers because each plate's scale comes from its dish. Photo 8 "
             "was taken from farther away than the others, which makes it the useful one to look at.",
             (Need("screen", ("cropping",)), Need("photo", (7, "32000X"))),
             "The circle sits on the edge of the agar. Each plate's scale in pixels per millimeter comes from "
             "the dish's known diameter (90 mm). Photo 8's dish looks smaller and its scale is smaller with it "
             "(15.4 px/mm against about 16.3 on the other plates), so its colony sizes still agree with the "
             "other plates."),
        Step("sparse", "Colony Detection", "A sparse plate",
             "Detection finds colonies automatically and keeps the corrections made by hand. A sparse plate "
             "shows the simple case: every colony stands alone.",
             (Need("screen", ("detection",)), Need("index", (("128000",), "128000X"))),
             "Every colony stands apart from its neighbors. 16 of the 23 colonies on this plate were added by "
             "hand: automatic detection gives the starting point, and the corrections make the result that "
             "every module reads."),
        Step("crowded", "Colony Detection", "Touching colonies",
             "On a crowded plate colonies touch and merge. Show growth tints the growth that no colony "
             "outline covers, and splitting cuts touching colonies apart.",
             (Need("screen", ("detection",)), Need("index", (("500",), "500X")), Need("growth"), Need("split")),
             "Touching colonies form one blob. Show growth tints the growth left unmarked amber. With splitting "
             "on, detection cuts pairs and chains apart where they narrow and draws the pieces violet. Split "
             "colonies count toward colony counts; Stats can leave them out of sizes, and the capture ratio "
             "treats them as merged growth. To keep the saved result, choose Discard changes when you leave "
             "this plate."),
        Step("size", "Stats", "Crowding sets colony size",
             "One box per dilution compares colony radius across the series. A log axis keeps the small and "
             "the large colonies readable on one plot.",
             stats(p, "box", slots={DILUTION: "x"}, options={"axis": "log", "numbers": "show"}),
             "Median colony radius rises about 3-fold, from 572 µm at 100X to 1682 µm at 128000X. Nothing "
             "differs between these plates except how many cells were spread, so crowding alone changes "
             "colony size.", "Figure 10.1"),
        Step("photos", "Stats", "What the capture ratio sees",
             "The capture ratio measures how much of a plate's growth resolved into separate colonies. "
             "The plate photos show it next to each plate's outlines.",
             stats(p, "plate_photos", slots={DILUTION: "columns"}, options={"values": "count_ratio_hce"}),
             "Each plate with its detected colonies outlined. Growth outside every outline did not resolve "
             "into separate colonies. The capture ratio is the share of a plate's growth lying in growth "
             "pieces that hold exactly one marked colony: 11.3% on 100X and 42.7% on 500X. The 25% cutoff "
             "falls between the two plates.", "Figure 10.2"),
        Step("count", "Stats", "Colony count tracks crowding",
             "Plate metrics plots one value per plate. Colony count is the measure that says how crowded "
             "each plate is.",
             stats(p, "plate_metrics", slots={DILUTION: "x"}, measure="colony_count"),
             "A 256-fold change in dilution (500X to 128000X) gives an 18-fold change in colony count: on "
             "denser plates, fewer of the cells spread establish separate colonies. Colony count, not the "
             "number of cells plated, is what tracks crowding. At 100X the count drops below 500X: colonies "
             "merged."),
        Step("capture", "Stats", "The capture ratio finds overplated plates",
             "The same plot with the capture ratio shows which plates fall below the 25% cutoff.",
             stats(p, "plate_metrics", slots={DILUTION: "x"}, measure="capture_ratio"),
             "100X falls below the 25% line: most of its growth merged into lawn, so its colony count and "
             "sizes cannot be trusted. 500X and every sparser plate stay above it. The cutoff works from the "
             "photo alone, so it applies to plates with no dilution series behind them."),
        Step("leave_out", "Stats", "Leave overplated plates out",
             "One switch leaves every plate below the cutoff out of every analysis.",
             (Need("screen", ("stats",)), Need("leave_out", (True,))),
             "100X now leaves every analysis, and the notes under the figure say so. Use the same switch on "
             "your own projects."),
        Step("fidelity", "Stats", "Measurement precision",
             "Each colony's radius is uncertain by about half a pixel at its edge. Measurement fidelity "
             "grades how much that matters on each plate.",
             stats(p, "fidelity", slots={DILUTION: "rows"}, leave_out=False, options={"cell": "colonies"}),
             "Colonies spanning few pixels measure poorly. The median colony of every plate reaches High "
             "fidelity except the two densest, at Good (5.2% and 5.1%). Measurement precision limits no "
             "comparison in this series. Check this on your own photos before comparing sizes.", "Figure 10.3"),
        Step("design", "Your experiments", "Plan for crowding",
             "Colony size depends on how many colonies share a plate, and how many establish depends on the "
             "strain itself. So: plate each condition at several densities, so their colony counts overlap; "
             "compare plates with similar colony counts; leave out plates below 25% capture ratio; and check "
             "measurement fidelity. Press Finish to end the tour."),
    )


# ======================================================================================
# The historical est mutant plates (figures 8.7, 10.6, 10.9 and table 10.3)
# ======================================================================================
def _historical_steps() -> tuple[Step, ...]:
    p = "historical"
    every = ("est1", "tlc1", "est21", "est31", "est41", "est11")
    return (
        Step("open", "Projects", "Open the Historical project",
             "Twenty streak-out sectors read from the published est mutant photographs: six genotypes across "
             "four passages.",
             (Need("project", (p,)),),
             "Source paper and figure are annotations on every plate. A lineage's passages share one plate, so "
             "they share crowding."),
        Step("fidelity", "Stats", "Which sets can be sized",
             "Published photographs carry few pixels per colony, so measurement fidelity decides which sets "
             "enter the size analysis.",
             stats(p, "fidelity", slots={"genotype": "rows", "passage": "columns"}, leave_out=False),
             "The tlc1Δ sets at passages 2 and 3 fall at Low fidelity and leave the size analysis. Cells marked "
             "no data are sectors with no verifiable colony. Streak-out sectors are not spread plates, so every "
             "one of them sits below the 25% capture ratio; the exclusions here come from fidelity and from "
             "lost colonies, never from the overplated rule.", "Figure 8.7"),
        Step("photos", "Stats", "Retained and excluded lineages",
             "The photos of two lineages show what the fidelity grades mean on the plate: one stays measurable, "
             "one does not.",
             stats(p, "plate_photos", slots={"genotype": "rows", "passage": "columns"},
                   levels={"genotype": ("est11", "tlc1")}, options={"values": "count_median_ratio", "bar": "5"}),
             "est1-1 stays separable at every passage while its median radius falls from 1028 to 395 µm, so it "
             "enters the size analysis. tlc1Δ is down to three faint colonies by passage 3 and never forms a "
             "sector with measurable colonies again.", "Figure 10.6"),
        Step("sizes", "Stats", "Size falls with passage",
             "Two lineages with measurable colonies at every passage show how colony size changes over "
             "senescence.",
             stats(p, "box", slots={"passage": "x", "genotype": "groups"}, color="genotype",
                   levels={"genotype": ("est41", "est11")},
                   options={"view": "together", "axis": "log", "numbers": "show"}),
             "est4-1 falls from 1288 to 555 µm with 5 to 15 colonies per passage; est1-1 falls from 1028 to "
             "395 µm while its colony count rises from 17 to 80.", "Figure 10.9"),
        Step("table", "Stats", "Counts and medians",
             "Plate metrics gives the table's numbers, one row per sector. Save table writes them.",
             stats(p, "plate_metrics", slots={"passage": "x", "genotype": "groups"}, levels={"genotype": ()},
                   options={"view": "table"}, measure="colony_count"),
             "Table 10.3's colony counts. Measure: Median colony radius gives its median radii. Ratios to "
             "passage 1 are each value divided by its genotype's passage 1 value: Normalization, Control "
             "(x-axis slot).", "Table 10.3"),
        Step("done", "Stats", "Every panel in one file",
             "Save figure writes any of these panels; the analysis PDF export (Export on the home page) "
             "collects them in one file. Press Finish to end the tour."),
    )


# ======================================================================================
# Anbalagan et al. (2011) senescence streak-outs (figures 10.4, 10.5, 10.7, 10.8, table 10.2)
# ======================================================================================
def _senescence_steps() -> tuple[Step, ...]:
    p = "anbalagan"
    lineages = {"passage": "x", "genotype": "groups", "source_figure": "groups"}
    return (
        Step("open", "Projects", "Open the Anbalagan project",
             "Thirty-two quadrants read from the streak-out photographs of Anbalagan et al. (2011), figures 2C "
             "and 8D: six genotypes across four streaks of 25 generations each.",
             (Need("project", (p,)),),
             "Source figure is a variable: the two figures hold separate lineages from separate experiments. "
             "The generation count travels with each plate as an annotation."),
        Step("fidelity", "Stats", "Measurement fidelity",
             "Before comparing sizes, fidelity says which colony sets these photographs can measure.",
             stats(p, "fidelity", slots={"genotype": "rows", "source_figure": "rows", "passage": "columns"}),
             "Four sets fall at Low fidelity: cdc13-2 at 75 generations, the Figure 8D wild type at 75, and "
             "rif1Δ est2Δ at 75 and 100. They stay in the figures as collected and out of size comparisons.",
             "Figure 10.5"),
        Step("photos", "Stats", "Two quadrants of one plate",
             "Wild type and cdc13-2 share each plate of Figure 2C, so their quadrants share crowding and "
             "photography. Comparing them at the first and last streak isolates the genotype.",
             stats(p, "plate_photos", slots={"genotype": "rows", "passage": "columns"},
                   levels={"genotype": ("Wild_Type", "cdc132"), "source_figure": ("2C",), "passage": ("1", "4")},
                   options={"values": "count_median_ratio", "bar": "5"}),
             "The wild-type median stays near 500 µm (507 and 494 µm); cdc13-2 falls from 551 to 367 µm over "
             "the same streaks, on the same photographs.", "Figure 10.4"),
        Step("counts", "Stats", "Colony count by lineage",
             "Each lineage is followed across its streaks. Colony count is the usual senescence readout; the "
             "band spans the wild-type lineages.",
             stats(p, "plate_statistic", slots=lineages, color="genotype",
                   levels={"genotype": ("Wild_Type", "rif1", "cdc132", "est2"), "source_figure": (), "passage": ()},
                   options={"statistic": "Colony Count", "band": "control"}),
             "Each line is one lineage: solid for Figure 2C, dashed for 8D. Counts fall in both telomerase "
             "mutants: cdc13-2 from 60 to 8 by 75 generations, est2Δ from 43 to none at 100. Colony count marks "
             "both alleles as senescent.", "Figure 10.7 A, B"),
        Step("sizes", "Stats", "Colony size separates the alleles",
             "The same lineages with median colony size in place of colony count.",
             stats(p, "plate_statistic", slots=lineages, color="genotype",
                   options={"statistic": "Median", "band": "control"}),
             "cdc13-2 falls from 551 to 373 µm by 50 generations and stands at 367 µm at 100, below the "
             "wild-type band. est2Δ stays between 493 and 549 µm while its count falls roughly eightfold. Size "
             "separates the two alleles that count identifies as senescent.", "Figure 10.7 C, D"),
        Step("boxes", "Stats", "Every colony set",
             "One box per colony set, one panel per source figure: the full size distributions behind the "
             "lineage medians.",
             stats(p, "box", slots={"genotype": "x", "passage": "x", "source_figure": "groups"}, color="genotype",
                   levels={"genotype": (), "passage": (), "source_figure": ()},
                   options={"view": "separate", "axis": "log", "numbers": "show", "space": "genotype"}),
             "The counts and medians above the boxes are table 10.2's. Save table writes them; Plate metrics "
             "with Measure: Colonies counted gives the plate totals.", "Figure 10.8 and table 10.2"),
        Step("done", "Stats", "Every panel in one file",
             "Save figure writes any of these panels; the analysis PDF export (Export on the home page) "
             "collects them in one file. Press Finish to end the tour."),
    )


# ======================================================================================
# The dbf4-1 rescue (chapters 13 to 15)
# ======================================================================================
def _rescue_steps() -> tuple[Step, ...]:
    p = "rescue"
    main = {"series": ("main",)}
    choose = dict(selection=True, matched=("genotype", "passage"), window=(50, None), fit=True)
    large = "Double_P4_6Days_LargeColonies"
    return (
        Step("open", "Projects", "Open the Rescue project",
             "121 plates: four genotypes across five passages, plated at three densities, with enrichment and "
             "21-day series beside the main one.",
             (Need("project", (p,)),),
             "Plates with the same levels are technical replicates of one index. Cell estimate, plate size "
             "and incubation days are annotations on every plate."),
        Step("all_fidelity", "Stats", "Fidelity across every plate",
             "Before any comparison, fidelity across all 121 plates says how well these photographs measure "
             "colony size.",
             stats(p, "fidelity", slots={"genotype": "rows", "passage": "columns"}, levels={"series": ()},
                   selection=False, options={"cell": "plates"}),
             "All 25300 colonies: the median colony reads 10.2% uncertainty and covers 76 px, at Moderate "
             "fidelity. cdc13-2 P5 is the only cell at Moderate; every other cell is better.", "Figure 14.3"),
        Step("classifier", "Stats", "Shape tells dbf4-1 apart",
             "dbf4-1 colonies differ in the shape of their size distribution. The classifier asks whether CV "
             "and skew alone pick out dbf4-1 plates: Target is the class being identified, Reference the "
             "class it is told apart from.",
             stats(p, "shape_classifier", color="genotype", slots={"genotype": "classes"}, levels=main,
                   options={"target": "dbf4-1", "reference": "wild_type"}),
             "Each plate with at least 10 colonies is a point in CV and skew. Target and Reference each get a "
             "centroid, and every plate is assigned to dbf4-1 or not by the nearer one. 91 of 106 plates are "
             "assigned correctly (85.8% against a 73.6% baseline), 44 of 52 on wild type and dbf4-1 alone "
             "(84.6% against 53.8%).", "Figure 14.5 D"),
        Step("cv", "Stats", "CV and skew by passage",
             "The two measures behind the classifier, plate by plate across passages.",
             stats(p, "plate_statistic", color="genotype", slots={"passage": "x", "genotype": "groups"},
                   levels={"genotype": ("wild_type", "dbf4-1"), "series": ("main",)},
                   options={"statistic": "Coefficient Of Variation"}),
             "dbf4-1 concentrates its colonies in one upper mode, which gives it lower CV than wild type at "
             "every passage. Set Statistic to Skew: dbf4-1 is lower there too. Each dot is one plate; the "
             "lines join the passage means.", "Figure 14.5 A, B"),
        Step("pair", "Stats", "One count-matched pair",
             "Density curves at one passage and one density tier compare the two genotypes at matched colony "
             "counts.",
             stats(p, "density", color="genotype", slots={"genotype": "curves"},
                   levels={"genotype": ("wild_type", "dbf4-1"), "passage": ("2",), "density": ("2",)},
                   options={"scale": "log"}),
             "Each curve pools the replicate plates at the middle tier: 337 wild-type colonies from three "
             "plates and 286 dbf4-1 colonies from two. Wild type spreads across a lower and an upper mode; "
             "dbf4-1 has one dominant peak at 859 µm, beside the upper wild-type mode at 840 µm.",
             "Figure 14.5 C"),
        Step("selection", "Stats", "One plate per genotype and passage",
             "Comparing plates fairly needs plates with similar colony counts. One per index keeps, for each "
             "genotype and passage, the plate whose colony count is closest to the target; the colony window "
             "keeps very sparse plates out of the choice.",
             stats(p, "plate_metrics", color="genotype", slots={"passage": "x", "genotype": "groups"},
                   levels={"series": ("main",), "passage": (), "density": (), "genotype": ()},
                   options={"view": "table"}, measure="colony_count", **choose),
             "Among standard six-day plates with at least 50 colonies and a capture ratio of 25% or more, each "
             "genotype and passage keeps the plate closest to 144 colonies. 144 is the target that keeps the "
             "chosen plates closest to one shared count: summed over the 19 filled cells, they sit 1113 "
             "colonies from it, and any other target gives a larger sum. cdc13-2 P4 stays empty: none of its "
             "plates qualifies. Selection… lists every candidate "
             "and why each was passed over; Save table writes the table.", "Table 14.1"),
        Step("boxes", "Stats", "The selected plates",
             "The size distributions of the selected plates, one panel per genotype.",
             stats(p, "box", slots={"passage": "x", "genotype": "groups"}, color="genotype",
                   options={"view": "separate", "axis": "log", "numbers": "show"}, **choose),
             "From passage 3 the double mutant declines less than cdc13-2, which falls to a 220 µm median at "
             "P5 while the double mutant holds near 680 µm. Ratios to wild type and to P1 (table 14.1) come "
             "from Per-plate statistic, Statistic: Median, with Normalization: Control (groups slot) and "
             "Control (x-axis slot).", "Figure 14.4"),
        Step("selected_fidelity", "Stats", "Fidelity of the selected plates",
             "The same fidelity check, now on the plates the comparison uses.",
             stats(p, "fidelity", slots={"genotype": "rows", "passage": "columns"},
                   options={"cell": "colonies"}, **choose),
             "Every selected plate reaches Moderate fidelity or better. cdc13-2 P3 and P5 and the double mutant "
             "P4 are at Moderate (11.7%, 19.8% and 10.4%); every other plate reads Good or High.", "Figure 14.2"),
        Step("p5_photos", "Stats", "The selected P5 plates",
             "Photos of the last passage show the size difference directly on the plates.",
             stats(p, "plate_photos", slots={"genotype": "columns"}, levels={"passage": ("5",)},
                   options={"values": "count_median_ratio", "bar": "10"}, **choose),
             "At P5 cdc13-2 colonies fall below 300 µm (220 µm median over 925 colonies), while the double "
             "mutant stays inside the wild-type size range (680 µm).", "Figure 14.1"),
        Step("classes", "Stats", "Two colony classes at passage 3",
             "At passage 3 both cdc13-2 genotypes begin to form small colonies. Log-radius density curves with "
             "their peaks marked show the two classes.",
             stats(p, "density", color="genotype", slots={"genotype": "panels"},
                   levels={"passage": ("3",), "genotype": ("cdc13-2", "dbf4-1_cdc13-2")},
                   options={"scale": "log", "peaks": "show"}, **choose),
             "Both selected plates carry a large and a small colony class: cdc13-2 peaks at 290 and 576 µm, the "
             "double mutant at 344 and 612 µm. Plate photos with Genotype in Columns shows the two plates.",
             "Figure 13.1"),
        Step("p5_components", "Stats", "P5 mixture components",
             "A mixture model splits each plate's sizes into components. Comparing every P5 plate's "
             "components with the wild-type plate shows which genotypes keep wild-type colony sizes.",
             stats(p, "component_locations", color="genotype", slots={"genotype": "clusters", "passage": "levels"},
                   levels={"genotype": (), "passage": ("5",)},
                   options={"layout": "plates", "reference": "first", "overlap": "show"}, **choose),
             "The double-mutant components sit at the wild-type positions (overlap 0.42 to 0.76) while every "
             "cdc13-2 component falls below 300 µm (0.00 to 0.06). Wild type reads 0.84 against itself and "
             "dbf4-1 0.24 to 0.68. The first visit fits every plate; the fits are kept in the project.",
             "Figure 14.6"),
        Step("trajectory", "Stats", "Components across passages",
             "Following the components through every passage shows when the small class appears. cdc13-2 has "
             "no qualifying standard plate at P4, so its large-colony enriched P4 plates stand in for that "
             "passage. The double mutant already has a standard P4 plate, so its large-colony enriched plate "
             "is left out by hand.",
             stats(p, "component_locations", color="genotype", slots={"genotype": "clusters", "passage": "levels"},
                   levels={"genotype": (), "passage": (), "series": ("main", "large_enriched")}, by_hand=(large,),
                   options={"layout": "plates", "reference": "none", "overlap": "hide", "trajectory": "chosen"},
                   **choose),
             "The senescent class appears at P3 below the wild-type and dbf4-1 range, and by P5 it separates "
             "cdc13-2 from the double mutant. Lines join the selected plates' components by size rank, dotted "
             "across cdc13-2 P4, where no plate was selected.", "Figure 14.7"),
        Step("share", "Stats", "The share below 450 µm",
             "One number per plate: the share of its colonies below 450 µm, the size that separates the small "
             "class from the large one.",
             stats(p, "threshold_share", color="genotype", slots={"passage": "x", "genotype": "groups"},
                   levels={"genotype": (), "series": ("main",)},
                   options={"cutoff": "450", "lines": "selected", "others": "range", "layout": "panels"},
                   **choose),
             "Filled points and lines are the selected plates; open circles the other plates with 100 to 200 "
             "colonies. Small colonies make up every cdc13-2 colony by P5 (59% at P3), while the double-mutant "
             "share falls from 53 to 19 percent between P4 and P5. The dotted line crosses the empty cdc13-2 "
             "P4 cell.", "Figure 14.8"),
        Step("rate", "Stats", "One axis for both genotypes",
             "The selected-plate shares of the two cdc13-2 genotypes on one axis.",
             stats(p, "threshold_share", color="genotype", slots={"passage": "x", "genotype": "groups"},
                   levels={"genotype": ("cdc13-2", "dbf4-1_cdc13-2")},
                   options={"lines": "selected", "others": "none", "layout": "together"}, **choose),
             "The cdc13-2 share climbs to every colony by P5. The double-mutant share peaks at 53 percent at P4 "
             "and falls to 19 percent at P5. A dbf4-1 effect on the rate of senescence alone would delay the "
             "climb, not reverse it.",
             "Figure 15.1 C"),
        Step("enrichment", "Stats", "Selective enrichment at P4",
             "At P4 colonies were picked by class: large-colony and small-colony enriched plates sit beside "
             "the standard ones. Each plate's own distribution shows whether a class breeds true.",
             stats(p, "plate_distributions", slots={"genotype": "groups", "series": "groups"}, color="genotype",
                   levels={"passage": ("4",), "genotype": ("cdc13-2", "dbf4-1_cdc13-2"),
                           "series": ("main", "large_enriched", "small_enriched")},
                   selection=False, by_hand=(), options={"glyph": "auto50", "axis": "log"}),
             "One glyph per plate, a violin from 50 colonies; stars mark the 140 mm plates. Both cdc13-2 "
             "classes breed true, while small-enriched double-mutant colonies span the full range of sizes. "
             "Plate photos with Series in Rows and Genotype in Columns draws panel A.", "Figure 13.2 B"),
        Step("founded", "Stats", "Colonies founded at P4",
             "The small-colony enriched plates were seeded at matched input from the small colonies of P3. "
             "Their colony counts show how many of those cells still found a colony.",
             stats(p, "plate_metrics", color="genotype", slots={"genotype": "x"}, levels={"series": ("small_enriched",)},
                   measure="colony_count"),
             "cdc13-2 founds no colony; the double mutant founds 70 and 74 on its two plates (the bar is their "
             "mean). An effect on the rate of senescence alone predicts none for both.", "Figure 15.1 B"),
        Step("export", "Stats", "Every figure in one PDF",
             "Export on the home page writes the analysis PDF: tick the analyses you walked through and "
             "QuantaColony renders each one with the choices saved in the project, Measurement fidelity first. "
             "Press Finish to end the tour."),
    )


# ======================================================================================
# Why each change, step by step (need_key -> reason); steps fall back on reason()'s defaults
# ======================================================================================
PASSAGE_X = ("Passage is time in senescence: left to right follows each lineage as its telomeres shorten.")
REASONS = {
    ("crowding", "dish"): {
        "photo": "Photo 8 was taken from farther away than the others. Its dish shows how the scale corrects for "
                 "camera distance."},
    ("crowding", "sparse"): {"index": "At 128000X colonies are few and far apart: the easy case for detection."},
    ("crowding", "crowded"): {
        "index": "At 500X colonies are dense enough to touch each other.",
        "growth": "Show growth tints the growth that no colony outline covers: the part of the plate the "
                  "measurements miss.",
        "split": "Splitting cuts touching colonies apart where they narrow, so each one is counted."},
    ("crowding", "size"): {"slot:dilution_level": "Dilution is the only thing that differs between these plates, "
                                                  "so it goes on the X axis."},
    ("crowding", "photos"): {"slot:dilution_level": "One photo per dilution, from the densest plate to the "
                                                    "sparsest."},
    ("crowding", "count"): {"slot:dilution_level": "Dilution on the X axis shows how the count follows plating "
                                                   "density."},
    ("crowding", "capture"): {"slot:dilution_level": "Dilution on the X axis shows where along the series plates "
                                                     "become overplated."},
    ("crowding", "fidelity"): {"leave_out": "This figure grades every plate, the overplated one too."},
    ("historical", "fidelity"): {"leave_out": "Streak-out sectors are not spread plates and all sit below the 25% "
                                              "cutoff, so the overplated rule does not apply here."},
    ("historical", "photos"): {"levels:genotype": "est1-1 keeps measurable colonies at every passage and tlc1Δ "
                                                  "loses them. The pair shows what the fidelity grades mean on "
                                                  "the plate."},
    ("historical", "sizes"): {
        "levels:genotype": "est4-1 and est1-1 have measurable colonies at every passage, so their sizes can be "
                           "followed through senescence.",
        "slot:passage": PASSAGE_X},
    ("historical", "table"): {"levels:genotype": "The table covers every genotype.", "slot:passage": PASSAGE_X},
    ("anbalagan", "photos"): {
        "levels:genotype": "Wild type and cdc13-2 share each Figure 2C plate, so they share crowding and "
                           "photography: any difference comes from the genotype.",
        "levels:source_figure": "Both genotypes sit on the same Figure 2C plates.",
        "levels:passage": "The first and last streak show the whole change."},
    ("anbalagan", "counts"): {
        "slot:passage": PASSAGE_X,
        "slot:source_figure": "Figures 2C and 8D are separate experiments, so each lineage keeps its own line.",
        "color": "Lines of one genotype share a color; solid and dashed lines tell the two source figures apart.",
        "levels:genotype": "Wild type, the two single telomerase mutants and rif1Δ are the lineages this figure "
                           "follows; the double mutants appear in the box plots.",
        "levels:source_figure": "Both source figures enter, so each genotype has lineages from both "
                                "experiments."},
    ("anbalagan", "sizes"): {"slot:passage": PASSAGE_X,
                             "slot:source_figure": "Figures 2C and 8D are separate experiments, so each lineage "
                                                   "keeps its own line."},
    ("anbalagan", "boxes"): {
        "slot:genotype": "Genotype and passage together on the X axis make one box per colony set.",
        "slot:passage": "Genotype and passage together on the X axis make one box per colony set.",
        "slot:source_figure": "Each source figure is a separate experiment, drawn in its own group."},
    ("rescue", "all_fidelity"): {
        "levels:series": "Fidelity is checked over every plate, enrichment and 21-day plates included.",
        "selection": "Fidelity belongs to the photos, so it is checked before any plate is chosen."},
    ("rescue", "classifier"): {
        "levels:series": "The main series holds the standard six-day plates. Enrichment plates were picked by "
                         "colony class, which would bias the shapes."},
    ("rescue", "cv"): {
        "slot:passage": PASSAGE_X,
        "levels:genotype": "dbf4-1 is compared with wild type, its own background.",
        "levels:series": "Only the standard six-day plates enter the comparison."},
    ("rescue", "pair"): {
        "levels:genotype": "dbf4-1 is compared with wild type, its own background.",
        "levels:passage": "At passage 2 the two genotypes have plates with nearly the same colony counts.",
        "levels:density": "At the middle density tier wild type and dbf4-1 have nearly the same colony counts, so "
                          "crowding cannot explain a difference in shape."},
    ("rescue", "selection"): {
        "slot:passage": PASSAGE_X,
        "levels:series": "Only the standard six-day plates are candidates.",
        "levels:density": "Every density tier is a candidate, so the choice can match colony counts across "
                          "tiers."},
    ("rescue", "boxes"): {"slot:passage": PASSAGE_X},
    ("rescue", "p5_photos"): {"levels:passage": "Passage 5 is the last passage, where the genotypes differ most."},
    ("rescue", "classes"): {
        "levels:passage": "Passage 3 is where cdc13-2 first forms a small colony class.",
        "levels:genotype": "Both cdc13-2 genotypes, with and without dbf4-1: does dbf4-1 change the classes?"},
    ("rescue", "p5_components"): {"levels:passage": "Passage 5 is the last passage, where the genotypes differ "
                                                    "most."},
    ("rescue", "trajectory"): {
        "levels:series": "cdc13-2 has no qualifying standard plate at P4. Its large-colony enriched plates fill "
                         "that passage.",
        "by_hand": "The double mutant already has a standard P4 plate. Its large-colony enriched plate would count "
                   "that passage twice."},
    ("rescue", "share"): {"slot:passage": PASSAGE_X,
                          "levels:series": "Only the standard six-day plates enter the comparison."},
    ("rescue", "rate"): {"levels:genotype": "The two cdc13-2 genotypes are the comparison: does dbf4-1 change how "
                                            "much of the plate the small class takes over?"},
    ("rescue", "enrichment"): {
        "levels:passage": "The enrichment plates were made at passage 4.",
        "levels:genotype": "Both cdc13-2 genotypes were enriched for each colony class.",
        "levels:series": "Standard plates beside the large- and small-colony enriched plates; the 21-day plates "
                         "answer a different question.",
        "slot:series": "Series beside Genotype in Groups gives each enrichment type its own glyphs.",
        "selection": "Every enrichment plate is shown, not one per group.",
        "by_hand": "The double mutant's large-colony enriched plate comes back: this figure is about "
                   "enrichment."},
    ("rescue", "founded"): {"levels:series": "Only the small-colony enriched plates were seeded from small "
                                             "colonies."},
}


ORDER = ("screen", "analysis", "slot", "color", "levels", "leave_out", "selection", "matched", "window", "fit",
         "by_hand", "scheme", "option", "measure")


def _complete(project: str, steps: tuple) -> tuple:
    """Every Stats step names every setting, so nothing left from an earlier step (or an earlier visit) changes
    the figure: options it leaves out get their defaults; Normalization and Color by are named; every variable's
    levels and the plate rules carry over from the step before when the step leaves them out (a tour starts
    with every level used, every plate ticked, Overplated on Include and Plates on All)."""
    import dataclasses
    from ..analyses.registry import load_analyses
    analyses = load_analyses().analyses
    variables = VARIABLES.get(project, ())
    carried = {"levels": {v: () for v in variables}, "leave_out": False, "selection": False,
               "matched": None, "window": None, "fit": False, "by_hand": ()}
    done = []
    for step in steps:
        analysis_id = next((n.args[0] for n in step.needs if n.kind == "analysis"), None)
        analysis = analyses.get(analysis_id)
        if analysis is None:
            for need in step.needs:                 # a reading or plain step can still change a rule
                if need.kind == "leave_out":
                    carried["leave_out"] = need.args[0]
            done.append(step)
            continue
        needs = list(step.needs)
        kinds = {n.kind for n in needs}
        # carry levels and plate rules
        named = {n.args[0] for n in needs if n.kind == "levels"}
        for need in needs:
            if need.kind == "levels":
                carried["levels"][need.args[0]] = need.args[1]
            elif need.kind in ("leave_out", "selection", "by_hand"):
                carried[need.kind] = need.args[0]
            elif need.kind == "matched":
                carried["matched"] = need.args[0]
            elif need.kind == "window":
                carried["window"] = need.args
            elif need.kind == "fit":
                carried["fit"] = True
        needs += [Need("levels", (v, carried["levels"][v])) for v in variables if v not in named]
        if "selection" not in kinds:
            needs.append(Need("selection", (carried["selection"],)))
        if carried["selection"]:
            if "matched" not in kinds and carried["matched"] is not None:
                needs.append(Need("matched", (carried["matched"],)))
            if "window" not in kinds and carried["window"] is not None:
                needs.append(Need("window", tuple(carried["window"])))
            if "fit" not in kinds and carried["fit"]:
                needs.append(Need("fit"))
        elif "leave_out" not in kinds:              # Overplated can be set only while Plates is on All
            needs.append(Need("leave_out", (carried["leave_out"],)))
        if "by_hand" not in kinds:
            needs.append(Need("by_hand", (carried["by_hand"],)))
        # name every setting of the analysis
        if getattr(analysis, "color_slots", ()) and "color" not in kinds:
            needs.append(Need("color", ("",)))
        if analysis.normalize is not None and "scheme" not in kinds:
            needs.append(Need("scheme", ("raw",)))
        given = {n.args[1] for n in needs if n.kind == "option"}
        needs += [Need("option", (analysis_id, o.key, o.initial)) for o in analysis.options if o.key not in given]
        options = {o.key: i for i, o in enumerate(analysis.options)}
        slots = {v: i for i, v in enumerate(variables)}
        needs.sort(key=lambda n: (ORDER.index(n.kind),
                                  options.get(n.args[1], 99) if n.kind == "option" else
                                  slots.get(n.args[0], 99) if n.kind == "levels" else 0))
        done.append(dataclasses.replace(step, needs=tuple(needs)))
    return tuple(done)


def _with_reasons(project: str, steps: tuple) -> tuple:
    import dataclasses
    return _complete(project, tuple(dataclasses.replace(step, reasons=REASONS.get((project, step.key), {}))
                                    for step in steps))


def _guide_steps() -> tuple:
    """Getting started: every Crowding step, read as a lesson in using QuantaColony (no figure labels)."""
    import dataclasses
    return tuple(dataclasses.replace(step, figure="") for step in _with_reasons("crowding", _crowding_steps()))


def _crowding_figure_steps() -> tuple:
    """Figures 10.1 to 10.3 alone: open the project, then the three Stats figures."""
    steps = {step.key: step for step in _with_reasons("crowding", _crowding_steps())}
    done = Step("done", "Stats", "Every panel in one file",
                "Save figure writes any of these panels; the analysis PDF export (Export on the home page) collects "
                "them in one file. Press Finish to end the tour.")
    return tuple(steps[k] for k in ("open", "size", "photos", "fidelity")) + (done,)


TOURS: tuple[Tour, ...] = (
    Tour("crowding", "Getting started", "One wild-type dilution series, from cropping and colony detection to "
         "the Stats figures. Covers the dish scale, touching colonies, the capture ratio and measurement "
         "fidelity.", "crowding", _guide_steps(), kind="guide"),
    Tour("crowding_figures", "Crowding sets colony size", "The wild-type dilution series: figures 10.1 to 10.3.",
         "crowding", _crowding_figure_steps()),
    Tour("rescue", "The dbf4-1 rescue", "121 rescue plates: figures 13.1 to 15.1 and table 14.1.", "rescue",
         _with_reasons("rescue", _rescue_steps())),
    Tour("senescence", "Senescence in published plates", "The Anbalagan et al. streak-outs: figures 10.4, 10.5, "
         "10.7, 10.8 and table 10.2.", "anbalagan", _with_reasons("anbalagan", _senescence_steps())),
    Tour("historical", "The historical est plates", "The est mutant papers: figures 8.7, 10.6, 10.9 and "
         "table 10.3.", "historical", _with_reasons("historical", _historical_steps())),
)
STEPS = TOURS[0].steps                            # the Crowding tour's steps


def step_index(key: str) -> int:
    return next((i for i, step in enumerate(STEPS) if step.key == key), 0)


def tour(key: str) -> Tour:
    return next((t for t in TOURS if t.key == key), TOURS[0])
