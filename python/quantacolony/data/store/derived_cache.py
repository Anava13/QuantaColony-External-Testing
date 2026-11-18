"""Derived results kept for speed. A cached value is used only while its fingerprint matches."""

from __future__ import annotations

import json

from .project_file import ProjectFile, now


class DerivedCache:
    def __init__(self, file: ProjectFile):
        self.file = file

    def lookup(self, name: str, index_id: int, fingerprint: str) -> list | None:
        row = self.file.connection.execute("SELECT fingerprint, value FROM derived_cache WHERE name = ? "
                                           "AND index_id = ?", (name, index_id)).fetchone()
        if row is not None and row["fingerprint"] == fingerprint:
            return json.loads(row["value"])
        return None

    def store(self, name: str, index_id: int, fingerprint: str, rows: list) -> None:
        """Keep a calculation for reuse. This save does not count as a change to the project."""
        with self.file.transaction(counts_as_change=False) as db:
            db.execute("INSERT OR REPLACE INTO derived_cache (name, index_id, fingerprint, value, computed_at) "
                       "VALUES (?, ?, ?, ?, ?)", (name, index_id, fingerprint, json.dumps(rows), now()))

    def clear(self, index_id: int) -> None:
        self.file.require_transaction()
        self.file.connection.execute("DELETE FROM derived_cache WHERE index_id = ?", (index_id,))
