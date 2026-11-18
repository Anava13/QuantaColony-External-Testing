"""DataManager: the only part of QuantaColony that reads or writes project files.

The DataManager holds the source of truth, builds packets for modules on
request, stores what modules send back, and writes exports. Modules ask it
for data and send results to it; they never open project files.

It is the front door to private helpers (data.store), one per area of the
project file. It creates them, hands each the one open file, and runs every
action that touches more than one area inside a single transaction. Helpers
never call each other; see data/store/__init__.py.

Projects live in the library (see library.py), one SQLite file each, named
by project id. Users reach them through QuantaColony: open by id, export a
copy to share, import a shared copy, restore a backup. Nobody picks or moves
project files by hand. A session that changed a project leaves a backup
snapshot when the project closes.

Folders group projects on the home page (records in folders.json, see
store/folders.py); deleting moves projects into the library's Trash, from
which they are restored or deleted permanently.
"""

from __future__ import annotations

import dataclasses

import hashlib
import io
import json
import sqlite3
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from .. import __version__
from ..core.benchmark import BENCHMARK_VERSION, CropBenchmark, DetectionBenchmark, environment
from ..core.colony_set import ColonySet
from ..core.params import DetectionParams
from ..core.record import DetectionResult, ImageRecord, SavedDetection
from .codec import blob_to_array
from .derived import DERIVATIONS, DerivationInputs, fingerprint
from .store.structure import expected_plates
from .exports import (check_writable, safe_name, write_browsable_summary, write_figure, write_growth_png, write_json,
                      write_overlay_png, write_png, write_table_csv, write_text)
from .library import Library, LibraryError, LibraryMove, ProjectLock, find_library, move_library
from .packets import (AnnotationSpec, Assignment, CropPacket, DerivedTable, ImagePacket, IndexInfo, ProjectInfo,
                      ProjectStatus, VariableSpec)
from .regions import REGION_KINDS, crop_box, cut_region, outline_from_geometry
from .schema import SCHEMA_VERSION
from .settings import Settings
from .store import catalog
from .store import folders
from .store.folders import TOP, FolderEntry, FolderProblem, Organization, TrashEntry
from .store.catalog import BACKUPS_KEPT, PROJECT_SUFFIX, BackupEntry, ProjectEntry
from .store.derived_cache import DerivedCache
from .store.annotations import AnnotationProblem, Annotations
from .store.detections import REGION_CHANGED, REGION_REMOVED, Detections
from .store.developer import Developer
from .store.photos import Photos
from .store.project_file import FileProblem, ProjectFile, now
from .store.structure import Structure

__all__ = ["DataManager", "ProjectError", "ProjectEntry", "BackupEntry", "FolderEntry", "TrashEntry", "PROJECT_SUFFIX",
           "BACKUPS_KEPT"]

KIND_NAMES = {"nominal": "Nominal", "ordinal": "Ordinal", "replicate": "Replicate"}
INDEX_MEASURES = {"colony_count": "Colonies counted", "mean_radius_um": "Mean colony radius (µm)",
                  "median_radius_um": "Median colony radius (µm)",
                  "growth_coverage": "Growth coverage (fraction of growable area)",
                  "colony_coverage": "Colony coverage (fraction of growable area)",
                  "capture_ratio": "Capture ratio (growth in single-colony pieces / all growth)"}


class ProjectError(Exception):
    """A request the project cannot satisfy; the message is meant for the user."""


