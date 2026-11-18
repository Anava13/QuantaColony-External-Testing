"""Tables of a QuantaColony project file (.qcproj, an SQLite database).

The file stores raw photos and user decisions. Everything else (crops,
masks, measurements, normalizations, exports) is computed from them on
request, so no stored value can disagree with another.

Colony coordinates are photo pixels (0-based, x = column, y = row), so a
colony keeps its place on the plate when a crop region changes.
"""

SCHEMA_VERSION = 6

TABLES = """
-- Project settings as key / JSON value pairs: name, schema_version, created_at,
-- software_version, detection_defaults, plate, last_detection_params (settings of the
-- last finished image, the starting point for the next new one)
CREATE TABLE project (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Variables (nominal, ordinal, replicate, ...). Rows, not columns, so a project
-- can gain a variable without a change to the file structure.
CREATE TABLE variables (
    variable_id  INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    type         TEXT NOT NULL CHECK (type IN ('nominal', 'ordinal', 'replicate')),
    position     INTEGER NOT NULL
);

CREATE TABLE levels (
    level_id      INTEGER PRIMARY KEY,
    variable_id   INTEGER NOT NULL REFERENCES variables (variable_id) ON DELETE CASCADE,
    value         TEXT NOT NULL,
    display_value TEXT NOT NULL,
    sort_order    INTEGER NOT NULL,
    is_control    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (variable_id, value)
);

-- A multivariable index: one level of every variable; gets one dedicated image
CREATE TABLE indices (
    index_id INTEGER PRIMARY KEY
);

CREATE TABLE index_levels (
    index_id    INTEGER NOT NULL REFERENCES indices (index_id) ON DELETE CASCADE,
    variable_id INTEGER NOT NULL REFERENCES variables (variable_id),
    level_id    INTEGER NOT NULL REFERENCES levels (level_id),
    PRIMARY KEY (index_id, variable_id)
);

-- Plate photos exactly as uploaded, each with its own scale (core.scale): the evidence set in
-- Cropping (scale, JSON) and the px/mm computed from it (scale_px_per_mm, NULL for a photo without
-- a scale, whose sizes stay in pixels). px_per_mm and scaling_method are kept for files read by
-- version 5 and earlier; they mirror the scale (px_per_mm is 1.0 when there is none).
CREATE TABLE photos (
    photo_id        INTEGER PRIMARY KEY,
    file_name       TEXT NOT NULL,
    content         BLOB NOT NULL,
    sha256          TEXT NOT NULL,
    px_per_mm       REAL NOT NULL,
    scaling_method  TEXT NOT NULL DEFAULT '',
    added_at        TEXT NOT NULL,
    width           INTEGER NOT NULL DEFAULT 0,   -- pixels, read once when the photo is added
    height          INTEGER NOT NULL DEFAULT 0,
    thumbnail       BLOB,                         -- small PNG for the project list
    scale           TEXT NOT NULL DEFAULT '{}',
    scale_px_per_mm REAL
);

-- Cropping's decision: which region of which photo belongs to an index
CREATE TABLE assignments (
    index_id    INTEGER PRIMARY KEY REFERENCES indices (index_id) ON DELETE CASCADE,
    photo_id    INTEGER NOT NULL REFERENCES photos (photo_id),
    region_kind TEXT NOT NULL CHECK (region_kind IN ('rectangle', 'sector', 'polygon', 'freehand')),
    geometry    TEXT NOT NULL,      -- JSON parameters of the shape, for exact editing
    outline     BLOB NOT NULL,      -- (M, 2) polygon in photo pixels, computed from geometry
    updated_at  TEXT NOT NULL
);

-- Cropping's plate layout for each photo: dish circle and cuts, or a rotated grid,
-- plus the levels of every section (null for an unused section). Assignments are
-- written from it in the same transaction.
CREATE TABLE photo_layouts (
    photo_id   INTEGER PRIMARY KEY REFERENCES photos (photo_id) ON DELETE CASCADE,
    layout     TEXT NOT NULL,       -- JSON, see core.cropping
    sections   TEXT NOT NULL,       -- JSON list: {variable name: level value} or null per section
    updated_at TEXT NOT NULL
);

-- Colony Detection's decision for an index: one row per committed index
CREATE TABLE detection (
    index_id          INTEGER PRIMARY KEY REFERENCES indices (index_id) ON DELETE CASCADE,
    revision          INTEGER NOT NULL,
    committed_at      TEXT NOT NULL,
    level             REAL NOT NULL,
    sense             REAL NOT NULL,
    rmin              REAL NOT NULL,
    rmax              REAL NOT NULL,
    maxpix            REAL NOT NULL,
    detection_type    TEXT NOT NULL,
    split             INTEGER NOT NULL DEFAULT 0,
    split_elongation  REAL NOT NULL DEFAULT 0.6,
    split_pinch       REAL NOT NULL DEFAULT 0.15,
    detection_version INTEGER NOT NULL,
    software_version  TEXT NOT NULL,
    needs_review      INTEGER NOT NULL DEFAULT 0,
    review_reason     TEXT NOT NULL DEFAULT ''
);

-- Every colony of the final result and of the baseline. Additions are in_final
-- and not in_baseline; deletions are the reverse.
CREATE TABLE colonies (
    index_id     INTEGER NOT NULL REFERENCES indices (index_id) ON DELETE CASCADE,
    colony_id    INTEGER NOT NULL,
    x            REAL NOT NULL,
    y            REAL NOT NULL,
    radius_px    REAL NOT NULL,
    eccentricity REAL,
    kind         TEXT NOT NULL,
    boundary     BLOB,               -- (M, 2) outline in photo pixels, NULL for circles
    in_baseline  INTEGER NOT NULL,
    in_final     INTEGER NOT NULL,
    PRIMARY KEY (index_id, colony_id)
);

-- Confirmed edits, in order, for every revision
CREATE TABLE edit_log (
    index_id   INTEGER NOT NULL REFERENCES indices (index_id) ON DELETE CASCADE,
    revision   INTEGER NOT NULL,
    step       INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    count      INTEGER NOT NULL,
    colony_ids TEXT NOT NULL,        -- JSON list
    params     TEXT NOT NULL,        -- JSON detection parameters after the edit
    PRIMARY KEY (index_id, revision, step)
);


-- Per-plate annotations: values measured or noted on a plate, never indexed on.
-- Declarations name each annotation; values attach to a plate (index) or to a
-- photo (every plate of that photo inherits it unless the plate has its own).
CREATE TABLE annotations (
    annotation_id INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('number', 'choice', 'text')),
    unit          TEXT NOT NULL DEFAULT '',
    choices       TEXT NOT NULL DEFAULT '[]',  -- JSON list of values a 'choice' annotation offers
    position      INTEGER NOT NULL
);

CREATE TABLE annotation_values (
    index_id      INTEGER NOT NULL REFERENCES indices (index_id) ON DELETE CASCADE,
    annotation_id INTEGER NOT NULL REFERENCES annotations (annotation_id) ON DELETE CASCADE,
    value         TEXT NOT NULL,
    PRIMARY KEY (index_id, annotation_id)
);

CREATE TABLE photo_annotation_values (
    photo_id      INTEGER NOT NULL REFERENCES photos (photo_id) ON DELETE CASCADE,
    annotation_id INTEGER NOT NULL REFERENCES annotations (annotation_id) ON DELETE CASCADE,
    value         TEXT NOT NULL,
    PRIMARY KEY (photo_id, annotation_id)
);

-- Derived results kept for speed. Each row is used only while its fingerprint
-- matches the current inputs and algorithm version.
CREATE TABLE derived_cache (
    name        TEXT NOT NULL,
    index_id    INTEGER NOT NULL REFERENCES indices (index_id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL,
    value       TEXT NOT NULL,       -- JSON table
    computed_at TEXT NOT NULL,
    PRIMARY KEY (name, index_id)
);
"""

