"""Text for everything the window reports (the notifyuser catalogue).

Two destinations:
- Activity feed (kind set): lasting events such as detections, edits, undo and saves.
- Status line (kind empty): brief feedback that needs no record.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    text: str
    detail: str = ""
    kind: str = ""        # activity kind ('detect', 'delete', 'add', 'history', 'image', 'warning', 'save')


def _plural(count: int, noun: str = "colony", nouns: str = "colonies") -> str:
    return f"{count} {noun if count == 1 else nouns}"


def describe_params(params) -> str:
    """'Irregular · light colonies, threshold 0.35 · radius 2–20 px · sensitivity 0.90'."""
    from .params import DARK, threshold_from_level
    polarity, threshold = threshold_from_level(params.level)
    color = "dark" if polarity == DARK else "light"
    return (f"{params.detection_type} · {color} colonies, threshold {threshold:.2f} · "
            f"radius {params.rmin:g}–{params.rmax:g} px · sensitivity {params.sense:.2f}"
            + (f" · splitting touching colonies (elongation {params.split_elongation:.2f}, pinch "
               f"{params.split_pinch:.2f})" if getattr(params, "split", False) and params.detection_type != "Circular"
               else ""))


def compose_message(key: str, label: str = "", value=None) -> Message:
    """label is '<nominal display> <ordinal display>'; value depends on the key."""
    builders = {
        # ----- activity feed -----
        "ImageLoaded": lambda: Message("Image loaded", label, "image"),
        "DetectionResults": lambda: Message(f"Detected {_plural(value[0])}", describe_params(value[1]), "detect"),
        "SettingsEstimated": lambda: Message(f"Estimated settings from this image: {_plural(value[0])}",
                                             describe_params(value[1]), "detect"),
        "ColoniesDeleted": lambda: Message(f"Deleted {_plural(value[0])}", f"{_plural(value[1])} remain", "delete"),
        "ColoniesAdded": lambda: Message(
            f"Added {_plural(value[0])}",
            f"{_plural(value[2])} in total" + (f" · {_plural(value[1])} skipped as duplicates" if value[1] else ""),
            "add"),
        "Undo": lambda: Message(f"Undid {value}", "", "history"),
        "Redo": lambda: Message(f"Redid {value}", "", "history"),
        "ContourFailed": lambda: Message("No colony edge found", f"{value}; the circle is kept as drawn", "warning"),
        "LoadedSaved": lambda: Message(f"Opened saved result: {_plural(value[0])}",
                                       f"Revision {value[1]}", "history"),
        "NeedsReview": lambda: Message("Needs review", value, "warning"),
        "Saved": lambda: Message(f"Saved {_plural(value[0])} to the project",
                                 f"Revision {value[1]} · " + (value[2] if " " in value[2] else f"exported {value[2]}"),
                                 "save"),
        "ExportFailed": lambda: Message("Colony table not exported", value, "warning"),
        # ----- status line -----
        "ClickNotCloseEnough": lambda: Message("No colony under the pointer"),
        "NoColoniesSelected": lambda: Message("No colonies selected"),
        "NothingToAdd": lambda: Message("Nothing to add yet"),
        "NoUndoActions": lambda: Message("Nothing to undo"),
        "NoRedoActions": lambda: Message("Nothing to redo"),
        "ParameterError": lambda: Message(f"{value}"),
        "CircleRemoved": lambda: Message("Circle removed"),
        "ColonySelected": lambda: Message(f"Selected colony at x {value[0] + 1:.0f}, y {value[1] + 1:.0f}"),
        "ColonyDeselected": lambda: Message(f"Deselected colony at x {value[0] + 1:.0f}, y {value[1] + 1:.0f}"),
        "RectangleSelectionCount": lambda: Message(f"{_plural(value)} in the box"),
        "RegionCandidates": lambda: Message(f"{_plural(value, 'new colony', 'new colonies')} found in the region"),
        "SettingsAlreadyEstimated": lambda: Message("The settings already match the estimate for this image"),
        "AllDetected": lambda: Message("Every index is detected"),
        "ContourOn": lambda: Message("Snap to colony edge on"),
        "ContourOff": lambda: Message("Snap to colony edge off"),
    }
    builder = builders.get(key)
    return builder() if builder else Message(key)
