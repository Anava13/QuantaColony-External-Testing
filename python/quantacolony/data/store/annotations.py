"""Per-plate annotations: declarations and values.

An annotation is a fact recorded about a plate without entering the design:
the variables index plates, annotations only travel with them. Values attach
to a plate (index) or to a photo; a plate without its own value inherits its
photo's. A 'number' annotation is parsed as a float when read as a measure;
'choice' offers a fixed list; 'text' is a free note.
"""

from __future__ import annotations

import json

from .project_file import ProjectFile

KINDS = ("number", "choice", "text")


class AnnotationProblem(Exception):
    """The annotation cannot be stored; the message is meant for the user."""


class Annotations:
    def __init__(self, file: ProjectFile):
        self.file = file

    @property
    def db(self):
        return self.file.connection

    # ----- declarations -----------------------------------------------------
    def specs(self) -> list[dict]:
        """Every declared annotation, in position order."""
        return [{"annotation_id": r["annotation_id"], "name": r["name"], "display_name": r["display_name"],
                 "kind": r["kind"], "unit": r["unit"], "choices": tuple(json.loads(r["choices"])),
                 "position": r["position"]}
                for r in self.db.execute("SELECT * FROM annotations ORDER BY position")]

    def ids(self) -> dict[str, int]:
        return {r["name"]: r["annotation_id"] for r in self.db.execute("SELECT annotation_id, name FROM annotations")}

    def declare(self, name: str, kind: str, display_name: str = "", unit: str = "",
                choices: tuple[str, ...] = ()) -> int:
        """Add one annotation; returns its id. A name already declared is an error."""
        self.file.require_transaction()
        name = name.strip()
        if not name:
            raise AnnotationProblem("An annotation needs a name")
        if kind not in KINDS:
            raise AnnotationProblem(f"Annotation kind must be one of {', '.join(KINDS)}")
        if kind == "choice" and not choices:
            raise AnnotationProblem(f"{name}: a choice annotation needs its choices")
        if self.db.execute("SELECT 1 FROM annotations WHERE name = ?", (name,)).fetchone():
            raise AnnotationProblem(f"The project already has an annotation named {name}")
        position = self.db.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM annotations").fetchone()[0]
        cursor = self.db.execute(
            "INSERT INTO annotations (name, display_name, kind, unit, choices, position) VALUES (?, ?, ?, ?, ?, ?)",
            (name, display_name or name, kind, unit, json.dumps(list(choices)), position))
        return cursor.lastrowid

    def remove(self, name: str) -> None:
        """Remove an annotation and every value of it."""
        self.file.require_transaction()
        self.db.execute("DELETE FROM annotations WHERE name = ?", (name,))

    # ----- values -----------------------------------------------------------
    def _checked(self, name: str, value) -> tuple[int, str]:
        spec = next((s for s in self.specs() if s["name"] == name), None)
        if spec is None:
            raise AnnotationProblem(f"The project has no annotation named {name}")
        text = str(value).strip()
        if text and spec["kind"] == "number":
            try:
                float(text)
            except ValueError:
                raise AnnotationProblem(f"{spec['display_name']} takes a number; {text!r} is not one") from None
        if text and spec["kind"] == "choice" and text not in spec["choices"]:
            raise AnnotationProblem(f"{spec['display_name']} takes one of {', '.join(spec['choices'])}, "
                                    f"not {text!r}")
        return spec["annotation_id"], text

    def set_plate_value(self, index_id: int, name: str, value) -> None:
        """Set one plate's value ('' or None removes it, so the plate inherits its photo's)."""
        self.file.require_transaction()
        annotation_id, text = self._checked(name, "" if value is None else value)
        if text == "":
            self.db.execute("DELETE FROM annotation_values WHERE index_id = ? AND annotation_id = ?",
                            (index_id, annotation_id))
        else:
            self.db.execute("INSERT OR REPLACE INTO annotation_values (index_id, annotation_id, value) "
                            "VALUES (?, ?, ?)", (index_id, annotation_id, text))

    def set_photo_value(self, photo_id: int, name: str, value) -> None:
        """Set one photo's value, inherited by its plates ('' or None removes it)."""
        self.file.require_transaction()
        annotation_id, text = self._checked(name, "" if value is None else value)
        if text == "":
            self.db.execute("DELETE FROM photo_annotation_values WHERE photo_id = ? AND annotation_id = ?",
                            (photo_id, annotation_id))
        else:
            self.db.execute("INSERT OR REPLACE INTO photo_annotation_values (photo_id, annotation_id, value) "
                            "VALUES (?, ?, ?)", (photo_id, annotation_id, text))

    def plate_values(self) -> dict[int, dict[str, str]]:
        """Every plate's resolved values: {index_id: {annotation name: value}}.

        A plate's own value wins; a plate without one inherits its photo's. Plates
        with no region yet appear only once they have a photo."""
        names = {r["annotation_id"]: r["name"] for r in self.db.execute("SELECT annotation_id, name FROM annotations")}
        values: dict[int, dict[str, str]] = {}
        for r in self.db.execute("SELECT a.index_id, p.annotation_id, p.value FROM photo_annotation_values p "
                                 "JOIN assignments a USING (photo_id)"):
            values.setdefault(r["index_id"], {})[names[r["annotation_id"]]] = r["value"]
        for r in self.db.execute("SELECT index_id, annotation_id, value FROM annotation_values"):
            values.setdefault(r["index_id"], {})[names[r["annotation_id"]]] = r["value"]
        return values

    def photo_values(self) -> dict[int, dict[str, str]]:
        """{photo_id: {annotation name: value}} as stored at the photo level."""
        names = {r["annotation_id"]: r["name"] for r in self.db.execute("SELECT annotation_id, name FROM annotations")}
        values: dict[int, dict[str, str]] = {}
        for r in self.db.execute("SELECT photo_id, annotation_id, value FROM photo_annotation_values"):
            values.setdefault(r["photo_id"], {})[names[r["annotation_id"]]] = r["value"]
        return values

    def own_plate_values(self) -> dict[int, dict[str, str]]:
        """{index_id: {annotation name: value}} set on the plate itself (no photo inheritance)."""
        names = {r["annotation_id"]: r["name"] for r in self.db.execute("SELECT annotation_id, name FROM annotations")}
        values: dict[int, dict[str, str]] = {}
        for r in self.db.execute("SELECT index_id, annotation_id, value FROM annotation_values"):
            values.setdefault(r["index_id"], {})[names[r["annotation_id"]]] = r["value"]
        return values
