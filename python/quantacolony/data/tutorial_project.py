"""The tutorial project that comes with QuantaColony: the wild-type dilution series (Crowding).

Ten plates from one culture, spread at 100X to 128000X and incubated and photographed together,
so plating density is the only thing that differs between them. Every plate is cropped and
detected, and growth coverage is cached. It shows every module on real data, and its result
matters to every user: colony size depends on how crowded a plate is (median radius rises about
3-fold from the densest to the sparsest plate), and the capture ratio marks the plates too
crowded to measure (100X falls below the 25% cutoff).

The file is copied into the library at first start; add_tutorial_project adds a fresh copy at
any time. Its project settings carry {"tutorial": {"name": "crowding", "version": 1}}.
"""

from __future__ import annotations

from pathlib import Path

TUTORIAL_FILE = Path(__file__).parent / "tutorial" / "Crowding.qcproj"
TUTORIAL_PROJECT_NAME = "Crowding"


DISSERTATION_FOLDER_NAME = "Navarro Dissertation Projects"
EARLIER_FOLDER_NAMES = ("Dissertation",)          # the folder's earlier name, renamed when found
OLDER_VERSIONS_FOLDER_NAME = "Older versions"     # inside it: copies replaced by a newer bundled version
BUNDLE_VERSION = 2                                # raise when any bundled project changes: every computer then
                                                  # adds the projects it is missing or holds an older copy of
DISSERTATION_FILES = {"crowding": TUTORIAL_FILE,
                      "rescue": Path(__file__).parent / "dissertation" / "Rescue.qcproj",
                      "historical": Path(__file__).parent / "dissertation" / "Historical.qcproj",
                      "anbalagan": Path(__file__).parent / "dissertation" / "Anbalagan.qcproj"}
