"""Variables, levels and indices: the experimental design of a project.

Design variables (nominal, ordinal) are the factors an experiment crosses: one combination of their
levels is one index. A replicate variable is not a factor. Its levels number the interchangeable
plates (biological replicates) that share one combination, so it never multiplies the design: an
index holds one or more plates, and the replicate level only tells those plates apart. In the file
each plate is still one row of `indices` with one level of every variable (the replicate level
included), so every module that works plate by plate (regions, detection, colonies) is unchanged.
Replicate levels are numbered "1", "2", ... and added as plates need them.
"""

from __future__ import annotations

import math

from ...core.record import LevelLabel
from ..exports import safe_name
from ..packets import LevelInfo, VariableInfo, VariableSpec
from .project_file import ProjectFile


REPLICATE = "replicate"


def combination_count(connection) -> int:
    """Every combination of the design variables' levels (a replicate variable is not a factor)."""
    counts = [r[0] for r in connection.execute(
        "SELECT COUNT(*) FROM levels l JOIN variables v USING (variable_id) WHERE v.type != 'replicate' "
        "GROUP BY l.variable_id")]
    return math.prod(counts) if counts else 0


def index_count(connection) -> int:
    """Distinct combinations of design levels among plates that have a region (the indices in use)."""
    keys: dict[int, list] = {}
    for r in connection.execute(
            "SELECT il.index_id, il.variable_id, il.level_id FROM index_levels il "
            "JOIN variables v USING (variable_id) JOIN assignments a USING (index_id) "
            "WHERE v.type != 'replicate'"):
        keys.setdefault(r[0], []).append((r[1], r[2]))
    plates = connection.execute("SELECT COUNT(*) FROM assignments").fetchone()[0]
    if keys:
        return len({tuple(sorted(pairs)) for pairs in keys.values()})
    return 1 if plates else 0                      # a project with only a replicate variable


def sections_per_photo(plate: dict | None) -> int:
    plate = plate or {}
    if plate.get("type") == "rectangular":
        return max(1, int(plate.get("rows", 1)) * int(plate.get("cols", 1)))
    return max(1, int(plate.get("sections", 1)))


def expected_plates(connection, plate: dict | None) -> int:
    """Plates (regions) the project holds or will hold: every region placed, plus each photo not cropped yet
    counted at the plate setting's sections per photo."""
    placed = connection.execute("SELECT COUNT(*) FROM assignments").fetchone()[0]
    waiting = connection.execute(
        "SELECT COUNT(*) FROM photos p WHERE p.photo_id NOT IN (SELECT photo_id FROM photo_layouts) "
        "AND p.photo_id NOT IN (SELECT photo_id FROM assignments)").fetchone()[0]
    return placed + waiting * sections_per_photo(plate)


