"""DataManager: the single source of truth for QuantaColony projects.

Modules talk to the DataManager; only the DataManager touches files.
"""

from .library import (Library, LibraryError, LibraryMove, check_location, create_library, find_library, move_library,
                      open_library)
from .manager import PROJECT_SUFFIX, BackupEntry, DataManager, FolderEntry, ProjectEntry, ProjectError, TrashEntry
from .settings import Settings, default_library_folder

__all__ = ["DataManager", "ProjectError", "ProjectEntry", "BackupEntry", "FolderEntry", "TrashEntry", "PROJECT_SUFFIX", "Settings",
           "Library", "LibraryError", "LibraryMove", "create_library", "open_library", "find_library", "move_library",
           "check_location", "default_library_folder"]