# Changes for files written by older versions, applied when such a file is opened
MIGRATIONS = {
    2: """
CREATE TABLE IF NOT EXISTS photo_layouts (
    photo_id   INTEGER PRIMARY KEY REFERENCES photos (photo_id) ON DELETE CASCADE,
    layout     TEXT NOT NULL,
    sections   TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
""",
    3: """
ALTER TABLE photos ADD COLUMN width INTEGER NOT NULL DEFAULT 0;
ALTER TABLE photos ADD COLUMN height INTEGER NOT NULL DEFAULT 0;
ALTER TABLE photos ADD COLUMN thumbnail BLOB;
""",
    4: """
ALTER TABLE detection ADD COLUMN split INTEGER NOT NULL DEFAULT 0;
ALTER TABLE detection ADD COLUMN split_elongation REAL NOT NULL DEFAULT 0.6;
ALTER TABLE detection ADD COLUMN split_pinch REAL NOT NULL DEFAULT 0.15;
""",
    5: """
CREATE TABLE IF NOT EXISTS annotations (
    annotation_id INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('number', 'choice', 'text')),
    unit          TEXT NOT NULL DEFAULT '',
    choices       TEXT NOT NULL DEFAULT '[]',
    position      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS annotation_values (
    index_id      INTEGER NOT NULL REFERENCES indices (index_id) ON DELETE CASCADE,
    annotation_id INTEGER NOT NULL REFERENCES annotations (annotation_id) ON DELETE CASCADE,
    value         TEXT NOT NULL,
    PRIMARY KEY (index_id, annotation_id)
);
CREATE TABLE IF NOT EXISTS photo_annotation_values (
    photo_id      INTEGER NOT NULL REFERENCES photos (photo_id) ON DELETE CASCADE,
    annotation_id INTEGER NOT NULL REFERENCES annotations (annotation_id) ON DELETE CASCADE,
    value         TEXT NOT NULL,
    PRIMARY KEY (photo_id, annotation_id)
);
""",
    # Per-photo scale. Version 5 stored 1.0 for a photo without a scale; those become NULL. The
    # evidence of the scales kept is filled in by DataManager.open_project (it needs the plate setting).
    6: """
ALTER TABLE photos ADD COLUMN scale TEXT NOT NULL DEFAULT '{}';
ALTER TABLE photos ADD COLUMN scale_px_per_mm REAL;
UPDATE photos SET scale_px_per_mm = px_per_mm WHERE px_per_mm != 1.0 AND scale = '{}'
""",
}
