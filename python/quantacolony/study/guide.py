"""The browser study's guide on the practice plate (Qt-free): automatic detection first, one setting at a
time from a poorly tuned start, then manual correction one tool at a time, each on a part of the plate
built for it (the bundle's marks: a plate number, condensation, a faint colony, a dark colony to snap to,
the brighter side, a mass of three, an inseparable mass), then the training check (study/score.py).

    POOR            the practice plate's starting settings: each guided fix improves the detection
                    (on the practice plate: 5, then 22, 38, 62 and 85 of its 100 colonies)
    Need            one bullet of a step: its text, the control it points at, and its check
    GuideStep       a step: title, explanation, bullets; done when every bullet is checked
    STEPS           the guide, in order
    GuideFacts      what the checks read: the current settings, what the recorder has seen, which
                    sections are open on the page, the merged mass, whether the practice plate is saved
    facts_from      GuideFacts from the recorder's events and the page's state

Anchors name controls on the page (index.html ids). A collapsed section is opened by its own bullet,
so a participant learns where each control lives before using it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.autoparams import maxpix_for
from ..core.params import DetectionParams

POOR = DetectionParams(level=0.25, sense=0.5, rmin=3.0, rmax=8.0, maxpix=maxpix_for(8.0), detection_type="Irregular",
                       split=False)
# The settings the settings steps lead to, exactly. Every later step is built on the detection they give
# (tools/study/build_bundle.py tuned_detection), so those steps wait while the settings differ.
TUNED = DetectionParams(level=0.41, sense=0.9, rmin=3.0, rmax=19.0, maxpix=maxpix_for(19.0),
                        detection_type="Irregular", split=True)
# (field, label, the page's section holding it, how to show a value)
TUNED_FIELDS = (("level", "Colony color", "sec-appearance", lambda v: "Dark" if v >= 0 else "Light"),
                ("detection_type", "Colony shape", "sec-appearance", str),
                ("level", "Brightness threshold", "sec-threshold", lambda v: f"{abs(v):.2f}"),
                ("rmin", "Min radius", "sec-size", lambda v: f"{v:g} px"),
                ("rmax", "Max radius", "sec-size", lambda v: f"{v:g} px"),
                ("sense", "Sensitivity", "sec-sensitivity", lambda v: f"{v:.2f}"),
                ("split", "Split touching colonies", "sec-split", lambda v: "on" if v else "off"),
                ("split_elongation", "Split above elongation", "sec-split", lambda v: f"{v:.2f}"),
                ("split_pinch", "Minimum pinch", "sec-split", lambda v: f"{v:.2f}"))


def settings_changes(params: DetectionParams) -> list:
    """[(label, current, lesson value, section)] for every setting that differs from TUNED."""
    out = []
    for field_name, label, section, show in TUNED_FIELDS:
        now, want = getattr(params, field_name), getattr(TUNED, field_name)
        if label == "Colony color":
            differs = (now >= 0) != (want >= 0)
        elif label == "Brightness threshold":
            differs = (now >= 0) == (want >= 0) and abs(abs(now) - abs(want)) > 0.004
        elif isinstance(want, float):
            differs = abs(now - want) > 1e-6
        else:
            differs = now != want
        if differs:
            out.append((label, show(now), show(want), section))
    return out

COLORS = ("Teal outlines are colonies the program detected; purple ones were cut from a mass of touching "
          "colonies; lime ones are colonies you added; red ones are selected. White dashed circles are drawn "
          "but not added yet, and do not count.")


@dataclass(frozen=True)
class GuideFacts:
    params: DetectionParams = POOR
    open: frozenset = frozenset()          # ids of the open sections on the page
    mode: str = ""
    seen_light: bool = False               # a detection ran with light colonies
    seen_circular: bool = False            # a detection ran with circular shapes
    estimated: bool = False                # Estimate settings was pressed
    selected_click: bool = False
    selected_box: bool = False
    deleted_one: bool = False              # a deletion in Delete colonies
    deleted_box: bool = False              # a deletion in Delete a region
    drew: bool = False                     # a circle drawn in Add colonies
    contour_on: bool = False               # Snap to colony edge is on
    snapped: bool = False                  # a drawn circle was fitted to a colony's edge
    removed_drawn: bool = False            # a drawn circle was removed with a right-click
    added_one: bool = False                # an addition in Add colonies
    region: bool = False                   # a region previewed in Detect in a region
    region_tuned: bool = False             # a setting changed while a region was previewed
    added_region: bool = False             # an addition in Detect in a region
    undone: bool = False
    redone: bool = False
    mass_unmerged: bool = False            # no outline covers two colonies of the ringed mass
    mass_resolved: bool = False            # each colony of the mass has its own outline
    marker_clear: bool = False             # no outline centered on the ringed plate number
    condensation_clear: bool = False       # no outline centered on the ringed condensation
    faint_added: bool = False              # the marked faint colony has its own outline
    snap_added: bool = False               # the marked dark colony (Snap) has its own outline
    cond_boxed: bool = False               # the Delete a region box holds every corner dot of the condensation
    clump_unmerged: bool = False           # no outline covers two colonies of the inseparable mass
    dots_boxed: bool = False               # the region box holds every dot of the bright side
    dots_found: bool = False               # the region's dashed outlines hold every dotted colony
    added_dots: bool = False               # each dotted colony has its own outline
    saved: bool = False                    # the practice plate is saved


@dataclass(frozen=True)
class Need:
    text: str
    anchor: str = ""
    check: object = None                   # GuideFacts -> bool

    def met(self, facts: GuideFacts) -> bool:
        return True if self.check is None else bool(self.check(facts))


@dataclass(frozen=True)
class GuideStep:
    key: str
    title: str
    text: str                              # why the step matters: under "More context", collapsed
    needs: tuple = ()
    hide_outlines: bool = False            # the photo without any outlines
    ring: str = ""                         # the bundle mark the step's four corner dots frame
    tuned: bool = False                    # the step waits until the Step 1 settings equal TUNED

    def met(self, facts: GuideFacts) -> bool:
        return all(n.met(facts) for n in self.needs)


def opened(section: str):
    return lambda f: section in f.open


TUNED_NOTE = "If you change a Step 1 setting later, the guide asks you to set it back before you go on."

STEPS = (
    GuideStep("photo", "The practice plate",
              "This plate is for practice and is not part of the results. Most dark spots are colonies; a few things "
              "on it are not. On every plate, each colony you can tell apart gets its own outline, on its outer edge.",
              (Need("Scroll to zoom in and out"),
               Need("Hold Space and drag, or drag with the middle mouse button, to move around")),
              hide_outlines=True),
    GuideStep("detection", "Step 1 · Automatic detection",
              "The program outlines colonies on its own, using the settings under Step 1. These settings start poorly "
              "tuned on purpose, so only a few colonies have outlines. The next steps fix them one at a time. "
              + COLORS,
              (Need("Open Step 1 · Automatic detection on the left", "grp-auto", opened("grp-auto")),)),
    GuideStep("color", "Colony color",
              "Colony color tells the program whether colonies are darker or lighter than the agar. These colonies "
              "are dark.",
              (Need("Open Colony appearance", "sec-appearance", opened("sec-appearance")),
               Need("Choose Light: the outlines disappear", "ctl-color", lambda f: f.seen_light),
               Need("Choose Dark again", "ctl-color", lambda f: f.seen_light and f.params.level > 0))),
    GuideStep("shape", "Colony shape",
              "Circular fits a circle to each colony and errs toward missing colonies. Irregular follows each colony's "
              "own edge and errs toward outlining things that are not colonies, which you then delete. Circular has "
              "one more setting, Largest object: blobs covering more pixels than this are left out before circles "
              "are fitted. Irregular suits these plates.",
              (Need("Choose Circular: most colonies lose their outlines", "ctl-shape", lambda f: f.seen_circular),
               Need("Find Largest object under it (it shows only with Circular)", "ctl-shape",
                    lambda f: f.seen_circular),
               Need("Choose Irregular again", "ctl-shape",
                    lambda f: f.seen_circular and f.params.detection_type == "Irregular"))),
    GuideStep("threshold", "Brightness threshold",
              "Pixels darker than this threshold count as colony. Too low, and only the darkest centers count; too "
              "high, and the agar starts to count too.",
              (Need("Open Brightness threshold", "sec-threshold", opened("sec-threshold")),
               Need("Set it to 0.41 (click the slider, then use the arrow keys)", "ctl-threshold",
                    lambda f: abs(f.params.level - TUNED.level) <= 0.004))),
    GuideStep("size", "Colony size",
              "Objects smaller than Min radius or larger than Max radius are left out. These limits keep dust and "
              "smears out without losing colonies.",
              (Need("Open Colony size", "sec-size", opened("sec-size")),
               Need("Set Max radius to 19", "ctl-rmax", lambda f: f.params.rmax == TUNED.rmax))),
    GuideStep("sensitivity", "Sensitivity",
              "Sensitivity sets how far from round an object can be and still count as a colony. Low values keep "
              "only round objects.",
              (Need("Open Sensitivity", "sec-sensitivity", opened("sec-sensitivity")),
               Need("Set it to 0.90", "ctl-sense", lambda f: abs(f.params.sense - TUNED.sense) < 1e-6))),
    GuideStep("split", "Touching colonies",
              "With Split touching colonies on, the program cuts a mass of touching colonies where it narrows. The "
              "two sliders under the switch tune where it cuts; their starting values suit most plates.",
              (Need("Open Touching colonies", "sec-split", opened("sec-split")),
               Need("Turn on Split touching colonies: cut colonies get purple outlines", "ctl-split",
                    lambda f: f.params.split))),
    GuideStep("manual", "Step 2 · Manual correction",
              "Automatic detection misses some colonies and outlines some things that are not colonies. The tools "
              "under Step 2 fix outlines one at a time or a box at a time. Each of the next steps uses one tool on a "
              "part of the plate, marked by an arrow or by four dots. " + TUNED_NOTE,
              (Need("Open Step 2 · Manual correction on the left", "grp-manual", opened("grp-manual")),), tuned=True),
    GuideStep("delete", "Delete colonies",
              "The mark the arrow points to is a plate number written in marker under the dish. Plate numbers, bubbles in the "
              "agar (small rings) and dust are not colonies, so their outlines are deleted.",
              (Need("Choose Delete colonies", "mode-DeleteIndividualColonies",
                    lambda f: f.mode == "DeleteIndividualColonies" or f.deleted_one),
               Need("Click each outline on the number the arrow points to (it turns red)", "plate",
                    lambda f: f.selected_click or f.marker_clear),
               Need("Press Delete colonies", "btn-delete", lambda f: f.deleted_one and f.marker_clear)),
              ring="marker", tuned=True),
    GuideStep("delete_region", "Delete a region",
              "The patch between the dots is condensation: water droplets on the lid. Delete a region removes every outline "
              "inside a box, for an area full of false outlines like this one.",
              (Need("Choose Delete a region", "mode-DeleteManyColonies",
                    lambda f: f.mode == "DeleteManyColonies" or f.deleted_box),
               Need("Drag a box around the four dots (each turns green inside the box)", "plate",
                    lambda f: f.cond_boxed or (f.deleted_box and f.condensation_clear)),
               Need("Press Delete colonies", "btn-delete-region", lambda f: f.deleted_box and f.condensation_clear)),
              ring="condensation", tuned=True),
    GuideStep("add", "Add colonies",
              "The colony the arrow points to is faint, so automatic detection misses it. Drag across a colony from one edge to "
              "the other; the drag is its diameter. A drawn circle (white, dashed) is not a colony yet and does not "
              "count. Add colonies turns drawn circles into colonies, outlined in lime. Circles never added are left "
              "out of the results; Finish image warns you about them.",
              (Need("Choose Add colonies", "mode-AddIndividualColonies",
                    lambda f: f.mode == "AddIndividualColonies" or f.added_one),
               Need("Drag across the colony the arrow points to, edge to edge", "plate", lambda f: f.drew),
               Need("Press Add colonies: the circle turns lime and the count goes up", "btn-add",
                    lambda f: f.added_one and f.faint_added)), ring="faint", tuned=True),
    GuideStep("add_tools", "Snap and remove",
              "Snap to colony edge fits a drawn circle to the colony's own outline. It needs a clear edge, so it "
              "works on dark colonies like the one the arrow points to. This one is larger than Max radius, so automatic "
              "detection misses it. On a faint colony it finds no edge and leaves the circle "
              "as drawn; size that circle by hand. A right-click removes a drawn circle before it is added.",
              (Need("Turn on Snap to colony edge", "sw-contour", lambda f: f.contour_on or f.snapped),
               Need("Drag across the colony the arrow points to: the circle snaps to its edge", "plate",
                    lambda f: f.snapped or f.snap_added),
               Need("Right-click the circle to remove it", "plate", lambda f: f.removed_drawn or f.snap_added),
               Need("Drag across it again, then press Add colonies", "btn-add",
                    lambda f: f.removed_drawn and f.snap_added)),
              ring="snap", tuned=True),
    GuideStep("region", "Detect in a region",
              "Light falls unevenly on this plate. One side is brighter, so its colonies look pale and the "
              "plate-wide threshold misses them. Detect in a region runs automatic detection inside a box only. "
              "While this tool is on, the Step 1 settings sit under it, and a change applies inside the box only.",
              (Need("Choose Detect in a region", "mode-AddManyColonies",
                    lambda f: f.mode == "AddManyColonies" or f.added_dots),
               Need("Drag a box around the four dots (each turns green inside the box)", "plate",
                    lambda f: f.dots_boxed or f.added_dots),
               Need("Set Brightness threshold below the tool to 0.60: dashed outlines appear", "sec-threshold",
                    lambda f: f.dots_found or f.added_dots),
               Need("Press Add detected colonies", "btn-add-region", lambda f: f.added_dots)),
              ring="bright", tuned=True),
    GuideStep("undo", "Undo and redo",
              "Every change can be undone and redone, from the top bar or with Ctrl+Z and Ctrl+Y.",
              (Need("Press Undo: the colonies you just added disappear", "btn-undo", lambda f: f.undone),
               Need("Press Redo: they come back", "btn-redo", lambda f: f.undone and f.redone)), tuned=True),
    GuideStep("mass", "A mass of three",
              "Touching colonies are separate colonies, so a mass of three gets three outlines. One outline around the "
              "mass counts as one colony of the wrong size.",
              (Need("Delete any outline that covers more than one of the three colonies the arrow points to", "plate", lambda f: f.mass_unmerged),
               Need("Draw and add one outline for each of the three", "plate", lambda f: f.mass_resolved)),
              ring="triple", tuned=True),
    GuideStep("skip", "A mass you cannot separate",
              "The mass the arrow points to holds about seven colonies that cannot be told apart. Masses of five or more like this "
              "are skipped. One outline around such a mass counts as a wrong outline.",
              (Need("Leave the mass the arrow points to without an outline (delete any outline on it)", "plate",
                    lambda f: f.clump_unmerged),), ring="clump", tuned=True),
    GuideStep("finish", "Measure the whole plate",
              "Finish image compares your outlines with the plate's true colonies. To start the study, at least 90% "
              "of the colonies you should measure need a correct outline, and at least 95% of your outlines need to "
              "be correct. If the plate falls short, you see how far, and a few places to look again; then correct "
              "it and press Finish image again.",
              (Need("Look over the whole plate, using what you have learned, and correct what you find"),
               Need("Press Finish image when you believe every outline is right", "btn-finish",
                    lambda f: f.saved))),
)

# The instructions shown on every scored plate, as tasks.
PLATE_TASKS = (
    "Give every colony you can tell apart its own outline, on its outer edge",
    "Touching colonies are separate: a mass of three gets three outlines",
    "Skip masses of five or more colonies that you cannot tell apart",
    "Delete outlines on plate numbers, bubbles, condensation, dust and shadows",
    "Some plates show part of a dish: measure colonies cut by the edge too",
    "Press Finish image when the outlines match the colonies",
)


def facts_from(events, params: DetectionParams, open_sections, mode: str, places: dict,
               saved: bool, estimate_pressed: bool = False, contour_on: bool = False) -> GuideFacts:
    """GuideFacts from recorder events (core.benchmark.Event: kind, data) and the page's state.
    places: the checks on the ringed parts of the plate (WebStudy._places): mass (no merged outline over
    the triple, each of its colonies has its own outline), marker_clear, condensation_clear, faint_added,
    clump_unmerged.
    estimate_pressed: the button was pressed; with settings already at the estimate, no detection runs."""
    mass = places.get("mass", (False, False))
    detections = [e.data.get("params", {}) for e in events if e.kind == "detection"]
    edits = [e.data for e in events if e.kind == "edit"]
    selects = [e.data for e in events if e.kind == "select"]
    regions = {repr(sorted(e.data.get("params", {}).items())) for e in events if e.kind == "region_preview"}
    return GuideFacts(
        params=params, open=frozenset(open_sections), mode=mode,
        seen_light=any(float(p.get("level", 1)) < 0 for p in detections),
        seen_circular=any(p.get("detection_type") == "Circular" for p in detections),
        estimated=estimate_pressed or any(e.kind == "detection" and e.data.get("trigger") == "estimate"
                                          for e in events),
        selected_click=any(s.get("how") == "click" and s.get("count", 0) > 0 for s in selects),
        selected_box=any(s.get("how") == "rectangle" and s.get("count", 0) > 0 for s in selects),
        deleted_one=any(d.get("kind") == "Deletion" and d.get("mode") == "DeleteIndividualColonies" for d in edits),
        deleted_box=any(d.get("kind") == "Deletion" and d.get("mode") == "DeleteManyColonies" for d in edits),
        drew=any(e.kind == "draw" for e in events),
        contour_on=contour_on,
        snapped=any(e.kind == "draw" and e.data.get("snapped") for e in events),
        removed_drawn=any(e.kind == "draw_removed" and e.data.get("how") == "right" for e in events),
        region_tuned=len(regions) >= 2,
        added_one=any(d.get("kind") == "Addition" and d.get("mode") == "AddIndividualColonies" for d in edits),
        region=any(e.kind == "region_preview" for e in events),
        added_region=any(d.get("kind") == "Addition" and d.get("mode") == "AddManyColonies" for d in edits),
        undone=any(e.kind == "undo" for e in events),
        redone=any(e.kind == "redo" for e in events),
        mass_unmerged=mass[0], mass_resolved=mass[1], saved=saved,
        marker_clear=places.get("marker_clear", False), condensation_clear=places.get("condensation_clear", False),
        faint_added=places.get("faint_added", False), clump_unmerged=places.get("clump_unmerged", False),
        dots_boxed=places.get("dots_boxed", False), dots_found=places.get("dots_found", False),
        snap_added=places.get("snap_added", False), cond_boxed=places.get("cond_boxed", False),
        added_dots=places.get("added_dots", False))