class DataManager:
    def __init__(self, settings: Settings | None = None, library: Library | None = None, developer: bool = False):
        """developer: turn benchmark recording on for every project opened (quantacolony --developer).
        A project created with recording on (New Project wizard, Review step) records whatever this flag
        says. Records go to the project's developer tables (data/store/developer.py; core/benchmark.py)."""
        self.settings = settings if settings is not None else Settings.load()
        self.developer = developer
        self._environment_id: int | None = None         # this opening's row in dev_environment
        self.library: Library | None = library
        self._file: ProjectFile | None = None
        self._project_id: str | None = None
        self._lock: ProjectLock | None = None
        # helpers on the open file (created in _attach)
        self._structure: Structure | None = None
        self._annotations: Annotations | None = None
        self._photos: Photos | None = None
        self._detections: Detections | None = None
        self._cache: DerivedCache | None = None
        self._developer: Developer | None = None
        self.new_project_folder: str = TOP            # where create_project and import_project_file place projects

    # =====================================================================
    # library
    # =====================================================================
    def resolve_library(self) -> Library | None:
        """The library from settings, following it to another drive letter; None if not found."""
        if self.library is None:
            self.library = find_library(self.settings.library_path, self.settings.library_id)
            if self.library is not None and str(self.library.root) != self.settings.library_path:
                self.settings.set_library(self.library.root, self.library.library_id)
        return self.library

    def use_library(self, library: Library) -> None:
        """Make library the one QuantaColony uses from now on (first start, located, created or moved)."""
        self.close()
        self.library = library
        self.settings.set_library(library.root, library.library_id)

    def move_library(self, destination: Path) -> LibraryMove:
        """Move the whole library (closes the open project first) and point settings at the new place.

        The result names the old folder when part of it could not be removed (a file open in another
        program); the move itself is complete and the leftover folder is no longer a library."""
        library = self._require_library()
        reopen = self._project_id
        self.close()
        try:
            moved = move_library(library, destination)
        except LibraryError as error:
            if reopen:
                self.open_project(reopen)               # a refused move leaves the project as it was
            raise ProjectError(str(error)) from error
        self.use_library(moved.library)
        if reopen:
            self.open_project(reopen)
        return moved

    def _require_library(self) -> Library:
        if self.library is None:
            raise ProjectError("No project library is set up")
        return self.library

    def list_projects(self) -> list[ProjectEntry]:
        """Every project in the library with its progress, most recently changed first."""
        return catalog.list_projects(self._require_library())

    def project_thumbnail(self, project_id: str) -> bytes | None:
        """PNG bytes of a small picture of the project's first photo, or None."""
        return catalog.thumbnail(self._require_library().project_path(project_id))

    def library_project_id(self, path: Path) -> str | None:
        """The project id when path is a project file inside the library (open it, do not import it)."""
        return catalog.library_project_id(self._require_library(), path)

    # =====================================================================
    # the open project
    # =====================================================================
    @property
    def library_root(self) -> Path:
        return self._require_library().root

    @property
    def is_open(self) -> bool:
        return self._file is not None

    @property
    def project_id(self) -> str:
        self._require_open()
        return self._project_id

    @property
    def is_tutorial_open(self) -> bool:
        """The open project is a copy of the tutorial project."""
        return self._file is not None and self._file.setting("tutorial") is not None

    @property
    def project_path(self) -> Path:
        self._require_open()
        return self._file.path

    @property
    def _connection(self):
        """The open SQLite connection (tests only; modules never use it)."""
        return self._file.connection if self._file else None

    @property
    def exports_folder(self) -> Path:
        """Documents/QuantaColony Exports/<project name>: where exports go unless a path is given."""
        return Path.home() / "Documents" / "QuantaColony Exports" / safe_name(self._file.setting("name") or "project")

    def create_project(self, name: str, variables: list[VariableSpec], detection_defaults: DetectionParams,
                       plate: dict | None = None, extra: dict | None = None,
                       estimate_settings: bool = False,
                       annotations: list[AnnotationSpec] | None = None, record_benchmark: bool = False) -> ProjectInfo:
        """New project in the library, then opened. Built under a temporary name, then renamed.

        extra: more project settings stored by key (e.g. variable units from the New Project wizard).
        estimate_settings: Colony Detection estimates its settings from the first image instead of
        starting from detection_defaults.
        record_benchmark: the project records benchmark data whenever it is open (developer mode)."""
        library = self._require_library()
        if not name.strip():
            raise ProjectError("A project needs a name")
        if any(entry.name == name for entry in self.list_projects()):
            raise ProjectError(f"A project named {name} already exists")
        if not variables:
            raise ProjectError("A project needs at least one variable")
        if sum(1 for v in variables if v.type == "replicate") > 1:
            raise ProjectError("A project has at most one replicate variable: it numbers the plates of each index")
        project_id = uuid.uuid4().hex
        path = library.project_path(project_id)
        library.projects_folder.mkdir(parents=True, exist_ok=True)
        building = path.with_name(path.name + ".creating")
        settings = {"project_id": project_id, "name": name, "schema_version": SCHEMA_VERSION,
                    "created_at": now(), "software_version": __version__,
                    "detection_defaults": asdict(detection_defaults), "plate": plate or {},
                    "estimate_detection_settings": bool(estimate_settings)}
        if record_benchmark:
            settings["developer"] = {"benchmark": BENCHMARK_VERSION, "since": now()}
        settings.update({key: value for key, value in (extra or {}).items() if key not in settings})
        replicate = next((v for v in variables if v.type == "replicate"), None)
        if replicate is not None and "plates_per_index" not in settings:
            settings["plates_per_index"] = max(1, len(replicate.levels))
        try:
            ProjectFile.create(building, settings)
            file = ProjectFile.open(building)
            try:
                with file.transaction():
                    Structure(file).insert_variables(variables)
                    store = Annotations(file)
                    for spec in annotations or []:
                        store.declare(spec.name, spec.kind, spec.display_name, spec.unit, tuple(spec.choices))
            finally:
                file.close()
        except Exception:
            building.unlink(missing_ok=True)
            raise
        building.replace(path)
        self._place_new(project_id)
        info = self.open_project(project_id)
        self._file.changed = True                     # a new project gets a first backup on close
        return info

    def open_project(self, project_id: str) -> ProjectInfo:
        library = self._require_library()
        path = library.project_path(project_id)
        if not path.is_file():
            raise ProjectError("This project is not in the library")
        self.close()
        lock = ProjectLock(library.lock_path(project_id))
        try:
            lock.acquire()
        except LibraryError as error:
            raise ProjectError(str(error)) from error
        try:
            file = ProjectFile.open(path)
        except FileProblem as error:
            lock.release()
            raise ProjectError(str(error)) from error
        except Exception:
            lock.release()
            raise
        self._attach(file, project_id, lock)
        if file.upgraded_from is not None and file.upgraded_from < 3:
            with file.transaction():                   # photos stored before sizes were kept
                self._photos.fill_missing_sizes()
        if file.upgraded_from is not None and file.upgraded_from < 6:
            with file.transaction():                   # photos stored before each kept its scale's evidence
                self._photos.fill_scale_evidence(file.setting("plate") or {})
        if self.developer or file.setting("developer"):
            with file.transaction():
                self._environment_id = self._developer.ensure(environment())
        self.settings.remember_project(project_id)
        return self.project_info()

    def _attach(self, file: ProjectFile, project_id: str, lock: ProjectLock) -> None:
        self._file, self._project_id, self._lock = file, project_id, lock
        self._structure = Structure(file)
        self._photos = Photos(file)
        self._detections = Detections(file)
        self._annotations = Annotations(file)
        self._cache = DerivedCache(file)
        self._developer = Developer(file)

    @property
    def recording(self) -> bool:
        """The open project records benchmark data: modules hand their records to the commits."""
        return self._file is not None and self._environment_id is not None

    def close(self) -> None:
        """Close the open project; a session that changed it leaves a backup snapshot."""
        if self._file is not None:
            if self._file.changed:
                try:
                    catalog.snapshot(self._require_library(), self._file.connection, self._project_id)
                except (OSError, sqlite3.DatabaseError, ProjectError):
                    pass                                   # a failed backup never blocks closing
            self._file.close()
        if self._lock is not None:
            self._lock.release()
        self._file, self._project_id, self._lock = None, None, None
        self._structure = self._photos = self._detections = self._annotations = self._cache = None
        self._developer, self._environment_id = None, None

    def _require_open(self) -> None:
        if self._file is None:
            raise ProjectError("No project is open")

    # ----- sharing ----------------------------------------------------------
    def export_project_file(self, project_id: str, destination: Path) -> Path:
        """A consistent copy of a project for sending to someone; QuantaColony never works on it in place."""
        library = self._require_library()
        check_writable(Path(destination).parent)
        open_connection = self._file.connection if project_id == self._project_id and self._file else None
        try:
            return catalog.export_copy(library, project_id, destination, open_connection)
        except FileProblem as error:
            raise ProjectError(str(error)) from error

    def import_project_file(self, source: Path) -> str:
        """Copy a shared .qcproj into the library; returns its project id. A project already in the
        library is imported as a copy with a new id."""
        try:
            project_id = catalog.import_copy(self._require_library(), source)
        except FileProblem as error:
            raise ProjectError(str(error)) from error
        self._place_new(project_id)
        return project_id

    def is_new_library(self) -> bool:
        """No project, nothing in the Trash and no folders ever made: a library never used yet."""
        library = self._require_library()
        return not library.project_ids() and not catalog.trashed_projects(library) \
            and not folders.folders_path(library).exists()

    def add_tutorial_project(self) -> str:
        """A fresh copy of the tutorial project (Crowding) in the library; returns its project id.
        A copy already in the library keeps its work: the new one is named 'Crowding (copy)'."""
        from .tutorial_project import TUTORIAL_FILE
        return self.import_project_file(TUTORIAL_FILE)

    def add_dissertation_project(self, name: str) -> str:
        """A fresh copy of one bundled dissertation project ('crowding', 'rescue', 'historical',
        'anbalagan') in the Navarro Dissertation Projects folder; returns its project id."""
        from .tutorial_project import DISSERTATION_FILES
        if name not in DISSERTATION_FILES:
            raise ProjectError(f"No bundled project is named {name}")
        project_id = self.import_project_file(DISSERTATION_FILES[name])
        self.move_projects([project_id], self._dissertation_folder())
        return project_id

    @staticmethod
    def bundled_version(name: str) -> int:
        """The tutorial version of a bundled dissertation project (its marker's 'version')."""
        from .tutorial_project import DISSERTATION_FILES
        connection = sqlite3.connect(f"{Path(DISSERTATION_FILES[name]).resolve().as_uri()}?mode=ro", uri=True)
        try:
            row = connection.execute("SELECT value FROM project WHERE key = 'tutorial'").fetchone()
        finally:
            connection.close()
        marker = json.loads(row[0]) if row else {}
        return int(marker.get("version", 0) or 0)

    def _dissertation_folder(self) -> str:
        """The Navarro Dissertation Projects folder at the top level, made (or renamed from its earlier name)
        when missing; returns its id."""
        from .tutorial_project import DISSERTATION_FOLDER_NAME, EARLIER_FOLDER_NAMES
        entries = self.folder_layout()[0]
        top = [entry for entry in entries if entry.parent == TOP]
        folder = next((e for e in top if e.name == DISSERTATION_FOLDER_NAME), None)
        if folder is not None:
            return folder.folder_id
        earlier = next((e for e in top if e.name in EARLIER_FOLDER_NAMES), None)
        if earlier is not None:
            self.rename_folder(earlier.folder_id, DISSERTATION_FOLDER_NAME)
            return earlier.folder_id
        return self.create_folder(DISSERTATION_FOLDER_NAME)

    def add_dissertation_projects(self) -> list[str]:
        """Every bundled dissertation project, in the Navarro Dissertation Projects folder; returns the ids
        added. A project already in the library at the bundled version is not added again. A copy of an
        older version stays, moved into the folder's Older versions subfolder, and the current version is
        added beside it: nothing anyone worked on is deleted."""
        from .tutorial_project import DISSERTATION_FILES, OLDER_VERSIONS_FOLDER_NAME
        entries = [e for e in self.list_projects() if e.tutorial_name]
        added, older = [], []
        for name in DISSERTATION_FILES:
            current = self.bundled_version(name)
            copies = [e for e in entries if e.tutorial_name == name]
            if any(e.tutorial_version >= current for e in copies):
                continue
            older += [e.project_id for e in copies]
            added.append(self.add_dissertation_project(name))
        folder_id = self._dissertation_folder()
        self.move_projects([e.project_id for e in entries if e.project_id not in older
                            and e.tutorial_name in DISSERTATION_FILES], folder_id)
        if older:
            inside = [e for e in self.folder_layout()[0]
                      if e.parent == folder_id and e.name == OLDER_VERSIONS_FOLDER_NAME]
            older_id = inside[0].folder_id if inside else self.create_folder(OLDER_VERSIONS_FOLDER_NAME, folder_id)
            self.move_projects(older, older_id)
        return added

    @property
    def tutorial_name(self) -> str:
        """Which tutorial project is open ('crowding', 'rescue', ...), or ''."""
        if self._file is None:
            return ""
        marker = self._file.setting("tutorial")
        return str((marker or {}).get("name", "")) if isinstance(marker, dict) else ""

    # ----- folders ----------------------------------------------------------
    def _organization(self) -> Organization:
        library = self._require_library()
        try:
            organization = folders.load(library)
        except FolderProblem as error:
            raise ProjectError(str(error)) from error
        organization.tidy(library.project_ids(), catalog.trashed_projects(library))
        return organization

    def _change_folders(self, change):
        """Load the folders, apply change(organization), save. Folder problems reach the user as ProjectError."""
        organization = self._organization()
        try:
            result = change(organization)
        except FolderProblem as error:
            raise ProjectError(str(error)) from error
        folders.save(self._require_library(), organization)
        return result

    def _place_new(self, project_id: str) -> None:
        if self.new_project_folder:
            try:
                self._change_folders(lambda o: o.place([project_id], self.new_project_folder))
            except ProjectError:
                pass                                         # the folder was deleted meanwhile: top level

    def folder_layout(self) -> tuple[list[FolderEntry], dict[str, str]]:
        """Every folder (id, name, parent; '' is the top level) and the folder of every project."""
        organization = self._organization()
        return organization.entries(), {p: organization.folder_of(p)
                                        for p in self._require_library().project_ids()}

    def create_folder(self, name: str, parent: str = TOP) -> str:
        return self._change_folders(lambda o: o.create_folder(name, parent))

    def rename_folder(self, folder_id: str, name: str) -> None:
        self._change_folders(lambda o: o.rename_folder(folder_id, name))

    def move_folder(self, folder_id: str, parent: str) -> None:
        """Move a folder (with everything in it) into another folder, or to the top level ('')."""
        self._change_folders(lambda o: o.move_folder(folder_id, parent))

    def move_projects(self, project_ids: list[str], folder_id: str) -> None:
        known = set(self._require_library().project_ids())
        self._change_folders(lambda o: o.place([p for p in project_ids if p in known], folder_id))

    # ----- the Trash ----------------------------------------------------------
    def _lock_all(self, project_ids) -> list[ProjectLock]:
        """Locks on every project, or none: a project open anywhere stops the whole delete."""
        library = self._require_library()
        if self._project_id in project_ids:
            raise ProjectError("Close this project before deleting it")
        locks = []
        try:
            for project_id in project_ids:
                lock = ProjectLock(library.lock_path(project_id))
                lock.acquire()
                locks.append(lock)
        except LibraryError as error:
            for lock in locks:
                lock.release()
            raise ProjectError(str(error)) from error
        return locks

    def delete_projects(self, project_ids: list[str]) -> None:
        """Move projects (and their backups) into the Trash; each becomes its own Trash item."""
        library = self._require_library()
        project_ids = [p for p in dict.fromkeys(project_ids) if p in library.project_ids()]
        if not project_ids:
            return
        names = {e.project_id: e.name for e in self.list_projects()}
        organization = self._organization()
        locks = self._lock_all(project_ids)
        moved = {}
        try:
            for project_id in project_ids:
                catalog.move_to_trash(library, project_id)
                moved[project_id] = names.get(project_id, project_id)
        except (OSError, FileProblem) as error:
            raise ProjectError(f"Could not move every project to the Trash ({error})") from error
        finally:
            for lock in locks:
                lock.release()
            if moved:
                organization.trash_projects(moved)
                folders.save(library, organization)

    def delete_folder(self, folder_id: str) -> None:
        """Move a folder, its subfolders and every project in them into the Trash as one item."""
        library = self._require_library()
        organization = self._organization()
        if folder_id not in organization.folders:
            raise ProjectError("That folder no longer exists")
        inside = organization.projects_in(folder_id, library.project_ids())
        locks = self._lock_all(inside)
        moved = []
        try:
            for project_id in inside:
                catalog.move_to_trash(library, project_id)
                moved.append(project_id)
        except (OSError, FileProblem) as error:
            raise ProjectError(f"Could not move every project to the Trash ({error})") from error
        finally:
            for lock in locks:
                lock.release()
            organization.trash_folder(folder_id, moved)
            folders.save(library, organization)

    def list_trash(self) -> list[TrashEntry]:
        """What the Trash holds, most recently deleted first."""
        return self._organization().trash_entries()

    def trash_size(self, entry: TrashEntry) -> int:
        return catalog.trash_size(self._require_library(), entry.project_ids)

    def restore_trash(self, item_id: str) -> dict[str, str]:
        """Put a Trash item back: its projects, and for a folder its folders, where they were (the top
        level when that folder is gone). Returns {project id: name} of projects whose name was taken
        meanwhile and that came back renamed."""
        library = self._require_library()
        organization = self._organization()
        try:
            item = organization.trash_item(item_id)
        except FolderProblem as error:
            raise ProjectError(str(error)) from error
        renamed = {}
        names = catalog.trashed_projects(library)
        try:
            for project_id in list(item.get("projects", {})):
                name = catalog.restore_from_trash(library, project_id)
                if name != names.get(project_id, project_id):
                    renamed[project_id] = name
        except (OSError, FileProblem) as error:
            raise ProjectError(f"Could not restore every project ({error})") from error
        organization.restore(item_id)
        folders.save(library, organization)
        return renamed

    def purge_trash(self, item_id: str) -> None:
        """Delete a Trash item for good."""
        library = self._require_library()
        organization = self._organization()
        try:
            item = organization.forget(item_id)
        except FolderProblem as error:
            raise ProjectError(str(error)) from error
        for project_id in item.get("projects", {}):
            catalog.purge(library, project_id)
        folders.save(library, organization)

    def empty_trash(self) -> None:
        for entry in self.list_trash():
            self.purge_trash(entry.item_id)

    # ----- backups ----------------------------------------------------------
    def list_backups(self, project_id: str) -> list[BackupEntry]:
        """Snapshots of a project, newest first."""
        return catalog.list_backups(self._require_library(), project_id)

    def restore_backup(self, project_id: str, backup: Path) -> None:
        """Replace a project with one of its snapshots. The current state is snapshotted first,
        so a restore can itself be undone."""
        library = self._require_library()
        backup = Path(backup)
        if backup.parent != library.backups_folder / project_id:
            raise ProjectError("That backup belongs to another project")
        reopen = self._project_id == project_id
        already_saved = reopen and self._file.changed          # closing below snapshots the current state
        if reopen:
            self.close()
        lock = ProjectLock(library.lock_path(project_id))
        try:
            lock.acquire()
        except LibraryError as error:
            raise ProjectError(str(error)) from error
        try:
            if not already_saved:
                catalog.snapshot_file(library, project_id)
            catalog.restore_file(library, project_id, backup)
        finally:
            lock.release()
        if reopen:
            self.open_project(project_id)

    # =====================================================================
    # project contents
    # =====================================================================
    def project_info(self) -> ProjectInfo:
        self._require_open()
        labels = self._structure.all_labels()
        assigned = set(self._photos.assigned_ids())
        states = self._detections.states()
        indices = []
        for row in self._file.connection.execute("SELECT index_id FROM indices ORDER BY index_id").fetchall():
            index_id = row["index_id"]
            revision, review = states.get(index_id, (0, False))
            indices.append(IndexInfo(index_id, labels.get(index_id, ()), index_id in assigned, revision, review))
        return ProjectInfo(self._project_id, self._file.setting("name"), self._file.setting("schema_version"),
                           self._structure.variables(), tuple(indices), self.detection_defaults())

    def detection_defaults(self) -> DetectionParams:
        return DetectionParams(**self._file.setting("detection_defaults"))

    def index_name(self, index_id: int) -> str:
        """File-safe name from the index's level values, e.g. 'rif1_cdc132_Figure_2_v_25'."""
        return self._structure.index_name(index_id)

    def add_index(self, levels: dict[str, str]) -> int:
        """New plate from {variable name: level value}; every variable needs a level. The replicate variable may
        be left out (or given None): the plate then takes the next free replicate number of its index."""
        self._require_open()
        variables = self._structure.variable_ids()
        replicate = self._structure.replicate_variable()
        given = {name: value for name, value in levels.items() if value is not None}
        needed = set(variables) - ({replicate.name} if replicate else set())
        if not needed <= set(given) or not set(given) <= set(variables):
            raise ProjectError(f"An index needs one level for each variable: {', '.join(sorted(variables))}")
        known = self._structure.level_ids()
        level_ids = {}
        for name, value in given.items():
            if value not in known.get(name, {}):
                raise ProjectError(f"Variable '{name}' has no level '{value}'")
            level_ids[variables[name]] = known[name][value]
        with self._file.transaction():
            if replicate is not None and replicate.variable_id not in level_ids:
                level_ids[replicate.variable_id] = self._free_replicate(
                    replicate.variable_id, tuple(sorted(level_ids.items())), set())
            elif self._structure.find_index(level_ids) is not None:
                raise ProjectError(f"An index for {levels} already exists")
            return self._structure.insert_index(level_ids)

    def _free_replicate(self, variable_id: int, design: tuple, taken: set, allowed: frozenset = frozenset()) -> int:
        """A replicate level for a new plate of the index `design`: the first level no other plate of that index
        holds, else a new level. taken: levels claimed in the same change; allowed: plates (index ids) that are
        free to reuse (the photo's own plates, whose regions are being replaced)."""
        plates = self._structure.plates_by_design().get(design, {})
        for level in self._structure.variables():
            if level.variable_id != variable_id:
                continue
            for lv in level.levels:
                holder = plates.get(lv.level_id)
                if lv.level_id not in taken and (holder is None or holder in allowed):
                    return lv.level_id
        return self._structure.add_replicate_level(variable_id)

    # ----- photos and regions (project creation and cropping) ------------
    def add_photo(self, source: Path | bytes, px_per_mm: float | None = None, file_name: str = "",
                  scaling_method: str = "", scale: dict | None = None) -> int:
        """Store a plate photo exactly as uploaded. source is a file path or the file's bytes.

        The photo's scale (core.scale): `scale`, the evidence Cropping starts from (a dish scale gets its
        px/mm once Cropping saves the dish circle); else px_per_mm, a scale known from elsewhere (stored
        as an imported scale); else the project's default (core.scale.default_scale). scaling_method is
        a label kept with an imported scale."""
        from ..core.scale import FIXED, default_scale, scale_from_dict
        self._require_open()
        content = Path(source).read_bytes() if isinstance(source, (str, Path)) else bytes(source)
        file_name = file_name or (Path(source).name if isinstance(source, (str, Path)) else "photo")
        if px_per_mm is not None and px_per_mm <= 0:
            raise ProjectError("The scale (pixels per mm) must be positive")
        if scale is None:
            scale = ({"method": FIXED, "value": float(px_per_mm), "unit": "mm"} if px_per_mm is not None
                     else default_scale(self._file.setting("plate") or {}).to_dict())
            if px_per_mm is not None and scaling_method:
                scale["source"] = scaling_method
        value = scale_from_dict(scale).px_per_mm()
        try:
            Image.open(io.BytesIO(content)).verify()
        except Exception as error:
            raise ProjectError(f"{file_name} is not a readable image ({error})") from error
        with self._file.transaction():
            return self._photos.insert(content, file_name, dict(scale), value)

    def commit_assignments(self, assignments: list[Assignment]) -> list[int]:
        """Store regions for indices directly. Returns indices flagged for review because a region
        changed after their colonies were committed."""
        self._require_open()
        prepared = []
        for assignment in assignments:
            if assignment.region_kind not in REGION_KINDS:
                raise ProjectError(f"Unknown region kind: {assignment.region_kind}")
            try:
                outline = outline_from_geometry(assignment.region_kind, assignment.geometry)
                crop_box(outline, self._photos.shape(assignment.photo_id))
            except (KeyError, ValueError) as error:
                raise ProjectError(f"Index {assignment.index_id}: invalid region ({error})") from error
            prepared.append((assignment, outline))
        flagged = []
        with self._file.transaction():
            for assignment, outline in prepared:
                changed = self._photos.save_assignment(assignment.index_id, assignment.photo_id,
                                                       assignment.region_kind, assignment.geometry, outline)
                if changed and self._detections.flag_review(assignment.index_id, REGION_CHANGED):
                    flagged.append(assignment.index_id)
        return flagged

    def _region(self, index_id: int):
        """(assignment row, photo row) for an index, or a ProjectError naming the missing step."""
        assignment = self._photos.assignment(index_id)
        if assignment is None:
            raise ProjectError("This index has no photo region yet. Assign one in Cropping first.")
        return assignment, self._photos.row(assignment["photo_id"])

    # =====================================================================
    # Colony Detection
    # =====================================================================
    def first_index_for_detection(self) -> int | None:
        """The first index waiting for detection or review, else the first index with a region."""
        indices = [i for i in self.project_info().indices if i.has_region]
        waiting = [i for i in indices if i.revision == 0 or i.needs_review]
        chosen = waiting or indices
        return chosen[0].index_id if chosen else None

    def detection_start(self) -> tuple[DetectionParams, bool]:
        """Settings a new index starts from, and whether to estimate them from its image instead."""
        params, estimate, _ = self._new_image_start()
        return params, estimate

    def _new_image_start(self) -> tuple[DetectionParams, bool, str]:
        """(settings, estimate from the image, source) for a new index.

        With new_image_start 'estimate', every new index estimates its settings from its own image,
        starting from the project defaults. Otherwise a new index starts from the settings of the last
        image finished in this project; before any image is finished it starts from the project
        defaults, or from an estimate when the project asks for one."""
        defaults = self.detection_defaults()
        if self.new_image_start() == "estimate":
            return defaults, True, "estimate"
        last = self._file.setting("last_detection_params")
        if last:
            return DetectionParams(**last), False, "last image"
        estimate = bool(self._file.setting("estimate_detection_settings"))
        return defaults, estimate, "estimate" if estimate else "defaults"

    def new_image_start(self) -> str:
        """How a new index chooses its starting settings: 'last image' (the settings of the last image
        finished) or 'estimate' (estimated from its own image, starting from the project defaults)."""
        self._require_open()
        return "estimate" if self._file.setting("new_image_start") == "estimate" else "last image"

    def set_new_image_start(self, mode: str) -> None:
        self._require_open()
        if mode not in ("last image", "estimate"):
            raise ProjectError(f"Unknown starting point for new images: {mode}")
        with self._file.transaction():
            self._file.set_setting("new_image_start", mode)

    def checkout_image(self, index_id: int) -> ImagePacket:
        """The cropped image, its mask and scale, the saved result (crop coordinates), and the
        settings a new index starts from."""
        self._require_open()
        assignment, photo = self._region(index_id)
        crop = cut_region(self._photos.array(photo["photo_id"]), blob_to_array(assignment["outline"]))
        labels = self._structure.labels(index_id)
        record = ImageRecord(index_id, self._structure.name_from_labels(labels), labels, crop.image, crop.mask,
                             _scale(photo["px_per_mm"]))
        saved = self._detections.load(index_id)
        if saved is not None:
            dx, dy = crop.offset
            saved = SavedDetection(saved.params, saved.colonies.translated(-dx, -dy),
                                   saved.baseline.translated(-dx, -dy), saved.revision, saved.detection_version,
                                   saved.needs_review, saved.review_reason)
        start, estimate, source = self._new_image_start()
        return ImagePacket(record, saved, start, estimate, "saved" if saved is not None else source)

    def index_photo(self, index_id: int) -> int:
        """The photo id an index's region lies on."""
        self._require_open()
        _, photo = self._region(index_id)
        return int(photo["photo_id"])

    def crop_origin(self, index_id: int) -> tuple[int, int]:
        """(x0, y0): where an index's crop starts in its photo (crop pixel + origin = photo pixel)."""
        self._require_open()
        assignment, photo = self._region(index_id)
        x0, y0, _, _ = crop_box(blob_to_array(assignment["outline"]), self._photos.shape(photo["photo_id"]))
        return x0, y0

    def commit_detection(self, result: DetectionResult, benchmark: DetectionBenchmark | None = None) -> int:
        """Store a finished result as the new truth for its index and remember its settings as the
        starting point for the next new index. Returns the new revision.
        benchmark: developer mode's record of the session, stored in the same transaction."""
        self._require_open()
        assignment, photo = self._region(result.index_id)
        x0, y0, _, _ = crop_box(blob_to_array(assignment["outline"]), self._photos.shape(photo["photo_id"]))
        final, baseline = result.colonies.translated(x0, y0), result.baseline.translated(x0, y0)
        for name, colonies in (("final", final), ("baseline", baseline)):
            if len(set(colonies.ids.tolist())) != len(colonies) or (len(colonies) and colonies.ids.min() < 1):
                raise ProjectError(f"The {name} colonies need unique positive ids")
        with self._file.transaction():
            revision = self._detections.write(result.index_id, result.params, final, baseline, result.edits,
                                              result.detection_version)
            self._cache.clear(result.index_id)
            self._file.set_setting("last_detection_params", asdict(result.params))
            if benchmark is not None and self.recording:
                self._developer.write_detection(benchmark, revision, (x0, y0), self._environment_id)
        return revision

    def developer_records(self) -> Developer:
        """Reader for the developer tables of the open project (analysis; see quantacolony.benchmark)."""
        self._require_open()
        return self._developer

    def edit_log(self, index_id: int) -> list[dict]:
        """Every committed edit for an index, oldest first."""
        self._require_open()
        return self._detections.edit_log(index_id)

    # =====================================================================
    # derived results
    # =====================================================================
    def request_derived(self, name: str, index_id: int) -> DerivedTable:
        """Computed now, or taken from the cache when its fingerprint matches the current inputs."""
        self._require_open()
        derivation = DERIVATIONS.get(name)
        if derivation is None:
            raise ProjectError(f"Unknown derived result: {name}")
        assignment, photo = self._region(index_id)
        revision, _ = self._detections.provenance(index_id)

        def crop():
            cut = cut_region(self._photos.array(photo["photo_id"]), blob_to_array(assignment["outline"]))
            return cut.image, cut.mask, cut.offset

        # the fingerprint needs only the revision, so a cached result never reads the colonies
        inputs = DerivationInputs(index_id, ColonySet.empty(), _scale(photo["px_per_mm"]), revision,
                                  photo["sha256"], hashlib.sha256(bytes(assignment["outline"])).hexdigest(), crop)
        key = fingerprint(derivation, inputs)
        if derivation.cache:
            cached = self._cache.lookup(name, index_id, key)
            if cached is not None:
                return DerivedTable(name, index_id, derivation.version, derivation.columns, tuple(cached),
                                    from_cache=True)
        saved = self._detections.load(index_id)                 # computing: read the colonies once
        inputs = dataclasses.replace(inputs, colonies=saved.colonies if saved else ColonySet.empty())
        rows = derivation.compute(inputs)
        if derivation.cache:
            self._cache.store(name, index_id, key, rows)
        return DerivedTable(name, index_id, derivation.version, derivation.columns, tuple(rows))

    # =====================================================================
    # exports (output only; never read back)
    # =====================================================================
    def export_colonies_csv(self, index_id: int, path: Path | None = None) -> Path:
        table = self.request_derived("colony_measurements", index_id)
        revision, version = self._detections.provenance(index_id)
        provenance = {label.variable: label.display for label in self._structure.labels(index_id)}
        provenance.update({"detection_revision": revision, "detection_version": version,
                           "measurement_version": table.algorithm_version, "software_version": __version__})
        path = Path(path) if path else self.exports_folder / f"{self.index_name(index_id)}_colonies.csv"
        return write_table_csv(table.columns, table.rows, path, provenance)

    def export_crop_png(self, index_id: int, path: Path | None = None) -> Path:
        record = self.checkout_image(index_id).record
        path = Path(path) if path else self.exports_folder / f"{record.name}_crop.png"
        return write_png(record.image, path)

    def export_browsable_copy(self, project_id: str, folder: Path) -> Path:
        """A folder anyone can look through without QuantaColony (see export_project). The project
        is opened for the export when it is not the open one, then closed again."""
        check_writable(folder)                                  # before opening anything
        if self._project_id == project_id:
            return self.export_project(folder)
        previous = self._project_id
        self.open_project(project_id)
        try:
            return self.export_project(folder)
        finally:
            self.close()
            if previous:
                self.open_project(previous)

    def export_project(self, folder: Path | None = None) -> Path:
        """Browsable copy of the open project: original photos, and for every index its crop, the
        crop with its colonies drawn on it and its colony table, plus summary.csv, project.json
        and an index.html page that explains the folder. QuantaColony never reads it back."""
        info = self.project_info()
        folder = Path(folder) if folder else self.exports_folder / "Browsable copy"
        check_writable(folder)
        for photo_id, file_name, content in self._photos.contents():
            target = folder / "photos" / f"{photo_id}_{safe_name(file_name)}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        entries, rows, page_rows = [], [], []
        total_colonies = 0
        for index in info.indices:
            name = Structure.name_from_labels(index.labels)
            levels = {label.variable: label.display for label in index.labels}
            entry = {"index_id": index.index_id, "name": name,
                     "levels": {label.variable: label.value for label in index.labels},
                     "revision": index.revision, "needs_review": index.needs_review}
            page = {"name": name, "levels": " · ".join(f"{k}: {v}" for k, v in levels.items()),
                    "crop": "", "overlay": "", "table": "", "folder": "", "colonies": "–"}
            colonies = None
            if index.has_region:
                region = self._photos.assignment(index.index_id)
                entry["region"] = {"photo_id": region["photo_id"], "kind": region["region_kind"],
                                   "geometry": json.loads(region["geometry"])}
                packet = self.checkout_image(index.index_id)
                index_folder = folder / "indices" / name
                write_png(packet.record.image, index_folder / "crop.png")
                page["crop"] = f"indices/{name}/crop.png"
                page["folder"] = f"indices/{name}/"
                if packet.saved is not None:
                    colonies = packet.saved.colonies
                    write_overlay_png(packet.record.image, colonies, index_folder / "colonies.png")
                    self.export_colonies_csv(index.index_id, index_folder / "colonies.csv")
                    page["overlay"] = f"indices/{name}/colonies.png"
                    page["table"] = f"indices/{name}/colonies.csv"
                    entry["detection_params"] = asdict(packet.saved.params)
                    entry["edit_log"] = self.edit_log(index.index_id)
                    _, growth, marked = self.growth_overlay(index.index_id)
                    write_growth_png(packet.record.image, growth, marked, index_folder / "growth.png")
                    page["growth"] = f"indices/{name}/growth.png"
                    cover = self.request_derived("plate_coverage", index.index_id).rows[0]
                    entry["coverage"] = cover
                    if cover.get("growth_coverage") is not None:
                        page["coverage"] = f"{cover['growth_coverage']:.1%}"
            state = ("needs review" if index.needs_review else "detected" if index.revision
                     else "not detected yet" if index.has_region else "no region")
            count = len(colonies) if colonies is not None else ""
            if colonies is not None:
                total_colonies += len(colonies)
                page["colonies"] = len(colonies)
            scale = _scale(self._region(index.index_id)[1]["px_per_mm"])
            radii_um = (colonies.radii * 1000.0 / scale
                        if colonies is not None and len(colonies) and scale else np.array([]))
            cover = entry.get("coverage", {})
            rows.append({"index": name, **levels, "state": state, "colonies": count,
                         "median_radius_um": round(float(np.median(radii_um)), 2) if len(radii_um) else "",
                         "mean_radius_um": round(float(np.mean(radii_um)), 2) if len(radii_um) else "",
                         **{key: "" if cover.get(key) is None else round(float(cover[key]), 4)
                            for key in ("growth_coverage", "colony_coverage", "capture_ratio")},
                         "detection_revision": index.revision or ""})
            page["state"] = state
            entries.append(entry)
            page_rows.append(page)

        columns = ["index"] + [v.display_name for v in info.variables] + [
            "state", "colonies", "median_radius_um", "mean_radius_um", "growth_coverage", "colony_coverage",
            "capture_ratio", "detection_revision"]
        write_table_csv(columns, rows, folder / "summary.csv", {"software_version": __version__})
        write_json({"name": info.name, "schema_version": info.schema_version, "software_version": __version__,
                    "exported_at": now(), "detection_defaults": asdict(info.detection_defaults),
                    "variables": [{"name": v.name, "display_name": v.display_name, "type": v.type,
                                   "levels": [{"value": lv.value, "display": lv.display, "is_control": lv.is_control}
                                              for lv in v.levels]} for v in info.variables],
                    "indices": entries}, folder / "project.json")
        detected = sum(1 for i in info.indices if i.revision)
        write_browsable_summary({
            "name": info.name, "software_version": __version__,
            "exported_at": datetime.now().astimezone().strftime("%b %d, %Y %H:%M"),
            "tiles": [(self._photos.count(), "photos"), (len(info.variables), "variables"),
                      (self._structure.index_count(), "indices"), (len(self._photos.assigned_ids()), "plates"),
                      (detected, "plates detected"),
                      (total_colonies, "colonies counted")],
            "variables": [(v.display_name, KIND_NAMES.get(v.type, v.type),
                           ", ".join(lv.display + (" (control)" if lv.is_control else "") for lv in v.levels))
                          for v in info.variables],
            "indices": page_rows}, folder / "index.html")
        return folder

    # =====================================================================
    # Cropping
    # =====================================================================
    def crop_packet(self) -> CropPacket:
        """Variables, plate settings, photos in order and every saved photo layout."""
        from ..core.cropping import VariableLevels
        self._require_open()
        everything = self._structure.variables()
        variables = tuple(VariableLevels(v.name, v.display_name, tuple(lv.value for lv in v.levels),
                                         tuple(lv.display for lv in v.levels), v.type)
                          for v in everything if v.type != "replicate")
        replicate = next((v.name for v in everything if v.type == "replicate"), "")
        return CropPacket(self._file.setting("name"), variables, self._file.setting("plate") or {},
                          self._photos.infos(), self._photos.layouts(),
                          self._file.setting("cropping_preferences") or {}, replicate,
                          int(self._file.setting("plates_per_index") or
                              next((len(v.levels) for v in everything if v.type == "replicate"), 1)))

    def photo_image(self, photo_id: int) -> np.ndarray:
        """A plate photo as RGB pixels (read-only array)."""
        self._require_open()
        try:
            return self._photos.array(photo_id)
        except KeyError as error:
            raise ProjectError(f"No photo {photo_id} in this project") from error

    def save_cropping_preferences(self, preferences: dict) -> None:
        """Which variables are set per photo or per section, and the index board's view."""
        self.save_module_preferences("cropping", preferences)

    def commit_photo(self, photo_id: int, layout: dict, sections: list,
                     benchmark: CropBenchmark | None = None, scale: dict | None = None) -> list[int]:
        """Store one photo's layout, its regions and its scale as the truth, in one transaction.

        scale: the photo's scale evidence (core.scale.Scale.to_dict); None keeps the stored evidence. The
        px/mm is computed from the evidence and this layout (a dish scale reads the dish circle), so moving
        the dish rescales the photo. Measurements follow on their own (their cache keys hold the px/mm).

        sections holds {variable name: level value} or None (unused) per section of the layout.
        With a replicate variable, a section needs levels of the design variables only: sections that
        share a combination (on this photo or on others) are plates of one index, and each plate keeps
        the replicate number it was given (a new plate takes the index's first free number). The numbers
        are written into the saved sections, so saving the photo again keeps them.
        Returns indices flagged for review because their region changed after detection.
        benchmark: developer mode's record of the photo's time in Cropping, stored in the same transaction.
        """
        from ..core.cropping import layout_from_dict
        from ..core.scale import scale_from_dict
        self._require_open()
        plan = layout_from_dict(layout)
        if len(sections) != plan.section_count:
            raise ProjectError("Every section of the layout needs levels or must be marked unused")
        photo = self._photos.row(photo_id)
        if photo is None:
            raise ProjectError(f"No photo {photo_id} in this project")
        evidence = scale_from_dict(scale if scale is not None else json.loads(photo["scale"] or "{}"))
        variables = self._structure.variable_ids()
        replicate = self._structure.replicate_variable()
        design_names = [n for n in variables if replicate is None or n != replicate.name]
        level_ids = self._structure.level_ids()
        shape = self._photos.shape(photo_id)
        regions = []                      # (section number, design key, kind, geometry, outline)
        seen = set()
        for number, levels in enumerate(sections, start=1):
            if levels is None:
                continue
            if any(levels.get(n) not in level_ids.get(n, {}) for n in design_names) or \
                    not set(levels) <= set(variables):
                raise ProjectError(f"Section {number} needs one valid level of every variable")
            key = tuple(sorted((variables[n], level_ids[n][levels[n]]) for n in design_names))
            if replicate is None:
                if key in seen:
                    raise ProjectError(f"Section {number} repeats an index already used on this photo")
                seen.add(key)
            kind, geometry = plan.region(number - 1)
            outline = outline_from_geometry(kind, geometry)
            try:
                crop_box(outline, shape)
            except ValueError as error:
                raise ProjectError(f"Section {number} lies outside the photo") from error
            regions.append((number, key, kind, geometry, outline))

        sections = [dict(levels) if levels is not None else None for levels in sections]
        flagged = []
        with self._file.transaction():
            own = frozenset(self._photos.assigned_on(photo_id))
            claimed: dict[tuple, set] = {}
            index_ids = []
            if replicate is not None:             # plates that kept their number first, so none is taken from them
                regions.sort(key=lambda r: (sections[r[0] - 1].get(replicate.name) is None, r[0]))
            for number, key, kind, geometry, outline in regions:
                if replicate is None:
                    index_id = self._structure.find_index(dict(key))
                    if index_id is None:
                        index_id = self._structure.insert_index(dict(key))
                else:
                    index_id = self._place_plate(replicate, key, sections[number - 1], own,
                                                 claimed.setdefault(key, set()))
                old = self._photos.assignment(index_id)
                if old is not None and old["photo_id"] != photo_id:
                    raise ProjectError("An index on this photo is already on another photo. Change one of them first.")
                if self._photos.save_assignment(index_id, photo_id, kind, geometry, outline) \
                        and self._detections.flag_review(index_id, REGION_CHANGED):
                    flagged.append(index_id)
                index_ids.append(index_id)
            for index_id in self._photos.assigned_on(photo_id):
                if index_id not in index_ids:
                    self._photos.delete_assignment(index_id)
                    if self._detections.flag_review(index_id, REGION_REMOVED):
                        flagged.append(index_id)
            self._photos.save_layout(photo_id, layout, sections)
            self._photos.set_scale(photo_id, evidence.to_dict(), evidence.px_per_mm(plan))
            if benchmark is not None and self.recording:
                self._developer.write_crop(benchmark, self._environment_id)
        return flagged

    def _place_plate(self, replicate, design: tuple, levels: dict, own: frozenset, claimed: set) -> int:
        """The plate (index id) one section of a photo holds: its saved replicate number when that plate is
        free (new, or already on this photo), else the index's first free number. Writes the number into
        levels. claimed: replicate levels already given to other sections of this index on this photo."""
        known = self._structure.level_ids().get(replicate.name, {})
        plates = self._structure.plates_by_design().get(design, {})
        wanted = known.get(levels.get(replicate.name))
        if wanted is not None and wanted not in claimed and (plates.get(wanted) is None or plates[wanted] in own):
            level_id = wanted
        else:
            level_id = self._free_replicate(replicate.variable_id, design, claimed, own)
        claimed.add(level_id)
        index_id = self._structure.plates_by_design().get(design, {}).get(level_id)
        if index_id is None:
            index_id = self._structure.insert_index(dict(design + ((replicate.variable_id, level_id),)))
        value = next(v for v, i in self._structure.level_ids()[replicate.name].items() if i == level_id)
        levels[replicate.name] = value
        return index_id

    # =====================================================================
    # project page and Stats
    # =====================================================================
    def project_status(self) -> ProjectStatus:
        """Counts for each step of the project page."""
        self._require_open()
        photos = self._photos.count()
        regions = len(self._photos.assigned_ids())
        detected, review, colonies = self._detections.counts()
        return ProjectStatus(photos, photos - self._photos.photos_needing_cropping(),
                             expected_plates(self._file.connection, self._file.setting("plate")), regions, detected,
                             review, colonies, self._structure.index_count())

    def stats_variables(self) -> list:
        """The design variables, as Stats sees them. A replicate variable is left out: its plates are
        interchangeable, so Stats always pools them (and One per index chooses among them)."""
        from ..core.stats import StatsVariable
        self._require_open()
        variables = []
        for v in self._structure.variables():
            if v.type == "replicate":
                continue
            control = next((lv.value for lv in v.levels if lv.is_control), None)
            variables.append(StatsVariable(v.name, v.display_name, v.type, tuple(lv.value for lv in v.levels),
                                           tuple(lv.display for lv in v.levels), control))
        return variables

    def colony_table(self):
        """Every colony of every detected index with its levels and scale (core.stats.ColonyTable)."""
        from ..core.stats import build_colony_table
        self._require_open()
        values = self._structure.all_level_values()
        indices = []
        for row in self._file.connection.execute("SELECT d.index_id, p.scale_px_per_mm AS px_per_mm FROM detection d "
                                                 "JOIN assignments a USING (index_id) JOIN photos p USING (photo_id) "
                                                 "ORDER BY d.index_id").fetchall():
            table = self.request_derived("colony_measurements", row["index_id"])
            indices.append((row["index_id"], values.get(row["index_id"], {}), _scale(row["px_per_mm"]),
                            list(table.rows)))
        return build_colony_table(self.stats_variables(), indices)

    def index_table(self, coverage: bool = True, progress=None):
        """One row per detected index with its plate-level measures (core.stats.IndexTable).

        coverage: include growth and colony coverage (core.growth; about half a second for each index
        not measured before). progress(done, total) is called as indices are measured; returning False
        stops early, leaving the rest unmeasured (NaN)."""
        from ..core.stats import build_index_table
        self._require_open()
        values = self._structure.all_level_values()
        detected = self._file.connection.execute(
            "SELECT d.index_id, p.scale_px_per_mm AS px_per_mm FROM detection d JOIN assignments a USING (index_id) "
            "JOIN photos p USING (photo_id) ORDER BY d.index_id").fetchall()
        rows = []
        measuring = coverage
        for done, row in enumerate(detected):
            table = self.request_derived("colony_measurements", row["index_id"])
            radii = [np.nan if r["radius_um"] is None else float(r["radius_um"]) for r in table.rows]
            pixels = [float(r["radius_px"]) for r in table.rows]
            measures = {"colony_count": float(len(radii)),
                        "mean_radius_um": float(np.mean(radii)) if radii else np.nan,
                        "median_radius_um": float(np.median(radii)) if radii else np.nan,
                        "mean_radius_px": float(np.mean(pixels)) if pixels else np.nan,
                        "median_radius_px": float(np.median(pixels)) if pixels else np.nan}
            if measuring:
                cover = self.request_derived("plate_coverage", row["index_id"]).rows[0]
                measures.update({key: np.nan if cover.get(key) is None else float(cover[key])
                                 for key in ("growth_coverage", "colony_coverage", "capture_ratio")})
                if progress is not None and progress(done + 1, len(detected)) is False:
                    measuring = False
            rows.append((row["index_id"], values.get(row["index_id"], {}), _scale(row["px_per_mm"]), measures))
        labels = dict(INDEX_MEASURES)
        values = self._annotations.plate_values()
        for spec in self._annotations.specs():
            if spec["kind"] != "number":
                continue
            key = f"annotation:{spec['name']}"
            unit = f" ({spec['unit']})" if spec["unit"] else ""
            labels[key] = f"{spec['display_name']}{unit}"
            for row in rows:
                text = values.get(row[0], {}).get(spec["name"], "")
                try:
                    row[3][key] = float(text)
                except (TypeError, ValueError):
                    row[3][key] = np.nan
        return build_index_table(self.stats_variables(), rows, labels)

    def index_photos(self) -> dict[int, tuple[int, str]]:
        """Each detected index's photo: {index id: (photo id, photo file name)}."""
        self._require_open()
        rows = self._file.connection.execute(
            "SELECT d.index_id, p.photo_id, p.file_name FROM detection d JOIN assignments a USING (index_id) "
            "JOIN photos p USING (photo_id) ORDER BY d.index_id").fetchall()
        return {int(r["index_id"]): (int(r["photo_id"]), str(r["file_name"])) for r in rows}

    def plate_numbers(self) -> dict[int, str]:
        """Each plate's replicate number within its index: {index id: number} ({} without a replicate variable)."""
        self._require_open()
        return {int(r["index_id"]): r["value"] for r in self._file.connection.execute(
            "SELECT il.index_id, l.value FROM index_levels il JOIN variables v USING (variable_id) "
            "JOIN levels l USING (level_id) WHERE v.type = 'replicate'")}

    # ----- annotations ------------------------------------------------------
    def annotation_specs(self) -> list[dict]:
        """Declared annotations in order: name, display_name, kind, unit, choices."""
        self._require_open()
        return self._annotations.specs()

    def add_annotation(self, spec: AnnotationSpec) -> None:
        self._require_open()
        with self._file.transaction():
            self._annotations.declare(spec.name, spec.kind, spec.display_name, spec.unit, tuple(spec.choices))

    def remove_annotation(self, name: str) -> None:
        """Remove an annotation and every value of it."""
        self._require_open()
        with self._file.transaction():
            self._annotations.remove(name)

    def set_plate_annotation(self, index_id: int, name: str, value) -> None:
        """One plate's value; '' or None removes it, so the plate inherits its photo's."""
        self._require_open()
        with self._file.transaction():
            self._annotations.set_plate_value(index_id, name, value)

    def set_photo_annotations(self, photo_id: int, values: dict) -> None:
        """One photo's values, inherited by every plate of the photo."""
        self._require_open()
        with self._file.transaction():
            for name, value in values.items():
                self._annotations.set_photo_value(photo_id, name, value)

    def plate_annotations(self) -> dict[int, dict[str, str]]:
        """Each plate's resolved values (its own, else its photo's): {index id: {name: value}}."""
        self._require_open()
        return self._annotations.plate_values()

    def photo_annotations(self) -> dict[int, dict[str, str]]:
        """Values stored at the photo level: {photo id: {name: value}}."""
        self._require_open()
        return self._annotations.photo_values()

    def plate_details(self) -> dict[int, dict]:
        """Per-plate context for analyses: {index id: {'annotations': {...}, 'replicate': str, 'photo': str}}."""
        self._require_open()
        annotations = self._annotations.plate_values()
        numbers = self.plate_numbers()
        photos = self.index_photos()
        details = {}
        for row in self._file.connection.execute("SELECT index_id FROM detection"):
            i = int(row["index_id"])
            details[i] = {"annotations": dict(annotations.get(i, {})), "replicate": numbers.get(i, ""),
                          "photo": photos.get(i, (0, ""))[1]}
        return details

    # ----- mixture fit cache ------------------------------------------------
    def mixture_fit_cache(self) -> dict:
        """Fits saved by Stats, keyed by a digest of each plate's radii: {digest: fit json}."""
        self._require_open()
        return self._file.setting("mixture_fit_cache") or {}

    def save_mixture_fits(self, fits: dict, cap: int = 4000) -> None:
        """Keep the fits for later sessions. Saving a cache does not count as a change."""
        self._require_open()
        known = self.mixture_fit_cache()
        known.update(fits or {})
        if len(known) > cap:                              # an old, grown cache starts over
            known = dict(fits or {})
        with self._file.transaction(counts_as_change=False):
            self._file.set_setting("mixture_fit_cache", known)

    def coverage_waiting(self) -> int:
        """Detected indices whose growth coverage is not measured for their current colonies yet."""
        self._require_open()
        waiting = 0
        derivation = DERIVATIONS["plate_coverage"]
        for row in self._file.connection.execute("SELECT d.index_id FROM detection d JOIN assignments a "
                                                 "USING (index_id)").fetchall():
            assignment, photo = self._region(row["index_id"])
            revision, _ = self._detections.provenance(row["index_id"])
            inputs = DerivationInputs(row["index_id"], ColonySet.empty(), _scale(photo["px_per_mm"]),
                                      revision, photo["sha256"],
                                      hashlib.sha256(bytes(assignment["outline"])).hexdigest())
            if self._cache.lookup("plate_coverage", row["index_id"], fingerprint(derivation, inputs)) is None:
                waiting += 1
        return waiting

    def growth_overlay(self, index_id: int):
        """(crop image, growth mask, marked-colony mask) of an index, in crop coordinates."""
        from ..core.growth import growth_mask
        packet = self.checkout_image(index_id)
        colonies = packet.saved.colonies if packet.saved else ColonySet.empty()
        result = growth_mask(packet.record.image, packet.record.mask, colonies)
        return packet.record.image, result.growth, result.colonies

    def analyses_folder(self) -> Path:
        """The library's Analyses folder, created with an example and a note on first use."""
        folder = self._require_library().analyses_folder
        if not folder.is_dir():
            from ..analyses.registry import CLASSIFIER_EXAMPLE, EXAMPLE
            folder.mkdir(parents=True, exist_ok=True)
            write_text(EXAMPLE, folder / "example_analysis.py.txt")
            write_text(CLASSIFIER_EXAMPLE, folder / "example_classifier.py.txt")
            write_text("Analysis plug-ins for QuantaColony Stats.\n\nEvery file ending in .py in this folder is "
                       "loaded when Stats opens. Loading a file runs its Python code, so only add files you "
                       "trust.\nexample_analysis.py.txt shows the format and example_classifier.py.txt a fuller "
                       "analysis: rename either to end in .py to try it.\n", folder / "README.txt")
        return folder

    def module_preferences(self, module: str) -> dict:
        self._require_open()
        return self._file.setting(f"{module}_preferences") or {}

    def save_module_preferences(self, module: str, preferences: dict) -> None:
        self._require_open()
        with self._file.transaction():
            self._file.set_setting(f"{module}_preferences", preferences)

    def study_record(self) -> dict:
        """The user study's record of this project (quantacolony.study): participant answers, plate order,
        stage times. Empty for projects outside the study."""
        self._require_open()
        return self._file.setting("study") or {}

    def save_study_record(self, record: dict) -> None:
        self._require_open()
        with self._file.transaction():
            self._file.set_setting("study", record)

    def export_figure(self, figure, name: str, suffix: str = ".png", path: Path | None = None) -> Path:
        path = Path(path) if path else self.exports_folder / "stats" / f"{safe_name(name)}{suffix}"
        return write_figure(figure, path)

    def export_rows_csv(self, rows: list[dict], name: str, path: Path | None = None) -> Path:
        path = Path(path) if path else self.exports_folder / "stats" / f"{safe_name(name)}.csv"
        columns = list(rows[0]) if rows else []
        return write_table_csv(columns, rows, path, {"project": self._file.setting("name"),
                                                     "software_version": __version__})

    def photos_needing_cropping(self) -> int:
        """Photos with neither a saved Cropping layout nor any region assigned."""
        self._require_open()
        return self._photos.photos_needing_cropping()

    def unassigned_index_keys(self) -> list[tuple]:
        """Combinations of design levels (values in design-variable order) that no photo region holds yet."""
        from ..core.cropping import all_index_keys
        packet_variables = self.crop_packet().variables
        names = [v.name for v in packet_variables]
        values = self._structure.all_level_values()
        assigned = {tuple(values.get(index_id, {}).get(n) for n in names) for index_id in self._photos.assigned_ids()}
        return [key for key in all_index_keys(list(packet_variables)) if key not in assigned]


def _scale(value) -> float | None:
    """A photo's px/mm as stored: a float, or None for a photo without a scale."""
    return None if value is None else float(value)
