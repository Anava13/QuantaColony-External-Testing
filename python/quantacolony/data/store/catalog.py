"""Projects as files in the library: summaries for the project list, copies in and out, backups,
and moving project files into and out of the Trash.

These work on project files that are not open (read-only connections), except
where the DataManager passes the open file so a copy includes unsaved state.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..library import Library
from ..schema import SCHEMA_VERSION
from .project_file import FileProblem, copy_database, read_only
from .structure import expected_plates, index_count

PROJECT_SUFFIX = ".qcproj"
BACKUPS_KEPT = 5
_STAMP = "%Y%m%dT%H%M%S%fZ"


@dataclass(frozen=True)
class ProjectEntry:
    """One project in the library, as the project list shows it."""

    project_id: str
    name: str
    modified: datetime
    size_bytes: int
    index_count: int                  # indices in use: combinations of design levels holding a plate
    detected_count: int               # plates with committed colonies (and a region)
    review_count: int = 0             # detected plates flagged for review
    cropped_count: int = 0            # plates with a photo region
    photo_count: int = 0
    colony_count: int = 0             # colonies counted over every detected index
    variable_count: int = 0
    created: datetime | None = None
    tutorial: bool = False            # a copy of a tutorial project
    tutorial_name: str = ""           # which one ('crowding', 'rescue', ...)
    tutorial_version: int = 0         # the bundled version it was copied from
    plate_count: int = 0              # plates placed plus photos not cropped yet (at the plate's sections)


@dataclass(frozen=True)
class BackupEntry:
    path: Path
    taken_at: datetime
    size_bytes: int


def _value(connection, key: str):
    row = connection.execute("SELECT value FROM project WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def summarize(project_id: str, path: Path) -> ProjectEntry | None:
    """A project's line in the project list, or None when the file is not readable as a project."""
    try:
        connection = read_only(path)
        try:
            name = _value(connection, "name")
            created = _value(connection, "created_at")
            marker = _value(connection, "tutorial")
            tutorial = marker is not None
            tutorial_name = str((marker or {}).get("name", "")) if isinstance(marker, dict) else ""
            tutorial_version = int((marker or {}).get("version", 0) or 0) if isinstance(marker, dict) else 0
            variable_count = connection.execute("SELECT COUNT(*) FROM variables").fetchone()[0]
            indices = index_count(connection)
            plates = expected_plates(connection, _value(connection, "plate"))
            cropped = connection.execute("SELECT COUNT(*) FROM assignments").fetchone()[0]
            detected, review = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(d.needs_review), 0) FROM detection d "
                "JOIN assignments a USING (index_id)").fetchone()
            colonies = connection.execute("SELECT COUNT(*) FROM colonies c JOIN assignments a USING (index_id) "
                                          "WHERE c.in_final = 1").fetchone()[0]
            photos = connection.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        finally:
            connection.close()
    except (sqlite3.DatabaseError, TypeError):
        return None
    if name is None:
        return None
    stat = path.stat()
    try:
        created_at = datetime.fromisoformat(created) if created else None
    except ValueError:
        created_at = None
    return ProjectEntry(project_id, name, datetime.fromtimestamp(stat.st_mtime, timezone.utc), stat.st_size,
                        indices, detected, int(review), cropped, photos, colonies, variable_count, created_at,
                        tutorial, plate_count=plates, tutorial_name=tutorial_name,
                        tutorial_version=tutorial_version)


def list_projects(library: Library) -> list[ProjectEntry]:
    """Every project in the library, most recently changed first."""
    entries = [summarize(project_id, library.project_path(project_id)) for project_id in library.project_ids()]
    return sorted((e for e in entries if e is not None), key=lambda e: e.modified, reverse=True)


def thumbnail(path: Path) -> bytes | None:
    """The first photo's thumbnail (PNG bytes), or None (no photos, or a file from before thumbnails)."""
    try:
        connection = read_only(path)
        try:
            row = connection.execute("SELECT thumbnail FROM photos ORDER BY photo_id LIMIT 1").fetchone()
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        return None
    return bytes(row[0]) if row and row[0] is not None else None


# ----- sharing -----------------------------------------------------------------
def export_copy(library: Library, project_id: str, destination: Path, open_connection=None) -> Path:
    """A consistent copy of a project for sending to someone, outside the library."""
    destination = Path(destination)
    if destination.suffix != PROJECT_SUFFIX:
        destination = destination.with_suffix(PROJECT_SUFFIX)
    if library.root.resolve() in destination.resolve().parents:
        raise FileProblem("Export to a folder outside the library")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if open_connection is not None:
        copy_database(open_connection, destination)
    else:
        source = read_only(library.project_path(project_id))
        try:
            copy_database(source, destination)
        finally:
            source.close()
    return destination


def import_copy(library: Library, source: Path) -> str:
    """Copy a shared .qcproj into the library; returns its project id. A project already in the
    library is imported as a copy with a new id and name."""
    source = Path(source)
    try:
        original = read_only(source)
    except sqlite3.DatabaseError as error:
        raise FileProblem(f"{source.name} cannot be opened ({error})") from error
    try:
        try:
            ok = original.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            values = {r["key"]: json.loads(r["value"]) for r in original.execute("SELECT key, value FROM project")}
        except sqlite3.DatabaseError as error:
            raise FileProblem(f"{source.name} is not a QuantaColony project ({error})") from error
        if not ok:
            raise FileProblem(f"{source.name} is damaged and was not imported")
        if values.get("schema_version", SCHEMA_VERSION + 1) > SCHEMA_VERSION:
            raise FileProblem(f"{source.name} needs a newer QuantaColony")
        project_id = values.get("project_id") or uuid.uuid4().hex
        name = values.get("name", source.stem)
        if project_id in library.project_ids():
            project_id = uuid.uuid4().hex
        names = {entry.name for entry in list_projects(library)}
        base, copy = name, 1
        while name in names:
            name = f"{base} (copy {copy})" if copy > 1 else f"{base} (copy)"
            copy += 1
        target = library.project_path(project_id)
        library.projects_folder.mkdir(parents=True, exist_ok=True)
        copy_database(original, target)
    finally:
        original.close()
    _set_values(target, {"project_id": project_id, "name": name})
    return project_id