class Structure:
    def __init__(self, file: ProjectFile):
        self.file = file

    @property
    def db(self):
        return self.file.connection

    # ----- reading ----------------------------------------------------------
    def variables(self) -> tuple[VariableInfo, ...]:
        """Every variable with its levels, in variable order (two queries in all)."""
        levels: dict[int, list[LevelInfo]] = {}
        for row in self.db.execute("SELECT * FROM levels ORDER BY variable_id, sort_order"):
            levels.setdefault(row["variable_id"], []).append(
                LevelInfo(row["level_id"], row["value"], row["display_value"], row["sort_order"],
                          bool(row["is_control"])))
        return tuple(VariableInfo(row["variable_id"], row["name"], row["display_name"], row["type"],
                                  tuple(levels.get(row["variable_id"], ())))
                     for row in self.db.execute("SELECT * FROM variables ORDER BY position"))

    def all_labels(self) -> dict[int, tuple[LevelLabel, ...]]:
        """index id -> its level labels in variable order (one query for every index)."""
        labels: dict[int, list[LevelLabel]] = {}
        for r in self.db.execute(
                "SELECT il.index_id, v.display_name, v.type, l.value, l.display_value FROM index_levels il "
                "JOIN variables v USING (variable_id) JOIN levels l USING (level_id) "
                "ORDER BY il.index_id, v.position"):
            labels.setdefault(r["index_id"], []).append(
                LevelLabel(r["display_name"], r["type"], r["value"], r["display_value"]))
        return {index_id: tuple(values) for index_id, values in labels.items()}

    def labels(self, index_id: int) -> tuple[LevelLabel, ...]:
        rows = self.db.execute(
            "SELECT v.display_name, v.type, l.value, l.display_value FROM index_levels il "
            "JOIN variables v USING (variable_id) JOIN levels l USING (level_id) "
            "WHERE il.index_id = ? ORDER BY v.position", (index_id,))
        return tuple(LevelLabel(r["display_name"], r["type"], r["value"], r["display_value"]) for r in rows)

    def level_values(self, index_id: int) -> dict[str, str]:
        """{variable name: level value} for an index."""
        return {r["name"]: r["value"] for r in self.db.execute(
            "SELECT v.name, l.value FROM index_levels il JOIN variables v USING (variable_id) "
            "JOIN levels l USING (level_id) WHERE il.index_id = ?", (index_id,))}

    def all_level_values(self) -> dict[int, dict[str, str]]:
        values: dict[int, dict[str, str]] = {}
        for r in self.db.execute("SELECT il.index_id, v.name, l.value FROM index_levels il "
                                 "JOIN variables v USING (variable_id) JOIN levels l USING (level_id)"):
            values.setdefault(r["index_id"], {})[r["name"]] = r["value"]
        return values

    @staticmethod
    def name_from_labels(labels: tuple[LevelLabel, ...]) -> str:
        """File-safe name from level values, e.g. 'rif1_cdc132_Figure_2_v_25'."""
        return safe_name("_".join(label.value for label in labels))

    def index_name(self, index_id: int) -> str:
        return self.name_from_labels(self.labels(index_id))

    def variable_ids(self) -> dict[str, int]:
        return {row["name"]: row["variable_id"] for row in self.db.execute("SELECT name, variable_id FROM variables")}

    def level_ids(self) -> dict[str, dict[str, int]]:
        """{variable name: {level value: level id}}."""
        result: dict[str, dict[str, int]] = {}
        for r in self.db.execute("SELECT v.name, l.value, l.level_id FROM levels l JOIN variables v USING (variable_id)"):
            result.setdefault(r["name"], {})[r["value"]] = r["level_id"]
        return result

    def find_index(self, level_ids: dict[int, int]) -> int | None:
        """The index with exactly these {variable id: level id}, or None."""
        stored: dict[int, dict[int, int]] = {}
        for r in self.db.execute("SELECT index_id, variable_id, level_id FROM index_levels"):
            stored.setdefault(r["index_id"], {})[r["variable_id"]] = r["level_id"]
        return next((index_id for index_id, levels in stored.items() if levels == level_ids), None)

    def combination_count(self) -> int:
        """Every combination of design-variable levels (replicates excluded)."""
        return combination_count(self.db)

    def index_count(self) -> int:
        """Indices that hold at least one plate with a region: distinct combinations of design levels."""
        return index_count(self.db)

    def replicate_variable(self):
        """The replicate variable (VariableInfo), or None."""
        return next((v for v in self.variables() if v.type == REPLICATE), None)

    def design_key(self, index_id: int) -> tuple:
        """(variable id, level id) of every design variable of a plate, sorted: the index it belongs to."""
        return tuple(sorted((r["variable_id"], r["level_id"]) for r in self.db.execute(
            "SELECT il.variable_id, il.level_id FROM index_levels il JOIN variables v USING (variable_id) "
            "WHERE il.index_id = ? AND v.type != 'replicate'", (index_id,))))

    def plates_by_design(self) -> dict[tuple, dict[int, int]]:
        """design key -> {replicate level id: plate (index id)} for every stored plate."""
        design: dict[int, list] = {}
        replicate: dict[int, int] = {}
        for r in self.db.execute("SELECT il.index_id, il.variable_id, il.level_id, v.type FROM index_levels il "
                                 "JOIN variables v USING (variable_id)"):
            if r["type"] == REPLICATE:
                replicate[r["index_id"]] = r["level_id"]
            else:
                design.setdefault(r["index_id"], []).append((r["variable_id"], r["level_id"]))
        found: dict[tuple, dict[int, int]] = {}
        for index_id, pairs in design.items():
            found.setdefault(tuple(sorted(pairs)), {})[replicate.get(index_id, 0)] = index_id
        return found

    # ----- writing (inside a transaction) -------------------------------------
    def insert_variables(self, variables: list[VariableSpec]) -> None:
        self.file.require_transaction()
        for position, spec in enumerate(variables):
            cursor = self.db.execute(
                "INSERT INTO variables (name, display_name, type, position) VALUES (?, ?, ?, ?)",
                (spec.name, spec.display_name or spec.name, spec.type, position))
            self.db.executemany(
                "INSERT INTO levels (variable_id, value, display_value, sort_order, is_control) "
                "VALUES (?, ?, ?, ?, ?)",
                [(cursor.lastrowid, level.value, level.display or level.value, order, int(level.is_control))
                 for order, level in enumerate(spec.levels)])

    def add_replicate_level(self, variable_id: int) -> int:
        """A new replicate level, numbered after the highest number in use ("1", "2", ...)."""
        self.file.require_transaction()
        rows = self.db.execute("SELECT value, sort_order FROM levels WHERE variable_id = ?", (variable_id,)).fetchall()
        numbers = [int(r["value"]) for r in rows if r["value"].isdigit()]
        value = str(max(numbers, default=len(rows)) + 1)
        while any(r["value"] == value for r in rows):
            value = str(int(value) + 1)
        order = max((r["sort_order"] for r in rows), default=-1) + 1
        return self.db.execute("INSERT INTO levels (variable_id, value, display_value, sort_order, is_control) "
                               "VALUES (?, ?, ?, ?, 0)", (variable_id, value, value, order)).lastrowid

    def insert_index(self, level_ids: dict[int, int]) -> int:
        self.file.require_transaction()
        index_id = self.db.execute("INSERT INTO indices DEFAULT VALUES").lastrowid
        self.db.executemany("INSERT INTO index_levels (index_id, variable_id, level_id) VALUES (?, ?, ?)",
                            [(index_id, variable_id, level_id) for variable_id, level_id in level_ids.items()])
        return index_id
