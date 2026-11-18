"""File writers for exports. Exports are output only: QuantaColony never reads them back.

Every exported table carries provenance columns (software version,
derivation version, detection revision), so any number traces back to the
code and the decisions that produced it.
"""

from __future__ import annotations

import csv
import html
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

# Overlay colors, as on screen: teal for automatic detection, lime for colonies added by hand
OVERLAY_AUTOMATIC = (46, 200, 187)
OVERLAY_MANUAL = (123, 245, 1)
OVERLAY_SPLIT = (156, 140, 255)           # cut from a blob of touching colonies
OVERLAY_HALO = (10, 11, 9)
OVERLAY_GROWTH = (229, 169, 47)        # growth outside the marked colonies


REPLACE_TRIES = 20                    # Windows: an antivirus or indexer can hold a new file for a moment
REPLACE_WAIT_S = 0.1


class ExportBlocked(OSError):
    """Windows (or the folder's permissions) refused a write; the message is meant for the user."""


def _atomic_target(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.with_name(path.name + ".tmp")


def _finish(temporary: Path, path: Path) -> None:
    """Rename the finished temporary file into place, retrying while another program holds it."""
    for attempt in range(REPLACE_TRIES):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(REPLACE_WAIT_S)


def check_writable(folder: Path) -> None:
    """Create the folder and write a test file in it, so a blocked folder fails before any work starts."""
    folder = Path(folder)
    probe = folder / ".quantacolony-write-test"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink()
    except PermissionError as error:
        raise ExportBlocked(blocked_message(folder)) from error


def export_problem(error: OSError, folder: Path) -> str:
    """The message for a failed export: the Windows explanation for a refused write, else the error."""
    if isinstance(error, ExportBlocked):
        return str(error)
    if isinstance(error, PermissionError):
        return blocked_message(Path(error.filename).parent if getattr(error, "filename", None) else folder)
    return str(error)


def blocked_message(folder: Path) -> str:
    return (f"Windows did not let QuantaColony write to\n{folder}\n\n"
            "If this folder is in Documents, Desktop or Pictures, Windows Security's Controlled folder access "
            "may be blocking it. Choose another folder (for example one directly on a drive, such as "
            "C:\\QuantaColony Exports), or allow QuantaColony's python.exe in Windows Security > Virus & threat "
            "protection > Ransomware protection.")


def write_table_csv(columns, rows, path: Path, provenance: dict | None = None) -> Path:
    """Rows of dicts as CSV; provenance values are appended to every row as extra columns."""
    path = Path(path)
    provenance = provenance or {}
    temporary = _atomic_target(path)
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns) + list(provenance))
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, **provenance})
    _finish(temporary, path)
    return path


def write_png(image: np.ndarray, path: Path) -> Path:
    path = Path(path)
    temporary = _atomic_target(path)
    Image.fromarray(np.ascontiguousarray(image)).save(temporary, format="PNG")
    _finish(temporary, path)
    return path


def write_json(data, path: Path) -> Path:
    path = Path(path)
    temporary = _atomic_target(path)
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _finish(temporary, path)
    return path


def write_figure(figure, path: Path) -> Path:
    """A matplotlib figure; the extension sets the format (.png, .pdf, .svg)."""
    path = Path(path)
    temporary = _atomic_target(path)
    figure.savefig(temporary, format=path.suffix.lstrip(".").lower() or "png", dpi=200, bbox_inches="tight")
    _finish(temporary, path)
    return path


def safe_name(text: str) -> str:
    """File-system-safe version of a label."""
    kept = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in text)
    return "_".join(part for part in kept.split("_") if part) or "item"


def write_overlay_png(image: np.ndarray, colonies, path: Path, scale: int = 2) -> Path:
    """The crop with every colony drawn on it (outline where traced, circle otherwise).

    colonies are in crop coordinates. The image is enlarged `scale` times so thin
    outlines stay visible on small crops."""
    path = Path(path)
    picture = Image.fromarray(np.ascontiguousarray(image)).convert("RGB")
    picture = picture.resize((picture.width * scale, picture.height * scale), Image.NEAREST)
    draw = ImageDraw.Draw(picture)
    for i in range(len(colonies)):
        color = {"manual": OVERLAY_MANUAL, "split": OVERLAY_SPLIT}.get(colonies.kinds[i], OVERLAY_AUTOMATIC)
        outline = colonies.boundaries[i]
        if outline is not None and len(outline) > 2:
            points = [((x + 0.5) * scale, (y + 0.5) * scale) for x, y in outline]
            points.append(points[0])
            draw.line(points, fill=OVERLAY_HALO, width=scale + 2)
            draw.line(points, fill=color, width=scale)
        else:
            x, y = (colonies.centers[i] + 0.5) * scale
            r = float(colonies.radii[i]) * scale
            draw.ellipse([x - r, y - r, x + r, y + r], outline=OVERLAY_HALO, width=scale + 2)
            draw.ellipse([x - r, y - r, x + r, y + r], outline=color, width=scale)
    temporary = _atomic_target(path)
    picture.save(temporary, format="PNG")
    _finish(temporary, path)
    return path