def _set_values(path: Path, values: dict) -> None:
    """Write project settings into a closed project file."""
    connection = sqlite3.connect(str(path), isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany("INSERT OR REPLACE INTO project (key, value) VALUES (?, ?)",
                               [(key, json.dumps(value)) for key, value in values.items()])
        connection.execute("COMMIT")
    finally:
        connection.close()


def free_project_name(library: Library, name: str, suffix: str = "restored") -> str:
    """name, or 'name (restored)', 'name (restored 2)', ... when a project in the library has it."""
    names = {entry.name for entry in list_projects(library)}
    candidate, number = name, 1
    while candidate in names:
        candidate = f"{name} ({suffix})" if number == 1 else f"{name} ({suffix} {number})"
        number += 1
    return candidate


# ----- the Trash -----------------------------------------------------------------
def _sidecars(path: Path) -> list[Path]:
    return [path.with_name(path.name + ending) for ending in ("-journal", "-wal", "-shm")]


def _move_project(library: Library, project_id: str, source: Path, target: Path, source_backups: Path,
                  target_backups: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileProblem(f"{target.name} is already there")
    source.replace(target)                                   # same drive: instant
    for old, new in zip(_sidecars(source), _sidecars(target)):
        if old.exists():
            old.replace(new)
    if source_backups.is_dir():
        target_backups.parent.mkdir(parents=True, exist_ok=True)
        if target_backups.exists():
            shutil.rmtree(target_backups)
        source_backups.replace(target_backups)


def move_to_trash(library: Library, project_id: str) -> None:
    """Move a project file and its backups into the Trash (the caller holds the project's lock)."""
    _move_project(library, project_id, library.project_path(project_id), library.trash_path(project_id),
                  library.backups_folder / project_id, library.trash_folder / "Backups" / project_id)


def restore_from_trash(library: Library, project_id: str) -> str:
    """Move a project back from the Trash. Returns its name, changed when another project has taken it."""
    trashed = library.trash_path(project_id)
    entry = summarize(project_id, trashed)
    name = entry.name if entry is not None else project_id
    free = free_project_name(library, name)
    if project_id in library.project_ids():
        raise FileProblem("A project with the same id is already in the library")
    _move_project(library, project_id, trashed, library.project_path(project_id),
                  library.trash_folder / "Backups" / project_id, library.backups_folder / project_id)
    if free != name:
        _set_values(library.project_path(project_id), {"name": free})
    return free


def purge(library: Library, project_id: str) -> None:
    """Delete a trashed project and its backups for good."""
    path = library.trash_path(project_id)
    for file in [path] + _sidecars(path):
        file.unlink(missing_ok=True)
    shutil.rmtree(library.trash_folder / "Backups" / project_id, ignore_errors=True)


def trashed_projects(library: Library) -> dict[str, str]:
    """Project files in the Trash: {project id: name}."""
    if not library.trash_folder.is_dir():
        return {}
    found = {}
    for path in sorted(library.trash_folder.glob(f"*{PROJECT_SUFFIX}")):
        entry = summarize(path.stem, path)
        found[path.stem] = entry.name if entry is not None else path.stem
    return found


def trash_size(library: Library, project_ids) -> int:
    total = 0
    for project_id in project_ids:
        path = library.trash_path(project_id)
        if path.is_file():
            total += path.stat().st_size
    return total


def library_project_id(library: Library, path: Path) -> str | None:
    """The project id when path is a project file inside this library, else None."""
    try:
        path = Path(path).resolve()
    except OSError:
        return None
    if path.parent == library.projects_folder.resolve() and path.suffix == PROJECT_SUFFIX \
            and path.stem in library.project_ids():
        return path.stem
    return None


# ----- backups -----------------------------------------------------------------
def snapshot(library: Library, connection: sqlite3.Connection, project_id: str) -> Path:
    folder = library.backups_folder / project_id
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{datetime.now(timezone.utc).strftime(_STAMP)}{PROJECT_SUFFIX}"
    copy_database(connection, target)
    for old in sorted(folder.glob(f"*{PROJECT_SUFFIX}"))[:-BACKUPS_KEPT]:
        old.unlink(missing_ok=True)
    return target


def snapshot_file(library: Library, project_id: str) -> Path:
    current = read_only(library.project_path(project_id))
    try:
        return snapshot(library, current, project_id)
    finally:
        current.close()


def list_backups(library: Library, project_id: str) -> list[BackupEntry]:
    """Snapshots of a project, newest first."""
    folder = library.backups_folder / project_id
    entries = []
    for path in sorted(folder.glob(f"*{PROJECT_SUFFIX}"), reverse=True):
        taken = datetime.strptime(path.stem, _STAMP).replace(tzinfo=timezone.utc)
        entries.append(BackupEntry(path, taken, path.stat().st_size))
    return entries


def restore_file(library: Library, project_id: str, backup: Path) -> None:
    """Overwrite a (closed, locked) project file with a snapshot."""
    source = read_only(backup)
    try:
        copy_database(source, library.project_path(project_id))
    finally:
        source.close()
