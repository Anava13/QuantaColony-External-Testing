"""Private helpers behind the DataManager. Nothing outside the data package imports them.

Each helper owns one area of the project file:

    project_file   the open SQLite file: connect, check, upgrade, transactions, settings
    structure      variables, levels, indices and index names
    photos         plate photos, their layouts and the crop region of every index
    detections     committed detection settings, colonies and the edit log
    derived_cache  fingerprinted derived results
    catalog        projects that are not open: list, copy in or out, backups

Two rules keep the DataManager the single source of truth:

1. Only the DataManager creates helpers. It hands each one the open ProjectFile,
   so there is one connection and every save starts in one place.
2. Helpers never call each other. An action that touches several areas (for
   example storing a photo layout, which writes indices and regions and flags
   detections for review) is a DataManager method that calls each helper inside
   one transaction.
"""
