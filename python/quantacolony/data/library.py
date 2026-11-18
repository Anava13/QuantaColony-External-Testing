"""The project library: one folder that holds every project, its backups and a marker file.

Finding the library: settings.json (always in local application data) stores
the library's path and id. The library folder holds library.json with the
same id. When the folder is not at the stored path, the same path is tried on
every other drive letter (an external drive that came back under a new
letter); otherwise the user locates the folder or creates a new library.
Projects hold their photos and refer to nothing outside themselves, so a
library moved intact never breaks a project.

The program itself never lives in the library: it is installed in a fixed
folder (see README), and a library is refused inside that folder, because
reinstalling QuantaColony replaces it.

    <library>/
      library.json                 marker: library id, format
      Projects/<project id>.qcproj one SQLite file per project
      Projects/<project id>.lock   held while the project is open
      Backups/<project id>/<UTC time>.qcproj
      Analyses/*.py                analysis plug-ins for Stats (see quantacolony/analyses/api.py)
      folders.json                 folders and how projects sit in them; Trash records (store/folders.py)
      Trash/<project id>.qcproj    deleted projects, until restored or deleted permanently
      Trash/Backups/<project id>/  their backups
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import string
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

LIBRARY_MARKER = "library.json"
LIBRARY_FORMAT = 1
PROJECTS_FOLDER = "Projects"
BACKUPS_FOLDER = "Backups"
ANALYSES_FOLDER = "Analyses"
TRASH_FOLDER = "Trash"
LOW_SPACE_BYTES = 5 * 1024 ** 3        # warn below 5 GB free: photos make projects large

# Folder names that sync programs create; SQLite files there can be copied mid-write
SYNC_FOLDER_PREFIXES = ("onedrive", "dropbox", "google drive", "googledrive", "icloud drive", "iclouddrive",
                        "icloud~", "pcloud")
SYNC_FOLDER_NAMES = ("my drive", "box", "box sync", "mega", "megasync")


class LibraryError(Exception):
    """A library problem; the message is meant for the user."""


@dataclass(frozen=True)
class Library:
    root: Path
    library_id: str

    @property
    def projects_folder(self) -> Path:
        return self.root / PROJECTS_FOLDER

    @property
    def backups_folder(self) -> Path:
        return self.root / BACKUPS_FOLDER

    @property
    def analyses_folder(self) -> Path:
        """Analysis plug-ins (.py files) that Stats loads besides the built-in ones."""
        return self.root / ANALYSES_FOLDER

    @property
    def trash_folder(self) -> Path:
        return self.root / TRASH_FOLDER

    def trash_path(self, project_id: str) -> Path:
        return self.trash_folder / f"{project_id}.qcproj"

    def project_path(self, project_id: str) -> Path:
        return self.projects_folder / f"{project_id}.qcproj"

    def lock_path(self, project_id: str) -> Path:
        return self.projects_folder / f"{project_id}.lock"

    def project_ids(self) -> list[str]:
        if not self.projects_folder.is_dir():
            return []
        return sorted(p.stem for p in self.projects_folder.glob("*.qcproj"))


@dataclass(frozen=True)
class LocationCheck:
    ok: bool
    problem: str             # why the folder cannot hold a library, empty when ok
    warning: str             # usable, but worth knowing (low space), may be empty
    free_bytes: int


# ----- marker ----------------------------------------------------------------
def read_marker(root: Path) -> str | None:
    """Library id from root/library.json, or None when root is not a library."""
    try:
        data = json.loads((Path(root) / LIBRARY_MARKER).read_text(encoding="utf-8"))
        return str(data["library_id"]) if data.get("format", 0) <= LIBRARY_FORMAT else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_marker(root: Path, library_id: str) -> None:
    temporary = root / (LIBRARY_MARKER + ".tmp")
    temporary.write_text(json.dumps({"library_id": library_id, "format": LIBRARY_FORMAT,
                                     "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                                    indent=2), encoding="utf-8")
    temporary.replace(root / LIBRARY_MARKER)


def open_library(root: Path) -> Library:
    library_id = read_marker(root)
    if library_id is None:
        raise LibraryError(f"{root} is not a QuantaColony library (no {LIBRARY_MARKER})")
    return Library(Path(root).resolve(), library_id)


def create_library(root: Path) -> Library:
    """New library at root (an empty or missing folder), or the library already there."""
    root = Path(root)
    if read_marker(root) is not None:
        return open_library(root)
    check = check_location(root)
    if not check.ok:
        raise LibraryError(check.problem)
    if root.exists() and any(root.iterdir()):
        raise LibraryError(f"{root} already holds other files. Choose an empty folder.")
    (root / PROJECTS_FOLDER).mkdir(parents=True, exist_ok=True)
    (root / BACKUPS_FOLDER).mkdir(exist_ok=True)
    library_id = uuid.uuid4().hex
    _write_marker(root, library_id)
    return Library(root.resolve(), library_id)


# ----- finding a library that moved -------------------------------------------
def find_library(stored_path: str, library_id: str) -> Library | None:
    """The library with this id at its stored path, or at the same path on another drive."""
    if not stored_path or not library_id:
        return None
    for candidate in _candidates(Path(stored_path)):
        if read_marker(candidate) == library_id:
            return Library(candidate.resolve(), library_id)
    return None


def _candidates(path: Path):
    yield path
    if sys.platform == "win32" and path.drive and len(path.drive) == 2:
        rest = Path(*path.parts[1:])
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if drive.upper() != path.anchor.upper() and os.path.exists(drive):
                yield Path(drive) / rest


# ----- choosing a location -----------------------------------------------------
def check_location(folder: Path) -> LocationCheck:
    """Whether a folder can hold the library: local, outside sync folders, with space."""
    folder = Path(folder).expanduser().absolute()
    existing = folder
    while not existing.exists() and existing.parent != existing:
        existing = existing.parent
    try:
        free = shutil.disk_usage(existing).free
    except OSError:
        free = 0
    resolved = _resolved(folder)

    if _is_network(folder) or _is_network(resolved):
        return LocationCheck(False, "This folder is on a network drive. SQLite project files need a local drive, "
                                    "so the library cannot go there.", "", free)
    program = next((root for root in program_folders() if _is_within(resolved, root)), None)
    if program is not None:
        return LocationCheck(False, f"This folder is inside the QuantaColony program folder ({program}). "
                                    "Installing or updating QuantaColony replaces that folder, so the library "
                                    "cannot go there.", "", free)
    sync = _sync_folder(resolved)
    if sync:
        return LocationCheck(False, f"This folder is inside {sync}, which copies files while they are open "
                                    "and can damage project files. Choose a folder outside it.", "", free)
    if read_marker(folder) is None and folder.exists() and not folder.is_dir():
        return LocationCheck(False, f"{folder} is a file, not a folder.", "", free)
    warning = ""
    if free < LOW_SPACE_BYTES:
        warning = f"Only {free / 1024 ** 3:.1f} GB free on this drive; projects with many photos need several GB."
    return LocationCheck(True, "", warning, free)


def program_folders() -> list[Path]:
    """Folders that hold the running program: its Python environment and, when it runs from a source
    folder, that folder. Reinstalling or deleting the program removes them, so no library goes inside.
    In a browser (Pyodide) the program is loaded into the page's memory with Python's prefix at '/':
    nothing is installed, so no folder is protected."""
    if sys.platform == "emscripten":
        return []
    folders = [Path(sys.prefix).resolve()]
    source = Path(__file__).resolve().parents[2]          # the folder holding the quantacolony package
    if (source / "pyproject.toml").is_file():
        folders.append(source)
    return folders


def _sync_folder(path: Path) -> str:
    """Name of the sync service whose folder contains path, or ''."""
    for variable in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        root = os.environ.get(variable)
        if root and _is_within(path, Path(root)):
            return "OneDrive"
    for part in path.parts:
        lowered = part.lower()
        if lowered.startswith(SYNC_FOLDER_PREFIXES) or lowered in SYNC_FOLDER_NAMES:
            return part
    return ""


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _is_network(path: Path) -> bool:
    text = str(path)
    if text.startswith("\\\\") or text.startswith("//"):
        return True
    if sys.platform == "win32" and path.drive:
        import ctypes
        DRIVE_REMOTE = 4
        return ctypes.windll.kernel32.GetDriveTypeW(path.anchor) == DRIVE_REMOTE
    if sys.platform.startswith("linux"):
        try:
            mounts = [line.split() for line in Path("/proc/mounts").read_text().splitlines()]
        except OSError:
            return False
        best, fstype = "", ""
        for fields in mounts:
            if len(fields) >= 3 and _is_within(path, Path(fields[1])) and len(fields[1]) > len(best):
                best, fstype = fields[1], fields[2]
        return fstype in {"nfs", "nfs4", "cifs", "smb3", "smbfs", "sshfs", "fuse.sshfs", "9p"}
    return False


# ----- moving the library --------------------------------------------------------
@dataclass(frozen=True)
class LibraryMove:
    library: Library              # the library at its new place
    leftover: Path | None         # the old folder when it could not be removed completely, else None


SKIPPED_ON_MOVE = ("*.lock", "*.tmp", "*.creating")


def move_library(library: Library, destination: Path) -> LibraryMove:
    """Copy everything in the library to destination, verify the copy, then remove the old folder.

    Every folder in the library moves (Projects, Backups, Analyses and any other). The marker is
    written last, so an interrupted copy leaves the old library complete and the new folder
    unrecognized. Once the new library is complete, the old marker is removed first, so the old
    folder is no longer a library even when a file in it cannot be deleted (open in another
    program); the move still succeeds and reports that folder as leftover. Close every project first.
    """
    destination = Path(destination)
    check = check_location(destination)
    if not check.ok:
        raise LibraryError(check.problem)
    if _is_within(_resolved(destination), library.root):
        raise LibraryError("The library cannot move into a folder inside itself. Choose a folder outside it.")
    if destination.exists() and any(destination.iterdir()):
        raise LibraryError(f"{destination} already holds other files. Choose an empty folder.")
    for lock in library.projects_folder.glob("*.lock"):
        if is_locked(lock):
            raise LibraryError("A project is open. Close it before moving the library.")

    destination.mkdir(parents=True, exist_ok=True)
    try:
        _copy_contents(library.root, destination)
        problem = _copy_problem(library.root, destination)
    except OSError as error:
        problem = f"Copying failed ({error})"
    if problem:
        shutil.rmtree(destination, ignore_errors=True)
        raise LibraryError(f"{problem}; the library was not moved.")
    _write_marker(destination, library.library_id)

    (library.root / LIBRARY_MARKER).unlink(missing_ok=True)
    shutil.rmtree(library.root, ignore_errors=True)
    leftover = library.root if library.root.exists() else None
    return LibraryMove(Library(destination.resolve(), library.library_id), leftover)


def _resolved(path: Path) -> Path:
    """path made absolute through its nearest existing folder (path itself may not exist yet)."""
    path = Path(path).expanduser().absolute()
    existing = path
    while not existing.exists() and existing.parent != existing:
        existing = existing.parent
    return existing.resolve() / path.relative_to(existing)


def _copy_contents(source: Path, destination: Path) -> None:
    """Every file and folder of the library except the marker, locks and unfinished files."""
    ignore = shutil.ignore_patterns(*SKIPPED_ON_MOVE)
    skipped = set(ignore(str(source), [p.name for p in source.iterdir()])) | {LIBRARY_MARKER}
    for item in source.iterdir():
        if item.name in skipped:
            continue
        if item.is_dir():
            shutil.copytree(item, destination / item.name, ignore=ignore, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination / item.name)
    (destination / PROJECTS_FOLDER).mkdir(exist_ok=True)
    (destination / BACKUPS_FOLDER).mkdir(exist_ok=True)


def _copy_problem(source: Path, destination: Path) -> str:
    """Why the copy differs from the library, or '' when every file arrived whole."""
    ignore = shutil.ignore_patterns(*SKIPPED_ON_MOVE)
    for original in source.rglob("*"):
        relative = original.relative_to(source)
        if not original.is_file() or relative == Path(LIBRARY_MARKER) or ignore("", [original.name]):
            continue
        copied = destination / relative
        if not copied.is_file() or copied.stat().st_size != original.stat().st_size:
            return f"The copy of {relative} did not match the original"
        if relative.parent == Path(PROJECTS_FOLDER) and copied.suffix == ".qcproj" and not _intact(copied):
            return f"The copy of {relative} is damaged"
    return ""


def _intact(path: Path) -> bool:
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            return connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        return False


# ----- one QuantaColony window per project ---------------------------------------
class ProjectLock:
    """An operating-system file lock held while a project is open.

    The operating system releases it when the process ends, so a crash never
    leaves a project locked.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._handle = None

    def acquire(self) -> None:
        handle = open(self.path, "a+", encoding="utf-8")
        try:
            _lock(handle)
        except OSError as error:
            handle.seek(0)
            holder = handle.read().strip() or "another QuantaColony window"
            handle.close()
            raise LibraryError(f"This project is already open ({holder}). Close it there first.") from error
        handle.seek(0)
        handle.truncate()
        handle.write(f"{socket.gethostname()}, process {os.getpid()}")
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is not None:
            try:
                _unlock(self._handle)
            finally:
                self._handle.close()
                self._handle = None
            self.path.unlink(missing_ok=True)


def is_locked(path: Path) -> bool:
    try:
        with open(path, "a+", encoding="utf-8") as handle:
            _lock(handle)
            _unlock(handle)
        return False
    except OSError:
        return True


if sys.platform == "win32":
    import msvcrt

    def _lock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
elif sys.platform == "emscripten":
    # In a browser (Pyodide) one page is one process, and its files live in that page's memory: nothing
    # else can open the project, so there is nothing to lock.
    def _lock(handle) -> None:
        pass

    def _unlock(handle) -> None:
        pass
else:
    import fcntl

    def _lock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