def write_growth_png(image: np.ndarray, growth: np.ndarray, colonies: np.ndarray, path: Path) -> Path:
    """The crop with growth tinted: marked colonies teal, other growth amber."""
    path = Path(path)
    picture = np.ascontiguousarray(image[..., :3]).astype(float)
    for mask, color in ((growth & ~colonies, OVERLAY_GROWTH), (colonies, OVERLAY_AUTOMATIC)):
        picture[mask] = picture[mask] * 0.45 + np.array(color) * 0.55
    return write_png(picture.round().astype(np.uint8), path)


def write_text(text: str, path: Path) -> Path:
    path = Path(path)
    temporary = _atomic_target(path)
    temporary.write_text(text, encoding="utf-8")
    _finish(temporary, path)
    return path


_PAGE_STYLE = """
body { font-family: "Segoe UI", Inter, Arial, sans-serif; color: #2D2D2D; background: #EEEFEA; margin: 0; }
header { background: #212121; color: #FBFCF8; padding: 18px 32px; }
header h1 { margin: 0; font-size: 22px; } header p { margin: 4px 0 0; color: #C9CCC1; font-size: 13px; }
main { padding: 24px 32px 40px; max-width: 1200px; }
section { background: #FFFFFF; border: 1px solid #DCDCDC; border-radius: 10px; padding: 18px 22px; margin-bottom: 18px; }
h2 { font-size: 16px; margin: 0 0 10px; }
p, li { font-size: 14px; line-height: 1.5; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th { text-align: left; background: #F0F0EC; padding: 7px 10px; border-bottom: 1px solid #DCDCDC; }
td { padding: 6px 10px; border-bottom: 1px solid #ECECE8; vertical-align: top; }
.tiles { display: flex; flex-wrap: wrap; gap: 12px; }
.tile { background: #F6F7F2; border-radius: 8px; padding: 10px 16px; min-width: 120px; }
.tile b { display: block; font-size: 20px; } .tile span { font-size: 12px; color: #5C5F56; }
.muted { color: #6B6E66; } a { color: #3B6B00; }
.thumb { height: 64px; border-radius: 4px; border: 1px solid #DCDCDC; }
code { background: #F0F0EC; padding: 1px 5px; border-radius: 4px; }
"""


def write_browsable_summary(summary: dict, path: Path) -> Path:
    """index.html at the top of a browsable copy: what the project holds and where each file is.

    summary: name, exported_at, software_version, tiles [(number, label)],
    variables [(name, type, levels text)], indices [dict with name, levels, state,
    colonies, folder, crop, overlay, table] (paths relative to the page)."""
    e = html.escape
    tiles = "".join(f"<div class='tile'><b>{e(str(n))}</b><span>{e(label)}</span></div>"
                    for n, label in summary["tiles"])
    variables = "".join(f"<tr><td>{e(name)}</td><td>{e(kind)}</td><td>{e(levels)}</td></tr>"
                        for name, kind, levels in summary["variables"])
    rows = []
    for index in summary["indices"]:
        crop = (f"<a href='{e(index['overlay'] or index['crop'])}'><img class='thumb' "
                f"src='{e(index['overlay'] or index['crop'])}'></a>") if index["crop"] else ""
        files = " · ".join(f"<a href='{e(link)}'>{e(label)}</a>" for label, link in
                           (("crop", index["crop"]), ("colonies drawn", index["overlay"]),
                            ("growth", index.get("growth", "")), ("colony table", index["table"]),
                            ("folder", index["folder"])) if link)
        rows.append(f"<tr><td>{crop}</td><td><b>{e(index['name'])}</b><br><span class='muted'>"
                    f"{e(index['levels'])}</span></td><td>{e(index['state'])}</td>"
                    f"<td>{e(str(index['colonies']))}</td><td>{e(index.get('coverage', ''))}</td>"
                    f"<td>{files}</td></tr>")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{e(summary['name'])} · QuantaColony export</title>
<style>{_PAGE_STYLE}</style></head><body>
<header><h1>{e(summary['name'])}</h1>
<p>Browsable copy exported {e(summary['exported_at'])} by QuantaColony {e(summary['software_version'])}</p></header>
<main>
<section><h2>Project</h2><div class="tiles">{tiles}</div></section>
<section><h2>About this folder</h2>
<p>This folder is a read-only copy for looking inside a project. QuantaColony never reads it back:
changes here do not reach the project. To continue work on the project, import the <code>.qcproj</code> file instead.</p>
<ul>
<li><code>photos/</code> holds the plate photos exactly as they were added.</li>
<li><code>indices/&lt;index&gt;/</code> holds, for each index, its crop (<code>crop.png</code>), the crop with every
colony drawn on it (<code>colonies.png</code>: teal for automatic detection, lime for colonies added by hand) and the
colony measurements (<code>colonies.csv</code>), and the growth found on it (<code>growth.png</code>: teal for
marked colonies, amber for other growth).</li>
<li><code>summary.csv</code> holds one row per index; <code>project.json</code> holds the variables, regions,
detection settings and edit history.</li>
</ul></section>
<section><h2>Variables</h2><table><tr><th>Variable</th><th>Type</th><th>Levels</th></tr>{variables}</table></section>
<section><h2>Indices</h2><table><tr><th></th><th>Index</th><th>State</th><th>Colonies</th>
<th>Growth coverage</th><th>Files</th></tr>
{''.join(rows)}</table></section>
</main></body></html>
"""
    return write_text(page, path)
