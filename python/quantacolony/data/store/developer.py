"""Developer mode's tables: benchmark records written next to the results they describe.

The tables exist only in a project opened at least once in developer mode
(`quantacolony --developer`). Standard mode never reads or writes them, and they sit
outside SCHEMA_VERSION and MIGRATIONS: a project with them opens in any QuantaColony
that opens the rest of the file. The project setting 'developer' marks them and holds
their own version, BENCHMARK_VERSION.

Colony coordinates are photo pixels, like the colonies table. Event data and settings are
JSON. See core/benchmark.py for what each event holds.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import numpy as np

from ...core.benchmark import BENCHMARK_VERSION, CropBenchmark, DetectionBenchmark, Event
from ...core.detection import detection_version
from .project_file import ProjectFile, now

DEV_TABLES = """
-- The computer and software of each developer-mode opening of the project
CREATE TABLE IF NOT EXISTS dev_environment (
    environment_id INTEGER PRIMARY KEY,
    recorded_at    TEXT NOT NULL,
    details        TEXT NOT NULL        -- JSON: software version, OS, CPU, cores, RAM
);

-- One row per finished image session in Colony Detection (its revision is the one it produced)
CREATE TABLE IF NOT EXISTS dev_detection_sessions (
    session_id        INTEGER PRIMARY KEY,
    index_id          INTEGER NOT NULL REFERENCES indices (index_id) ON DELETE CASCADE,
    revision          INTEGER NOT NULL,
    environment_id    INTEGER REFERENCES dev_environment (environment_id),
    opened_at         TEXT NOT NULL,
    finished_at       TEXT NOT NULL,
    duration_ms       INTEGER NOT NULL,
    start_source      TEXT NOT NULL,     -- 'saved', 'last image', 'estimate', 'defaults'
    start_params      TEXT NOT NULL,     -- JSON
    final_params      TEXT NOT NULL,     -- JSON
    image_rows        INTEGER NOT NULL,
    image_cols        INTEGER NOT NULL,
    region_px         INTEGER NOT NULL,
    px_per_mm         REAL NOT NULL,
    crop_x0           INTEGER NOT NULL,  -- crop origin in the photo: crop pixel + origin = photo pixel
    crop_y0           INTEGER NOT NULL
);

