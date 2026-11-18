"""Program settings outside any project: where the library is, and the last project used.

settings.json lives in a fixed place the operating system reports at every
start (%LOCALAPPDATA%\\QuantaColony on Windows), so it is always found. It
points to the project library, which may sit anywhere the user chose at
first start. QUANTACOLONY_HOME overrides the settings folder (tests, demos).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

MAX_RECENT = 10


def default_settings_folder() -> Path:
    override = os.environ.get("QUANTACOLONY_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "QuantaColony"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "QuantaColony"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "quantacolony"


def default_library_folder() -> Path:
    """Offered at first start: the library next to the settings, in local application data."""
    return default_settings_folder() / "Library"


@dataclass
class Settings:
    folder: Path = field(default_factory=default_settings_folder)
    library_path: str = ""            # where the library was last found
    library_id: str = ""              # id in the library's marker file
    last_project_id: str = ""
    recent_project_ids: list[str] = field(default_factory=list)
    expanded_folders: list[str] = field(default_factory=list)     # home page folders shown open, on this computer
    tutorial_step: str = ""                                       # the tutorial step shown last
    tutorial_done: list[str] = field(default_factory=list)        # tutorial steps whose task was done
    bundled_projects: int = 0                                     # the bundled-project version this computer added

    @property
    def path(self) -> Path:
        return self.folder / "settings.json"

    @classmethod
    def load(cls, folder: Path | None = None) -> "Settings":
        settings = cls(folder=Path(folder) if folder else default_settings_folder())
        try:
            data = json.loads(settings.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return settings                     # missing or unreadable: first start
        settings.library_path = str(data.get("library_path", ""))
        settings.library_id = str(data.get("library_id", ""))
        settings.last_project_id = str(data.get("last_project_id", ""))
        settings.recent_project_ids = [str(p) for p in data.get("recent_project_ids", [])][:MAX_RECENT]
        settings.expanded_folders = [str(f) for f in data.get("expanded_folders", [])]
        settings.tutorial_step = str(data.get("tutorial_step", ""))
        settings.tutorial_done = [str(k) for k in data.get("tutorial_done", [])]
        settings.bundled_projects = int(data.get("bundled_projects", 0) or 0)
        return settings

    def set_library(self, path: Path, library_id: str) -> None:
        self.library_path, self.library_id = str(Path(path).resolve()), library_id
        self.save()

    def remember_project(self, project_id: str) -> None:
        self.last_project_id = project_id
        others = [p for p in self.recent_project_ids if p != project_id]
        self.recent_project_ids = [project_id] + others[:MAX_RECENT - 1]
        self.save()

    def set_expanded_folders(self, folder_ids) -> None:
        self.expanded_folders = sorted(set(folder_ids))
        self.save()

    def set_tutorial(self, step: str, done) -> None:
        self.tutorial_step, self.tutorial_done = step, sorted(set(done))
        self.save()

    def set_bundled_projects(self, version: int) -> None:
        self.bundled_projects = int(version)
        self.save()

    def forget_project(self, project_id: str) -> None:
        self.recent_project_ids = [p for p in self.recent_project_ids if p != project_id]
        if self.last_project_id == project_id:
            self.last_project_id = ""
        self.save()

    def save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "library_path": self.library_path, "library_id": self.library_id,
            "last_project_id": self.last_project_id, "recent_project_ids": self.recent_project_ids,
            "expanded_folders": self.expanded_folders,
            "tutorial_step": self.tutorial_step, "tutorial_done": self.tutorial_done,
            "bundled_projects": self.bundled_projects,
        }, indent=2), encoding="utf-8")
        temporary.replace(self.path)              # atomic on the same drive
