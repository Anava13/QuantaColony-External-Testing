"""The open project file: connection, checks on opening, file-format upgrades, transactions, settings.

Writes happen inside SQLite transactions: a crash or power cut during a save
leaves the file at its last committed state. Files stay single files at rest
(rollback journal, not write-ahead log).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..schema import MIGRATIONS, SCHEMA_VERSION, TABLES


class FileProblem(Exception):
    """The file cannot be used; the message is meant for the user."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def copy_database(source: sqlite3.Connection, target: Path) -> None:
    """Consistent copy of an SQLite database (backup API), written under a temporary name, then renamed."""
    temporary = target.with_name(target.name + ".tmp")
    temporary.unlink(missing_ok=True)
    destination = sqlite3.connect(str(temporary))
    try:
        source.backup(destination)
        destination.execute("PRAGMA journal_mode = DELETE")
    finally:
        destination.close()
    from ..exports import _finish
    _finish(temporary, target)                   # retries while an antivirus or indexer holds the new file


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), isolation_level=None)   # transactions are explicit
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = DELETE")             # one file at rest
    connection.execute("PRAGMA synchronous = FULL")                # committed means on disk
    return connection


def _column_exists(db, statement: str) -> bool:
    """True for an 'ALTER TABLE t ADD COLUMN c ...' whose column is already there (a file that already
    has it is brought up to date without failing)."""
    words = statement.split()
    if len(words) < 6 or [w.upper() for w in (words[0], words[1], words[3], words[4])] != ["ALTER", "TABLE", "ADD",
                                                                                            "COLUMN"]:
        return False
    return any(row[1] == words[5] for row in db.execute(f"PRAGMA table_info({words[2]})"))


class ProjectFile:
    """One open project file. `changed` is True once a save changed the user's project."""

    def __init__(self, path: Path, connection: sqlite3.Connection):
        self.path = path
        self.connection = connection
        self.changed = False
        self.upgraded_from: int | None = None      # schema version before opening upgraded the file
        self._depth = 0

    # ----- opening ----------------------------------------------------------
    @classmethod
    def create(cls, path: Path, settings: dict) -> None:
        """Write a new, empty project file with its settings, then close it."""
        connection = connect(path)
        try:
            connection.executescript(TABLES)
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany("INSERT INTO project (key, value) VALUES (?, ?)",
                                   [(key, json.dumps(value)) for key, value in settings.items()])
            connection.execute("COMMIT")
        finally:
            connection.close()

    @classmethod
    def open(cls, path: Path) -> "ProjectFile":
        """Open, check and (for older files) upgrade a project file."""
        connection = None
        try:
            connection = connect(path)
            check = connection.execute("PRAGMA quick_check").fetchone()[0]
            row = connection.execute("SELECT value FROM project WHERE key = 'schema_version'").fetchone()
            version = json.loads(row[0]) if row else None
        except sqlite3.DatabaseError as error:
            if connection is not None:
                connection.close()
            raise FileProblem(f"{path.name} is not a QuantaColony project ({error})") from error
        if check != "ok":
            connection.close()
            raise FileProblem(f"The project file is damaged ({check}). Restore a backup.")
        if version is None or version > SCHEMA_VERSION:
            connection.close()
            raise FileProblem(f"This project needs a newer QuantaColony (file schema {version}, "
                              f"this program reads up to {SCHEMA_VERSION})")
        opened = cls(path, connection)
        if version < SCHEMA_VERSION:                     # bring an older file up to date, in one transaction
            try:
                with opened.transaction() as db:
                    for target in range(version + 1, SCHEMA_VERSION + 1):
                        for statement in MIGRATIONS[target].split(";"):
                            if statement.strip() and not _column_exists(db, statement):
                                db.execute(statement)
                    db.execute("UPDATE project SET value = ? WHERE key = 'schema_version'",
                               (json.dumps(SCHEMA_VERSION),))
            except Exception:
                connection.close()
                raise
            opened.upgraded_from = version
        return opened

    def close(self) -> None:
        self.connection.close()

    # ----- transactions -----------------------------------------------------
    @contextmanager
    def transaction(self, counts_as_change: bool = True):
        """All writes inside commit together or not at all. A nested call joins the outer transaction.

        counts_as_change=False is for saves that only keep a calculation for reuse (the derived
        cache): they do not make the closing backup, so a session spent looking at results never
        pushes a backup of real work out of the five that are kept."""
        if self._depth:
            self._depth += 1
            try:
                yield self.connection
            finally:
                self._depth -= 1
            if counts_as_change:
                self.changed = True
            return
        self.connection.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield self.connection
        except BaseException:
            self._depth = 0
            self.connection.execute("ROLLBACK")
            raise
        self._depth = 0
        self.connection.execute("COMMIT")
        if counts_as_change:
            self.changed = True

    def require_transaction(self) -> None:
        if not self._depth:
            raise RuntimeError("This write must run inside ProjectFile.transaction()")

    # ----- settings ---------------------------------------------------------
    def setting(self, key: str):
        row = self.connection.execute("SELECT value FROM project WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def set_setting(self, key: str, value) -> None:
        self.require_transaction()
        self.connection.execute("INSERT OR REPLACE INTO project (key, value) VALUES (?, ?)",
                                (key, json.dumps(value)))

    def copy_to(self, target: Path) -> None:
        copy_database(self.connection, target)
