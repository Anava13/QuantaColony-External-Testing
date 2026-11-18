"""A synthetic 12-photo project for trying Cropping: 4 variables, 8 sections per dish.

Genotype (4) x Medium (2) are plated in the 8 sections of each dish;
Passage (4) x Replicate (3) change from photo to photo, so the 96 indices
fill 12 photos exactly. The photos are drawn, not photographed: a dish on
a dark background with bright colonies whose size depends on the levels.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..core.params import DetectionParams
from .manager import DataManager
from .packets import LevelSpec, ProjectInfo, VariableSpec

DEMO_PROJECT_NAME = "Cropping demo (synthetic plates)"
STATS_DEMO_NAME = "Stats demo (synthetic plates, detected)"
PHOTO_SIZE = 900
DISH_MM = 90.0


def _variables() -> list[VariableSpec]:
    return [
        VariableSpec("genotype", "nominal", (LevelSpec("WT", "Wild type", True), LevelSpec("rif1", "rif1Δ"),
                                             LevelSpec("cdc13", "cdc13-2"), LevelSpec("rif1_cdc13", "rif1Δ cdc13-2")),
                     "Genotype"),
        VariableSpec("medium", "nominal", (LevelSpec("YPD", "YPD", True), LevelSpec("SC", "SC")), "Medium"),
        VariableSpec("passage", "ordinal", tuple(LevelSpec(p) for p in ("25", "50", "75", "100")), "Passage"),
        VariableSpec("replicate", "replicate", (LevelSpec("1"), LevelSpec("2"), LevelSpec("3")), "Replicate"),
    ]


def draw_plate(seed: int, rotation_deg: float, colony_scale: list[float]) -> bytes:
    """One dish photo as JPEG bytes. colony_scale gives a size factor for each of the 8 sections."""
    rng = np.random.default_rng(seed)
    image = Image.new("RGB", (PHOTO_SIZE, PHOTO_SIZE), (18, 19, 17))
    draw = ImageDraw.Draw(image)
    cx = PHOTO_SIZE / 2 + rng.uniform(-25, 25)
    cy = PHOTO_SIZE / 2 + rng.uniform(-25, 25)
    radius = PHOTO_SIZE * rng.uniform(0.40, 0.43)
    draw.ellipse([cx - radius - 10, cy - radius - 10, cx + radius + 10, cy + radius + 10], fill=(120, 122, 116))
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=(64, 66, 60))
    for section, scale in enumerate(colony_scale):
        start = np.radians(rotation_deg + section * 45 + 6)
        for _ in range(int(rng.integers(14, 24))):
            angle = start + rng.uniform(0, np.radians(33))
            distance = radius * np.sqrt(rng.uniform(0.04, 0.78))
            x, y = cx + distance * np.sin(angle), cy - distance * np.cos(angle)
            r = max(2.0, rng.normal(6.5, 1.5) * scale)
            draw.ellipse([x - r, y - r, x + r, y + r], fill=(226, 228, 220))
    image = image.filter(ImageFilter.GaussianBlur(0.8))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


def create_demo_project(manager: DataManager, name: str = DEMO_PROJECT_NAME) -> ProjectInfo:
    """Build the demo in the manager's library (photos only, nothing cropped) and leave it open."""
    manager.create_project(name, _variables(),
                           DetectionParams(level=-0.6, sense=0.95, rmin=2.0, rmax=25.0, maxpix=3000.0),
                           plate={"type": "circular", "sections": 8, "dish_mm": DISH_MM})
    for photo in range(12):
        passage, replicate = divmod(photo, 3)
        scales = [(1.15 - 0.12 * passage * (g in (1, 3))) * (1.0 if m == 0 else 0.8)
                  for g in range(4) for m in range(2)]
        content = draw_plate(seed=photo, rotation_deg=float(np.random.default_rng(100 + photo).uniform(-12, 12)),
                             colony_scale=scales)
        manager.add_photo(content, file_name=f"Plate_{photo + 1:02d}.jpg")
    return manager.project_info()


def _rotation(photo: int) -> float:
    return float(np.random.default_rng(100 + photo).uniform(-12, 12))


def complete_demo_project(manager: DataManager, name: str = STATS_DEMO_NAME) -> ProjectInfo:
    """The demo with every photo cropped and every index detected with the default settings
    (for trying the project page and Stats without doing the earlier steps by hand)."""
    from ..core.cropping import DishLayout, fit_dish
    from ..core.session import DetectionSession

    info = create_demo_project(manager, name)
    packet = manager.crop_packet()
    for position, photo in enumerate(packet.photos):
        passage, replicate = divmod(position, 3)
        cx, cy, r = fit_dish(manager.photo_image(photo.photo_id))
        layout = DishLayout.template(cx, cy, r, 8, rotation=_rotation(position) % 360)
        sections = [{"genotype": ("WT", "rif1", "cdc13", "rif1_cdc13")[i // 2], "medium": ("YPD", "SC")[i % 2],
                     "passage": ("25", "50", "75", "100")[passage], "replicate": ("1", "2", "3")[replicate]}
                    for i in range(8)]
        manager.commit_photo(photo.photo_id, layout.to_dict(), sections)
    for index in manager.project_info().indices:
        image = manager.checkout_image(index.index_id)
        session = DetectionSession(image.record, image.defaults)
        session.initialize_detection()
        manager.commit_detection(session.result())
    return info
