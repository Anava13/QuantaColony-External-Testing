"""The browser study's guide on the practice plate (Qt-free): automatic detection first, one setting at a
time from a poorly tuned start, then manual correction one tool at a time.

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

from ..core.params import DetectionParams

POOR = DetectionParams(level=0.25, sense=0.5, rmin=3.0, rmax=8.0, maxpix=300.0, detection_type="Irregular",
                       split=False)

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
    mass_unmerged: bool = False            # no outline covers two colonies of the ringed mass
    mass_resolved: bool = False            # each colony of the mass has its own outline
    saved: bool = False                    # the practice plate is saved


@dataclass(frozen=True)
class Need:
    text: str
    anchor: str = ""
    check: object = None                   # GuideFacts -> bool

    def met(self, facts: GuideFacts) -> bool:
        return bool(self.check(facts))


@dataclass(frozen=True)
class GuideStep:
    key: str
    title: str
    text: str
    needs: tuple = ()
    hide_outlines: bool = False            # the photo without any outlines

    def met(self, facts: GuideFacts) -> bool:
        return all(n.met(facts) for n in self.needs)


def opened(section: str):
    return lambda f: section in f.open


STEPS = (
    GuideStep("photo", "The practice plate",
              "This plate is for practice and is not part of the results. Each dark spot is a colony. Your task on "
              "every plate is to give each colony its own outline, on its outer edge. Scroll to zoom; drag with "
              "the middle button, or hold Space and drag, to move around.", hide_outlines=True),
    GuideStep("detection", "Step 1 · Automatic detection",
              "The program outlines colonies on its own, using the settings under Step 1. These settings are "
              "poorly tuned on purpose: only a few colonies have outlines. The next steps fix them one at a time. "
              + COLORS,
              (Need("Open Step 1 · Automatic detection on the left", "grp-auto", opened("grp-auto")),)),
    GuideStep("color", "Colony color",
              "Colony color tells the program whether colonies are darker or lighter than the agar. These colonies "
              "are dark.",
              (Need("Open Colony appearance", "sec-appearance", opened("sec-appearance")),
               Need("Choose Light and watch the outlines disappear", "ctl-color", lambda f: f.seen_light),
               Need("Choose Dark again", "ctl-color", lambda f: f.seen_light and f.params.level > 0))),
    GuideStep("threshold", "Brightness threshold",
              "Pixels darker than this threshold count as colony. Too low, and only the darkest centers count; "
              "too high, and the agar starts to count too.",
              (Need("Open Brightness threshold", "sec-threshold", opened("sec-threshold")),
               Need("Raise it to about 0.40, watching more colonies get outlines", "ctl-threshold",
                    lambda f: 0.36 <= f.params.level <= 0.48),)),
    GuideStep("size", "Colony size",
              "Objects smaller than Min radius or larger than Max radius are left out, and so are objects covering "
              "more pixels than Largest object. These limits keep dust and smears out without losing colonies.",
              (Need("Open Colony size", "sec-size", opened("sec-size")),
               Need("Raise Max radius to 15 px or more", "ctl-rmax", lambda f: f.params.rmax >= 15),
               Need("Raise Largest object to 1500 px or more", "ctl-maxpix", lambda f: f.params.maxpix >= 1500))),
    GuideStep("sensitivity", "Sensitivity",
              "Sensitivity sets how far from round an object can be and still count as a colony. Low values keep "
              "only round objects.",
              (Need("Open Sensitivity", "sec-sensitivity", opened("sec-sensitivity")),
               Need("Raise it to 0.8 or more", "ctl-sense", lambda f: f.params.sense >= 0.8))),
    GuideStep("split", "Touching colonies",
              "With Split touching colonies on, the program cuts a mass of touching colonies where it narrows. "
              "The two sliders under the switch tune where it cuts; their starting values suit most plates.",
              (Need("Open Touching colonies", "sec-split", opened("sec-split")),
               Need("Turn on Split touching colonies, then look at the purple outlines", "ctl-split",
                    lambda f: f.params.split))),
    GuideStep("shape", "Colony shape",
              "Circular fits a circle to each colony and errs toward missing colonies. Irregular follows each "
              "colony's own edge and errs toward outlining things that are not colonies, which you then delete. "
              "Irregular suits these plates.",
              (Need("Choose Circular and compare the outlines", "ctl-shape", lambda f: f.seen_circular),
               Need("Choose Irregular again", "ctl-shape",
                    lambda f: f.seen_circular and f.params.detection_type == "Irregular"))),
    GuideStep("estimate", "Estimate settings",
              "Estimate settings reads the photo and sets all of these at once. Every plate in the study starts "
              "from its own estimate, so on most plates the settings only need small changes.",
              (Need("Press Estimate settings", "btn-estimate", lambda f: f.estimated),)),
    GuideStep("manual", "Step 2 · Manual correction",
              "Automatic detection misses some colonies and outlines some things that are not colonies. The four "
              "tools under Step 2 fix the outlines one by one or a region at a time.",
              (Need("Open Step 2 · Manual correction on the left", "grp-manual", opened("grp-manual")),)),
    GuideStep("delete", "Delete colonies",
              "Removes outlines you click: an outline on agar, on a mark, or around more than one colony.",
              (Need("Choose Delete colonies", "mode-DeleteIndividualColonies",
                    lambda f: f.mode == "DeleteIndividualColonies" or f.deleted_one),
               Need("Click an outline to select it (it turns red)", "plate", lambda f: f.selected_click),
               Need("Press Delete colonies", "btn-delete", lambda f: f.deleted_one))),
    GuideStep("delete_region", "Delete a region",
              "Removes every outline inside a box, for an area full of false outlines.",
              (Need("Choose Delete a region", "mode-DeleteManyColonies",
                    lambda f: f.mode == "DeleteManyColonies" or f.deleted_box),
               Need("Drag a box over a few outlines", "plate", lambda f: f.selected_box),
               Need("Press Delete colonies", "btn-delete-region", lambda f: f.deleted_box))),
    GuideStep("add", "Add colonies",
              "Draws a colony the program missed. Drag across the colony from one edge to the other: the drag is "
              "its diameter. A drawn circle is white and dashed. It is not a colony yet and does not count. "
              "Add colonies turns the drawn circles into colonies, outlined in lime, and the count goes up. "
              "Drawn circles that are never added are left out of the results.",
              (Need("Choose Add colonies", "mode-AddIndividualColonies",
                    lambda f: f.mode == "AddIndividualColonies" or f.added_one),
               Need("Drag across a colony without an outline: a white dashed circle appears, and the count "
                    "stays the same", "plate", lambda f: f.drew),
               Need("Press Add colonies: the circle turns lime, and the count goes up", "btn-add",
                    lambda f: f.added_one))),
    GuideStep("add_tools", "Snap and remove",
              "Snap to colony edge fits each drawn circle to the colony's own outline. A right-click removes a "
              "drawn circle before it is added.",
              (Need("Turn on Snap to colony edge", "sw-contour", lambda f: f.contour_on or f.snapped),
               Need("Drag across a colony and watch the circle snap to its edge", "plate", lambda f: f.snapped),
               Need("Right-click the drawn circle to remove it", "plate", lambda f: f.removed_drawn))),
    GuideStep("region", "Detect in a region",
              "Runs automatic detection inside a box only, for a patch the plate-wide settings miss. While this "
              "tool is on, the Step 1 settings sit under it, and a change applies inside the box only.",
              (Need("Choose Detect in a region", "mode-AddManyColonies",
                    lambda f: f.mode == "AddManyColonies" or f.added_region),
               Need("Drag a box over colonies without outlines", "plate", lambda f: f.region),
               Need("Change a setting below it, such as Brightness threshold, and watch the outlines in the box "
                    "change", "sec-threshold", lambda f: f.region_tuned or f.added_region),
               Need("Press Add detected colonies", "btn-add-region", lambda f: f.added_region))),
    GuideStep("undo", "Undo",
              "Every change can be undone, and redone, from the top bar or with Ctrl+Z and Ctrl+Y.",
              (Need("Press Undo", "btn-undo", lambda f: f.undone),)),
    GuideStep("mass", "A merged mass",
              "The ringed mass is three touching colonies. Measure it as three colonies.",
              (Need("Delete any outline that covers more than one of the three", "plate", lambda f: f.mass_unmerged),
               Need("Give each of the three its own outline (Add colonies)", "plate", lambda f: f.mass_resolved))),
    GuideStep("finish", "Finish the plate",
              "Finish image saves a plate and opens the next one. Six plates follow, each starting from its own "
              "estimate. On each, correct the outlines until every colony has its own, then press Finish image.",
              (Need("Press Finish image to start the study", "btn-finish", lambda f: f.saved),)),
)


def facts_from(events, params: DetectionParams, open_sections, mode: str, mass: tuple[bool, bool],
               saved: bool, estimate_pressed: bool = False, contour_on: bool = False) -> GuideFacts:
    """GuideFacts from recorder events (core.benchmark.Event: kind, data) and the page's state.
    mass: (no merged outline over the ringed mass, each of its colonies has its own outline).
    estimate_pressed: the button was pressed; with settings already at the estimate, no detection runs."""
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
        mass_unmerged=mass[0], mass_resolved=mass[1], saved=saved)
