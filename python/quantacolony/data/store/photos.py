"""Plate photos, their Cropping layouts, and the photo region of every index."""

from __future__ import annotations

import hashlib
import io
import json

import numpy as np
from PIL import Image

from ..codec import array_to_blob, blob_to_array
from ..packets import PhotoInfo
from .project_file import ProjectFile, now

THUMBNAIL_SIZE = 320              # longest side of the stored thumbnail, pixels
CACHE_SIZE = 4                    # decoded photos kept in memory


def make_thumbnail(image: Image.Image) -> bytes:
    small = image.convert("RGB")
    small.thumbnail((THUMBNAIL_SIZE, THUMBNAIL_SIZE))
    buffer = io.BytesIO()
    small.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


class Photos:
    def __init__(self, file: ProjectFile):
        self.file = file
        self._arrays: dict[int, np.ndarray] = {}

    @property
    def db(self):
        return self.file.connection

    # ----- photos -----------------------------------------------------------
    def insert(self, content: bytes, file_name: str, scale: dict, px_per_mm: float | None) -> int:
        """Store a photo exactly as uploaded, with its size and a thumbnail read once now.
        scale: the scale's evidence (core.scale); px_per_mm: computed from it, None without a scale."""
        self.file.require_transaction()
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            thumbnail = make_thumbnail(image)
        return self.db.execute(
            "INSERT INTO photos (file_name, content, sha256, px_per_mm, scaling_method, added_at, width, height, "
            "thumbnail, scale, scale_px_per_mm) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (file_name, content, hashlib.sha256(content).hexdigest(), _legacy(px_per_mm),
             scale.get("method", ""), now(), width, height, thumbnail, json.dumps(scale),
             None if px_per_mm is None else float(px_per_mm))).lastrowid

    def fill_scale_evidence(self, plate: dict) -> int:
        """Files from version 5 kept only a px/mm: give each kept scale its evidence (a dish scale the
        project's dish diameter, anything else an imported value). Returns the photos updated."""
        self.file.require_transaction()
        rows = self.db.execute("SELECT photo_id, scaling_method, scale_px_per_mm FROM photos "
                               "WHERE scale = '{}'").fetchall()
        dish_mm = plate.get("dish_mm")
        for row in rows:
            if row["scale_px_per_mm"] is None:
                evidence = ({"method": "dish", "dish_mm": float(dish_mm), "unit": "mm"}
                            if row["scaling_method"] == "dish" and dish_mm else {"method": "none", "unit": "mm"})
            elif row["scaling_method"] == "dish" and dish_mm:
                evidence = {"method": "dish", "dish_mm": float(dish_mm), "unit": "mm"}
            else:
                evidence = {"method": "fixed", "value": float(row["scale_px_per_mm"]), "unit": "mm"}
            self.db.execute("UPDATE photos SET scale = ? WHERE photo_id = ?", (json.dumps(evidence), row["photo_id"]))
        return len(rows)

    def fill_missing_sizes(self) -> int:
        """Size and thumbnail for photos stored by an older version (each photo is read once, here)."""
        self.file.require_transaction()
        missing = [r["photo_id"] for r in self.db.execute("SELECT photo_id FROM photos WHERE width = 0 "
                                                          "OR thumbnail IS NULL")]
        for photo_id in missing:
            content = self.db.execute("SELECT content FROM photos WHERE photo_id = ?", (photo_id,)).fetchone()[0]
            with Image.open(io.BytesIO(content)) as image:
                width, height = image.size
                thumbnail = make_thumbnail(image)
            self.db.execute("UPDATE photos SET width = ?, height = ?, thumbnail = ? WHERE photo_id = ?",
                            (width, height, thumbnail, photo_id))
        return len(missing)

    def infos(self) -> tuple[PhotoInfo, ...]:
        """Every photo in order, without reading any image data."""
        return tuple(PhotoInfo(row["photo_id"], position, row["file_name"], row["width"], row["height"],
                               row["px_per_mm"], row["scaling_method"], json.loads(row["scale"] or "{}"))
                     for position, row in enumerate(self.db.execute(
                         "SELECT photo_id, file_name, width, height, scale_px_per_mm AS px_per_mm, scaling_method, "
                         "scale FROM photos ORDER BY photo_id"), start=1))

    def row(self, photo_id: int):
        """A photo's record; px_per_mm is its scale (None without one)."""
        return self.db.execute("SELECT photo_id, file_name, sha256, scale_px_per_mm AS px_per_mm, scaling_method, "
                               "scale, width, height FROM photos WHERE photo_id = ?", (photo_id,)).fetchone()

    def shape(self, photo_id: int) -> tuple[int, int]:
        """(rows, columns) of a photo, from the stored size."""
        row = self.row(photo_id)
        if row is None:
            raise KeyError(photo_id)
        return int(row["height"]), int(row["width"])

    def array(self, photo_id: int) -> np.ndarray:
        """A photo as read-only RGB pixels; the last few are kept in memory."""
        if photo_id not in self._arrays:
            row = self.db.execute("SELECT content FROM photos WHERE photo_id = ?", (photo_id,)).fetchone()
            if row is None:
                raise KeyError(photo_id)
            if len(self._arrays) >= CACHE_SIZE:
                self._arrays.pop(next(iter(self._arrays)))
            array = np.array(Image.open(io.BytesIO(row["content"])).convert("RGB"))
            array.setflags(write=False)
            self._arrays[photo_id] = array
        return self._arrays[photo_id]

    def contents(self):
        """(photo id, file name, original bytes) for every photo, one at a time."""
        for row in self.db.execute("SELECT photo_id, file_name, content FROM photos ORDER BY photo_id"):
            yield row["photo_id"], row["file_name"], row["content"]

    def first_thumbnail(self) -> bytes | None:
        row = self.db.execute("SELECT thumbnail FROM photos ORDER BY photo_id LIMIT 1").fetchone()
        return bytes(row[0]) if row and row[0] is not None else None

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM photos").fetchone()[0]

    def set_scale(self, photo_id: int, scale: dict, px_per_mm: float | None) -> bool:
        """Store a photo's scale evidence and the px/mm computed from it. Returns True when the px/mm changed."""
        self.file.require_transaction()
        old = self.row(photo_id)
        self.db.execute("UPDATE photos SET scale = ?, scale_px_per_mm = ?, px_per_mm = ?, scaling_method = ? "
                        "WHERE photo_id = ?", (json.dumps(scale), None if px_per_mm is None else float(px_per_mm),
                                               _legacy(px_per_mm), scale.get("method", ""), photo_id))
        return old is not None and old["px_per_mm"] != (None if px_per_mm is None else float(px_per_mm))

    def forget_cache(self) -> None:
        self._arrays.clear()

    # ----- Cropping layouts ---------------------------------------------------
    def layouts(self) -> dict:
        return {row["photo_id"]: (json.loads(row["layout"]), json.loads(row["sections"]))
                for row in self.db.execute("SELECT * FROM photo_layouts")}

    def save_layout(self, photo_id: int, layout: dict, sections: list) -> None:
        self.file.require_transaction()
        self.db.execute("INSERT OR REPLACE INTO photo_layouts (photo_id, layout, sections, updated_at) "
                        "VALUES (?, ?, ?, ?)", (photo_id, json.dumps(layout), json.dumps(sections), now()))

    def photos_needing_cropping(self) -> int:
        """Photos with neither a saved layout nor any region assigned."""
        return self.db.execute(
            "SELECT COUNT(*) FROM photos p"
            " WHERE NOT EXISTS (SELECT 1 FROM photo_layouts l WHERE l.photo_id = p.photo_id)"
            " AND NOT EXISTS (SELECT 1 FROM assignments a WHERE a.photo_id = p.photo_id)").fetchone()[0]

    # ----- index regions ----------------------------------------------------
    def assignment(self, index_id: int):
        return self.db.execute("SELECT * FROM assignments WHERE index_id = ?", (index_id,)).fetchone()

    def assigned_ids(self) -> list[int]:
        return [r["index_id"] for r in self.db.execute("SELECT index_id FROM assignments ORDER BY index_id")]

    def assigned_on(self, photo_id: int) -> list[int]:
        return [r["index_id"] for r in self.db.execute("SELECT index_id FROM assignments WHERE photo_id = ?",
                                                       (photo_id,))]

    def save_assignment(self, index_id: int, photo_id: int, kind: str, geometry: dict, outline: np.ndarray) -> bool:
        """Store an index's region. Returns True when an earlier region of the index differs from this one."""
        self.file.require_transaction()
        old = self.assignment(index_id)
        changed = old is not None and (old["photo_id"] != photo_id
                                       or not np.array_equal(blob_to_array(old["outline"]), outline))
        self.db.execute("INSERT OR REPLACE INTO assignments (index_id, photo_id, region_kind, geometry, outline, "
                        "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (index_id, photo_id, kind, json.dumps(geometry), array_to_blob(outline), now()))
        return changed

    def delete_assignment(self, index_id: int) -> None:
        self.file.require_transaction()
        self.db.execute("DELETE FROM assignments WHERE index_id = ?", (index_id,))


def _legacy(px_per_mm: float | None) -> float:
    """The version 5 column: the scale, or 1.0 without one."""
    return 1.0 if px_per_mm is None else float(px_per_mm)
