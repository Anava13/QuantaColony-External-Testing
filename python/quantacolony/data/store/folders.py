"""How projects are grouped in the library: nested folders and the Trash (records only, Qt-free).

folders.json sits in the library root:

    {"format": 1,
     "folders":   {folder id: {"name": ..., "parent": folder id, or "" at the top level}},
     "placement": {project id: folder id},       a project not listed sits at the top level
     "trash":     [item, ...]}

A Trash item is one delete: a project, or a folder with everything in it.

    {"item_id", "kind": "project" or "folder", "name", "deleted_at" (UTC, ISO),
     "parent":   the folder it was in,
     "folders":  {folder id: {"name", "parent"}}  the deleted folder and its subfolders (folder items),
     "projects": {project id: folder id}}         the projects it took, and where they sat

Folders are records only. Project files stay in Projects/ named by id, so opening, locks,
backups, export and moving the library never depend on folders. The DataManager moves the
files of a deleted project into Trash/ (see catalog) and keeps these records; nothing else
writes this file.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..library import Library

FOLDERS_FILE = "folders.json"
FOLDERS_FORMAT = 1
TOP = ""                                   # the top level of the library


class FolderProblem(Exception):
    """A folder request that cannot be done; the message is meant for the user."""


@dataclass(frozen=True)
class FolderEntry:
    folder_id: str
    name: str
    parent: str


@dataclass(frozen=True)
class TrashEntry:
    item_id: str
    kind: str                              # 'project' or 'folder'
    name: str
    deleted_at: datetime
    project_ids: tuple[str, ...]
    folder_count: int                      # folders it holds (a folder item: the folder and its subfolders)


@dataclass
class Organization:
    folders: dict = field(default_factory=dict)
    placement: dict = field(default_factory=dict)
    trash: list = field(default_factory=list)

    # ----- reading -------------------------------------------------------------------
    def entries(self) -> list[FolderEntry]:
        return sorted((FolderEntry(fid, f["name"], f.get("parent", TOP)) for fid, f in self.folders.items()),
                      key=lambda e: e.name.lower())

    def folder_of(self, project_id: str) -> str:
        folder = self.placement.get(project_id, TOP)
        return folder if folder in self.folders else TOP

    def subtree(self, folder_id: str) -> list[str]:
        """The folder and every folder inside it, at any depth."""
        found, frontier = [folder_id], [folder_id]
        while frontier:
            children = [fid for fid, f in self.folders.items() if f.get("parent", TOP) in frontier]
            found += children
            frontier = children
        return found

    def path(self, folder_id: str) -> list[str]:
        """Folder names from the top level down to this folder."""
        names, current, seen = [], folder_id, set()
        while current in self.folders and current not in seen:
            seen.add(current)
            names.append(self.folders[current]["name"])
            current = self.folders[current].get("parent", TOP)
        return list(reversed(names))

    def trash_entries(self) -> list[TrashEntry]:
        items = []
        for item in self.trash:
            try:
                deleted = datetime.fromisoformat(item["deleted_at"])
            except (KeyError, ValueError):
                deleted = datetime.now(timezone.utc)
            items.append(TrashEntry(item["item_id"], item["kind"], item["name"], deleted,
                                    tuple(item.get("projects", {})), len(item.get("folders", {}))))
        return sorted(items, key=lambda e: e.deleted_at, reverse=True)

    def trash_item(self, item_id: str) -> dict:
        item = next((i for i in self.trash if i["item_id"] == item_id), None)
        if item is None:
            raise FolderProblem("That item is no longer in the Trash")
        return item

    # ----- folders -------------------------------------------------------------------
    def _check_name(self, name: str, parent: str, ignore: str = "") -> str:
        name = " ".join(name.split())
        if not name:
            raise FolderProblem("A folder needs a name")
        if any(f.get("parent", TOP) == parent and f["name"].lower() == name.lower() and fid != ignore
               for fid, f in self.folders.items()):
            raise FolderProblem(f"This folder already holds a folder named {name}")
        return name

    def _check_parent(self, parent: str) -> None:
        if parent != TOP and parent not in self.folders:
            raise FolderProblem("That folder no longer exists")

    def free_name(self, name: str, parent: str) -> str:
        """name, or 'name (2)', 'name (3)', ... when the parent already holds that name."""
        taken = {f["name"].lower() for f in self.folders.values() if f.get("parent", TOP) == parent}
        candidate, number = name, 2
        while candidate.lower() in taken:
            candidate, number = f"{name} ({number})", number + 1
        return candidate

    def create_folder(self, name: str, parent: str = TOP) -> str:
        self._check_parent(parent)
        name = self._check_name(name, parent)
        folder_id = uuid.uuid4().hex
        self.folders[folder_id] = {"name": name, "parent": parent}
        return folder_id

    def rename_folder(self, folder_id: str, name: str) -> None:
        if folder_id not in self.folders:
            raise FolderProblem("That folder no longer exists")
        self.folders[folder_id]["name"] = self._check_name(name, self.folders[folder_id].get("parent", TOP), folder_id)

    def move_folder(self, folder_id: str, parent: str) -> None:
        if folder_id not in self.folders:
            raise FolderProblem("That folder no longer exists")
        self._check_parent(parent)
        if parent in self.subtree(folder_id):
            raise FolderProblem("A folder cannot move into itself or into a folder inside it")
        self._check_name(self.folders[folder_id]["name"], parent, folder_id)
        self.folders[folder_id]["parent"] = parent

    def place(self, project_ids, folder_id: str) -> None:
        self._check_parent(folder_id)
        for project_id in project_ids:
            if folder_id == TOP:
                self.placement.pop(project_id, None)
            else:
                self.placement[project_id] = folder_id

    # ----- the Trash -----------------------------------------------------------------
    def trash_projects(self, project_ids: dict[str, str]) -> list[str]:
        """Record each project (id: name) as its own Trash item. Returns the item ids."""
        items = []
        for project_id, name in project_ids.items():
            item_id = uuid.uuid4().hex
            self.trash.append({"item_id": item_id, "kind": "project", "name": name, "deleted_at": _now(),
                               "parent": self.folder_of(project_id), "folders": {},
                               "projects": {project_id: self.folder_of(project_id)}})
            self.placement.pop(project_id, None)
            items.append(item_id)
        return items

    def projects_in(self, folder_id: str, project_ids) -> list[str]:
        """The projects (of project_ids) placed in the folder or any folder inside it."""
        inside = set(self.subtree(folder_id))
        return [p for p in project_ids if self.folder_of(p) in inside]

    def trash_folder(self, folder_id: str, project_ids) -> str:
        """Record a folder, its subfolders and the given projects inside them as one Trash item."""
        if folder_id not in self.folders:
            raise FolderProblem("That folder no longer exists")
        inside = self.subtree(folder_id)
        item_id = uuid.uuid4().hex
        self.trash.append({"item_id": item_id, "kind": "folder", "name": self.folders[folder_id]["name"],
                           "deleted_at": _now(), "parent": self.folders[folder_id].get("parent", TOP),
                           "folders": {fid: dict(self.folders[fid]) for fid in inside},
                           "projects": {p: self.folder_of(p) for p in project_ids}})
        for project_id in project_ids:
            self.placement.pop(project_id, None)
        for fid in inside:
            self.folders.pop(fid)
        return item_id

    def restore(self, item_id: str) -> dict:
        """Put an item's folders and placements back; returns the item (its files are the caller's).
        Its top folder goes back where it was, or to the top level when that folder is gone."""
        item = self.trash_item(item_id)
        top = TOP
        if item["kind"] == "folder":
            parent = item.get("parent", TOP) if item.get("parent", TOP) in self.folders else TOP
            restored = item["folders"]
            root = next((fid for fid, f in restored.items() if f.get("parent", TOP) not in restored), None)
            for fid, f in restored.items():
                record = dict(f)
                if fid == root:
                    record["parent"] = parent
                    record["name"] = self.free_name(f["name"], parent)
                self.folders[fid] = record
            top = root or TOP
        for project_id, folder in item.get("projects", {}).items():
            if folder in self.folders:
                self.placement[project_id] = folder
            elif top != TOP:
                self.placement[project_id] = top
        self.trash.remove(item)
        return item

    def forget(self, item_id: str) -> dict:
        item = self.trash_item(item_id)
        self.trash.remove(item)
        return item

    def tidy(self, library_projects, trashed_files) -> None:
        """Match the records to the files: placements of projects no longer in the library are dropped,
        Trash records lose projects whose files are gone (a project item with none left is dropped), and
        a file in the Trash that no record names (an interrupted delete) gets its own item."""
        present = set(library_projects)
        self.placement = {p: f for p, f in self.placement.items() if p in present and f in self.folders}
        trashed = dict(trashed_files)                       # project id -> name
        named = set()
        kept = []
        for item in self.trash:
            item["projects"] = {p: f for p, f in item.get("projects", {}).items() if p in trashed}
            named |= set(item["projects"])
            if item["kind"] == "folder" or item["projects"]:
                kept.append(item)
        for project_id, name in trashed.items():
            if project_id not in named:
                kept.append({"item_id": uuid.uuid4().hex, "kind": "project", "name": name, "deleted_at": _now(),
                             "parent": TOP, "folders": {}, "projects": {project_id: TOP}})
        self.trash = kept


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----- the file -------------------------------------------------------------------------
def folders_path(library: Library) -> Path:
    return library.root / FOLDERS_FILE


def load(library: Library) -> Organization:
    """The library's folders and Trash records. A missing file is an empty organization; an unreadable
    one is set aside (folders.json.unreadable) so the next save does not overwrite it."""
    path = folders_path(library)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format", 0) > FOLDERS_FORMAT:
            raise FolderProblem("This library's folders were saved by a newer QuantaColony")
        return Organization({str(k): {"name": str(v["name"]), "parent": str(v.get("parent", TOP))}
                             for k, v in dict(data.get("folders", {})).items()},
                            {str(k): str(v) for k, v in dict(data.get("placement", {})).items()},
                            list(data.get("trash", [])))
    except FileNotFoundError:
        return Organization()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        try:
            path.replace(path.with_name(FOLDERS_FILE + ".unreadable"))
        except OSError:
            pass
        return Organization()


def save(library: Library, organization: Organization) -> None:
    path = folders_path(library)
    temporary = path.with_name(FOLDERS_FILE + ".tmp")
    temporary.write_text(json.dumps({"format": FOLDERS_FORMAT, "folders": organization.folders,
                                     "placement": organization.placement, "trash": organization.trash},
                                    indent=1), encoding="utf-8")
    temporary.replace(path)
