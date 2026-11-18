"""User study data (files and the project): the participant's project, the stage times, and the results zip.

Each participant gets a project of their own, made when the form is sent: one nominal variable, Plate,
whose levels are the practice plate and then the scored plates in the participant's order. Colony
Detection steps through levels in order, so it shows the plates in that order without any change.
The project records benchmark data (core/benchmark.py) and starts every plate from its own estimate.

    work_folder        where a participant's library and settings live (one folder per session)
    create_project     the participant's project, open in the manager
    crop_offset        photo pixel of a plate's crop origin (the tutorial check works in photo pixels)
    stamp              add a stage time to the study record
    first_pass_path    where the snapshot of every plate as first finished is kept (before the review)
    write_results      close the project and write <name>_<date>.zip: the project, the first-pass
                       snapshot when there is one, and participant.json
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__ as SOFTWARE_VERSION
from ..core.cropping import DishLayout, GridLayout
from ..core.params import new_project_detection
from ..data import DataManager, Settings, create_library
from ..data.packets import LevelSpec, VariableSpec
from ..data.regions import crop_box, outline_from_geometry
from .model import STUDY_VERSION, Bundle, BundlePlate, Participant, plate_levels

VARIABLE = "plate"
DISH_MM = 90.0                     # stored for completeness; every photo carries an imported scale


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def work_folder(root: Path) -> Path:
    """A new folder for one session under root (a session never reuses another's files)."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = Path(root) / f"session-{stamp}"
    k = 2
    while folder.exists():
        folder = Path(root) / f"session-{stamp}-{k}"
        k += 1
    folder.mkdir(parents=True)
    return folder


def open_manager(folder: Path) -> DataManager:
    manager = DataManager(Settings(folder=folder / "settings"))
    manager.use_library(create_library(folder / "library"))
    return manager


def plate_layout(plate: BundlePlate, shape: tuple[int, int]):
    """The plate's region: its dish circle, or the whole photo (shape: rows, columns)."""
    if plate.dish is None:
        rows, cols = shape[:2]
        return GridLayout(cols / 2, rows / 2, float(cols), float(rows), 0.0, 1, 1)
    cx, cy, r = plate.dish
    return DishLayout(cx, cy, r, ())


def crop_offset(plate: BundlePlate, shape: tuple[int, int]) -> tuple[int, int]:
    """(x0, y0): the photo pixel at the crop's top-left, as DataManager.checkout_image cuts it."""
    kind, geometry = plate_layout(plate, shape).region(0)
    x0, y0, _, _ = crop_box(outline_from_geometry(kind, geometry), shape)
    return x0, y0


def create_project(manager: DataManager, bundle: Bundle, participant: Participant, order: list[str],
                   seed: int) -> dict:
    """The participant's project, left open: photos with their imported scale, one dish region each,
    benchmark recording on. Returns the study record stored in the project."""
    levels = tuple(LevelSpec(value, display) for value, display in plate_levels(order))
    variable = VariableSpec(VARIABLE, "nominal", levels, "Plate")
    record = {"study_version": STUDY_VERSION, "bundle_version": bundle.version, "software_version": SOFTWARE_VERSION,
              "participant": participant.record(), "order_seed": int(seed), "order": list(order),
              "times": {"form_sent": now()}, "tutorial_skipped": []}
    manager.create_project(f"Study {participant.file_stem()}", [variable], new_project_detection(),
                           plate={"type": "circular", "sections": 1, "dish_mm": DISH_MM},
                           estimate_settings=True, record_benchmark=True)
    manager.set_new_image_start("estimate")
    for value, _ in plate_levels(order):
        plate = bundle.plate(value)
        photo_id = manager.add_photo(bundle.photo_path(plate), plate.px_per_mm, file_name=plate.file,
                                     scaling_method="study bundle")
        shape = next(p for p in manager.crop_packet().photos if p.photo_id == photo_id)
        layout = plate_layout(plate, (shape.height, shape.width))
        manager.commit_photo(photo_id, layout.to_dict(), [{VARIABLE: value}])
    manager.save_study_record(record)
    return record


def stamp(manager: DataManager, stage: str, **extra) -> dict:
    """Record the time a stage was reached (and any extra values) in the study record."""
    record = manager.study_record()
    record.setdefault("times", {})[stage] = now()
    record.update(extra)
    manager.save_study_record(record)
    return record


def results_name(participant: Participant, when: datetime | None = None) -> str:
    when = when or datetime.now()
    return f"QuantaColony study - {participant.file_stem()} - {when.strftime('%Y-%m-%d %H%M')}.zip"


def results_folder() -> Path:
    """The Desktop when there is one, else the home folder: somewhere a participant finds the file."""
    home = Path.home()
    desktop = home / "Desktop"
    return desktop if desktop.is_dir() else home


def first_pass_path(manager: DataManager) -> Path:
    """The snapshot of the project when the last plate was first finished, beside the project file."""
    project = Path(manager.project_path)
    return project.with_name(project.stem + " - first pass.qcproj")


def write_results(manager: DataManager, participant: Participant, folder: Path | None = None) -> Path:
    """Close the project and zip it with participant.json (the study record, readable without opening
    the project). Returns the zip's path."""
    record = stamp(manager, "finished")
    project = Path(manager.project_path)
    first = first_pass_path(manager)
    manager.close()
    target = Path(folder or results_folder()) / results_name(participant)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(project, f"{participant.file_stem()}.qcproj")
        if first.exists():
            archive.write(first, f"{participant.file_stem()} - first pass.qcproj")
        archive.writestr("participant.json", json.dumps(record, indent=2, ensure_ascii=False))
    return target