-- Automatic detection at settings estimated from the image, starting from the project defaults
CREATE TABLE IF NOT EXISTS dev_initial_detection (
    session_id        INTEGER PRIMARY KEY REFERENCES dev_detection_sessions (session_id) ON DELETE CASCADE,
    params            TEXT NOT NULL,     -- JSON
    extent            TEXT NOT NULL,     -- 'full', 'level only', 'none' (no estimate: the defaults were used)
    compute_ms        REAL NOT NULL,
    detection_version INTEGER NOT NULL,
    colony_count      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dev_initial_colonies (
    session_id   INTEGER NOT NULL REFERENCES dev_detection_sessions (session_id) ON DELETE CASCADE,
    colony_row   INTEGER NOT NULL,
    x            REAL NOT NULL,
    y            REAL NOT NULL,
    radius_px    REAL NOT NULL,
    eccentricity REAL,
    kind         TEXT NOT NULL,
    PRIMARY KEY (session_id, colony_row)
);

CREATE TABLE IF NOT EXISTS dev_detection_events (
    session_id INTEGER NOT NULL REFERENCES dev_detection_sessions (session_id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    t_ms       INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    data       TEXT NOT NULL,            -- JSON
    PRIMARY KEY (session_id, seq)
);

-- One row per photo saved in Cropping
CREATE TABLE IF NOT EXISTS dev_crop_sessions (
    crop_session_id INTEGER PRIMARY KEY,
    photo_id        INTEGER NOT NULL REFERENCES photos (photo_id) ON DELETE CASCADE,
    environment_id  INTEGER REFERENCES dev_environment (environment_id),
    opened_at       TEXT NOT NULL,
    done_at         TEXT NOT NULL,
    duration_ms     INTEGER NOT NULL,
    visits          INTEGER NOT NULL,
    section_count   INTEGER NOT NULL,
    layout_kind     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dev_crop_events (
    crop_session_id INTEGER NOT NULL REFERENCES dev_crop_sessions (crop_session_id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    t_ms            INTEGER NOT NULL,
    kind            TEXT NOT NULL,
    data            TEXT NOT NULL,
    PRIMARY KEY (crop_session_id, seq)
)
"""


def _json(value) -> str:
    return json.dumps(value, default=_plain)


def _plain(value):
    """numpy numbers and arrays in event data, as JSON values."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


class Developer:
    def __init__(self, file: ProjectFile):
        self.file = file

    @property
    def db(self):
        return self.file.connection

    # ----- setup ------------------------------------------------------------------------
    def present(self) -> bool:
        """The project has the developer tables."""
        return self.db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dev_environment'"
                               ).fetchone() is not None

    def ensure(self, details: dict) -> int:
        """Create the tables when missing, mark the project, and record this opening's computer.
        Returns the environment id the records of this opening refer to."""
        self.file.require_transaction()
        for statement in DEV_TABLES.split(";"):
            if statement.strip():
                self.db.execute(statement)
        marker = self.file.setting("developer") or {}
        if marker.get("benchmark") != BENCHMARK_VERSION:
            self.file.set_setting("developer", {"benchmark": BENCHMARK_VERSION, "since": marker.get("since", now())})
        cursor = self.db.execute("INSERT INTO dev_environment (recorded_at, details) VALUES (?, ?)",
                                 (details.get("recorded_at", now()), _json(details)))
        return int(cursor.lastrowid)

    # ----- writing (inside the transaction of the result) ---------------------------------
    def write_detection(self, bench: DetectionBenchmark, revision: int, origin: tuple[int, int],
                        environment_id: int | None) -> int:
        """Store one image session. origin: the crop's (x0, y0) in the photo. Returns its session id."""
        self.file.require_transaction()
        x0, y0 = int(origin[0]), int(origin[1])
        cursor = self.db.execute(
            "INSERT INTO dev_detection_sessions (index_id, revision, environment_id, opened_at, finished_at, "
            "duration_ms, start_source, start_params, final_params, image_rows, image_cols, region_px, px_per_mm, "
            "crop_x0, crop_y0) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (bench.index_id, revision, environment_id, bench.opened_at, bench.finished_at, bench.duration_ms,
             bench.start_source, _json(asdict(bench.start_params)), _json(asdict(bench.final_params)),
             bench.image_shape[0], bench.image_shape[1], bench.region_px, bench.px_per_mm, x0, y0))
        session_id = int(cursor.lastrowid)
        self._write_events("dev_detection_events", "session_id", session_id, bench.events)
        initial = bench.initial
        if initial is not None:
            colonies = initial.colonies.translated(x0, y0)
            self.db.execute("INSERT INTO dev_initial_detection (session_id, params, extent, compute_ms, "
                            "detection_version, colony_count) VALUES (?, ?, ?, ?, ?, ?)",
                            (session_id, _json(asdict(initial.params)), initial.extent, float(initial.compute_ms),
                             detection_version(initial.params), len(colonies)))
            self.db.executemany(
                "INSERT INTO dev_initial_colonies (session_id, colony_row, x, y, radius_px, eccentricity, kind) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(session_id, i, float(colonies.centers[i, 0]), float(colonies.centers[i, 1]),
                  float(colonies.radii[i]),
                  None if np.isnan(colonies.eccentricities[i]) else float(colonies.eccentricities[i]),
                  colonies.kinds[i]) for i in range(len(colonies))])
        return session_id

    def write_crop(self, bench: CropBenchmark, environment_id: int | None) -> int:
        self.file.require_transaction()
        cursor = self.db.execute(
            "INSERT INTO dev_crop_sessions (photo_id, environment_id, opened_at, done_at, duration_ms, visits, "
            "section_count, layout_kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (bench.photo_id, environment_id, bench.opened_at, bench.done_at, bench.duration_ms, bench.visits,
             bench.section_count, bench.layout_kind))
        crop_session_id = int(cursor.lastrowid)
        self._write_events("dev_crop_events", "crop_session_id", crop_session_id, bench.events)
        return crop_session_id

    def _write_events(self, table: str, key: str, session_id: int, events: tuple[Event, ...]) -> None:
        self.db.executemany(f"INSERT INTO {table} ({key}, seq, t_ms, kind, data) VALUES (?, ?, ?, ?, ?)",
                            [(session_id, e.seq, e.t_ms, e.kind, _json(e.data)) for e in events])

    # ----- reading (analysis) ---------------------------------------------------------------
    def detection_sessions(self) -> list[dict]:
        if not self.present():
            return []
        rows = self.db.execute("SELECT * FROM dev_detection_sessions ORDER BY session_id").fetchall()
        return [{**dict(r), "start_params": json.loads(r["start_params"]),
                 "final_params": json.loads(r["final_params"])} for r in rows]

    def detection_events(self, session_id: int) -> list[dict]:
        return self._events("dev_detection_events", "session_id", session_id)

    def initial_detection(self, session_id: int) -> dict | None:
        """The initial detection of a session with its colonies (photo pixels), or None."""
        row = self.db.execute("SELECT * FROM dev_initial_detection WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        colonies = self.db.execute("SELECT x, y, radius_px, eccentricity, kind FROM dev_initial_colonies "
                                   "WHERE session_id = ? ORDER BY colony_row", (session_id,)).fetchall()
        return {**dict(row), "params": json.loads(row["params"]),
                "centers": np.array([(c["x"], c["y"]) for c in colonies], dtype=float).reshape(-1, 2),
                "radii": np.array([c["radius_px"] for c in colonies], dtype=float),
                "kinds": [c["kind"] for c in colonies]}

    def crop_sessions(self) -> list[dict]:
        if not self.present():
            return []
        return [dict(r) for r in self.db.execute("SELECT * FROM dev_crop_sessions ORDER BY crop_session_id")]

    def crop_events(self, crop_session_id: int) -> list[dict]:
        return self._events("dev_crop_events", "crop_session_id", crop_session_id)

    def environments(self) -> list[dict]:
        if not self.present():
            return []
        return [{"environment_id": r["environment_id"], **json.loads(r["details"])}
                for r in self.db.execute("SELECT * FROM dev_environment ORDER BY environment_id")]

    def _events(self, table: str, key: str, session_id: int) -> list[dict]:
        return [{"seq": r["seq"], "t_ms": r["t_ms"], "kind": r["kind"], "data": json.loads(r["data"])}
                for r in self.db.execute(f"SELECT seq, t_ms, kind, data FROM {table} WHERE {key} = ? ORDER BY seq",
                                         (session_id,))]
