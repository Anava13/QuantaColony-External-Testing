// The plate canvas: the photo, the colonies drawn over it, zoom and pan. Mouse events on the photo are
// passed to the tools (main.js) in photo pixels; this file decides nothing about colonies.
// Colors follow quantacolony/theme/tokens.py (ColonyColors).

const COLORS = { detected: "#2EC8BB", split: "#9C8CFF", manual: "#7BF501", selected: "#FF6B5E",
                 candidate: "#7BF501", drawn: "#ECEEE6", halo: "#0A0B09", ring: "#FF3DFF",
                 flagged: "#FF9F1C", missed: "#FF3DFF",
                 dot: "#FF3DFF", dotHeld: "#7BF501", addBox: "#7BF501", deleteBox: "#FF6B5E" };
const MAX_SCALE = 40;

export class PlateView {
  constructor(canvas, tools) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.tools = tools;                // { press(x, y, button), move(x, y), release(x, y, button), wheel() }
    this.bitmap = null;
    this.view = null;
    this.ring = null;
    this.drag = null;                  // rubber band { x0, y0, x1, y1, color }
    this.scale = 1;
    this.ox = 0;
    this.oy = 0;
    this.panning = null;
    this.space = false;
    new ResizeObserver(() => this.resize()).observe(canvas);
    canvas.addEventListener("wheel", e => this.wheel(e), { passive: false });
    canvas.addEventListener("mousedown", e => this.down(e));
    window.addEventListener("mousemove", e => this.moveEvent(e));
    window.addEventListener("mouseup", e => this.up(e));
    canvas.addEventListener("contextmenu", e => e.preventDefault());
    window.addEventListener("keydown", e => { if (e.code === "Space" && !this.space && !isTyping(e)) { this.space = true; canvas.style.cursor = "grab"; } });
    window.addEventListener("keyup", e => { if (e.code === "Space") { this.space = false; canvas.style.cursor = ""; } });
  }

  async setImage(imageData) {
    this.bitmap = await createImageBitmap(imageData);
    this.fit();
  }

  setView(view) { this.view = view; this.draw(); }
  setRing(ring) { this.ring = ring && ring.length ? ring : null; this.draw(); }

  // ----- geometry --------------------------------------------------------------------
  resize() {
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(1, this.canvas.clientWidth * dpr);
    this.canvas.height = Math.max(1, this.canvas.clientHeight * dpr);
    if (this.bitmap && !this.userZoomed) this.fit(); else this.draw();
  }

  fit() {
    if (!this.bitmap) return;
    const dpr = window.devicePixelRatio || 1;
    const w = this.canvas.width / dpr, h = this.canvas.height / dpr;
    this.scale = Math.min(w / this.bitmap.width, h / this.bitmap.height) * 0.94;
    this.fitScale = this.scale;
    this.ox = (w - this.bitmap.width * this.scale) / 2;
    this.oy = (h - this.bitmap.height * this.scale) / 2;
    this.userZoomed = false;
    this.draw();
  }

  toImage(e) {
    const rect = this.canvas.getBoundingClientRect();
    return [(e.clientX - rect.left - this.ox) / this.scale - 0.5, (e.clientY - rect.top - this.oy) / this.scale - 0.5];
  }

  screenToImage(pixels) { return pixels / this.scale; }

  zoomAt(sx, sy, factor) {
    const target = Math.min(Math.max(this.scale * factor, this.fitScale || 0.01), MAX_SCALE);
    const ix = (sx - this.ox) / this.scale, iy = (sy - this.oy) / this.scale;
    this.scale = target;
    this.ox = sx - ix * target;
    this.oy = sy - iy * target;
    this.userZoomed = true;
    this.draw();
  }

  zoomBy(factor) { this.zoomAt(this.canvas.clientWidth / 2, this.canvas.clientHeight / 2, factor); }

  centerOn(x, y, scale) {
    this.scale = Math.min(Math.max(scale, this.fitScale), MAX_SCALE);
    this.ox = this.canvas.clientWidth / 2 - (x + 0.5) * this.scale;
    this.oy = this.canvas.clientHeight / 2 - (y + 0.5) * this.scale;
    this.userZoomed = true;
    this.draw();
  }

  // ----- mouse -----------------------------------------------------------------------
  wheel(e) {
    e.preventDefault();
    const rect = this.canvas.getBoundingClientRect();
    this.zoomAt(e.clientX - rect.left, e.clientY - rect.top, e.deltaY < 0 ? 1.2 : 1 / 1.2);
  }

  down(e) {
    if (e.button === 1 || this.space) {
      this.panning = { x: e.clientX, y: e.clientY, ox: this.ox, oy: this.oy };
      e.preventDefault();
      return;
    }
    const [x, y] = this.toImage(e);
    this.pressed = true;
    this.tools.press(x, y, e.button === 2 ? "right" : "left");
  }

  moveEvent(e) {
    if (this.panning) {
      this.ox = this.panning.ox + e.clientX - this.panning.x;
      this.oy = this.panning.oy + e.clientY - this.panning.y;
      this.userZoomed = true;
      this.draw();
      return;
    }
    if (e.target !== this.canvas && !this.pressed) return;
    const [x, y] = this.toImage(e);
    this.tools.move(x, y, this.pressed);
  }

  up(e) {
    if (this.panning) { this.panning = null; return; }
    if (!this.pressed) return;
    this.pressed = false;
    const [x, y] = this.toImage(e);
    this.tools.release(x, y, e.button === 2 ? "right" : "left");
  }

  // ----- drawing ---------------------------------------------------------------------
  draw() {
    const ctx = this.ctx, dpr = window.devicePixelRatio || 1;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = "#0A0B09";
    ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
    if (!this.bitmap) return;
    ctx.setTransform(dpr * this.scale, 0, 0, dpr * this.scale, dpr * this.ox, dpr * this.oy);
    ctx.imageSmoothingEnabled = this.scale < 2;
    ctx.drawImage(this.bitmap, 0, 0);
    const v = this.view;
    if (v) {
      const selected = new Set(v.selected);
      const dimmed = v.mode === "AddManyColonies" && v.roi;
      const flags = v.flags || {};
      for (const c of v.colonies) {
        const flagged = flags[c.id] !== undefined && !selected.has(c.id);
        const color = selected.has(c.id) ? COLORS.selected : flagged ? COLORS.flagged : c.kind === "manual" ? COLORS.manual
          : c.kind === "split" ? COLORS.split : COLORS.detected;
        this.shape(c, color, selected.has(c.id) || flagged ? 2.4 : 1.6, false, dimmed ? 0.35 : 1);
      }
      for (const [x, y, r] of v.missed || []) this.shape({ x, y, r: r + 2 }, COLORS.missed, 2, true, 1);   // training check
      for (const c of v.candidates) this.shape(c, COLORS.candidate, 1.8, true, 1);
      for (const c of v.pending) this.shape(c, COLORS.drawn, 1.8, true, 1);      // drawn, not added yet
      if (v.roi) this.box(v.roi[0], v.roi[1], v.roi[0] + v.roi[2], v.roi[1] + v.roi[3], COLORS.addBox);
      if (v.rect) this.box(...v.rect, COLORS.selected);
    }
    if (this.drag) this.box(this.drag.x0, this.drag.y0, this.drag.x1, this.drag.y1, this.drag.color);
    this.drawDots();
    if (this.ring) {
      const [x, y, r] = this.ring;
      ctx.beginPath();
      ctx.arc(x + 0.5, y + 0.5, r, 0, 2 * Math.PI);
      ctx.setLineDash([6 / this.scale, 4 / this.scale]);
      ctx.strokeStyle = COLORS.ring;
      ctx.lineWidth = 3 / this.scale;
      ctx.stroke();
      ctx.setLineDash([]);
    }
    const label = document.getElementById("zoom-level");
    if (label) label.textContent = `${Math.round(this.scale * 100)}%`;
  }

  path(c) {
    // pixel centers sit at integer coordinates: shift by half a pixel to the drawing grid
    const ctx = this.ctx;
    ctx.beginPath();
    if (c.outline && c.outline.length > 4) {
      ctx.moveTo(c.outline[0] + 0.5, c.outline[1] + 0.5);
      for (let i = 2; i < c.outline.length; i += 2) ctx.lineTo(c.outline[i] + 0.5, c.outline[i + 1] + 0.5);
      ctx.closePath();
    } else {
      ctx.arc(c.x + 0.5, c.y + 0.5, Math.max(c.r, 0.5), 0, 2 * Math.PI);
    }
  }

  shape(c, color, width, dashed, alpha) {
    const ctx = this.ctx;
    ctx.globalAlpha = alpha;
    this.path(c);
    ctx.setLineDash(dashed ? [4 / this.scale, 3 / this.scale] : []);
    ctx.strokeStyle = COLORS.halo;
    ctx.lineWidth = (width + 1.6) / this.scale;
    ctx.stroke();
    ctx.strokeStyle = color;
    ctx.lineWidth = width / this.scale;
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
  }

  // The region step's dots: magenta until the box being dragged (or the region drawn) holds them, then green.
  drawDots() {
    if (!this.dots || !this.dots.length) return;
    const d = this.drag, roi = this.view && this.view.roi, rect = this.view && this.view.rect;
    const box = !this.dotsCapture ? null
      : d ? [Math.min(d.x0, d.x1), Math.min(d.y0, d.y1), Math.max(d.x0, d.x1), Math.max(d.y0, d.y1)]
      : roi ? [roi[0], roi[1], roi[0] + roi[2], roi[1] + roi[3]]
      : rect ? [Math.min(rect[0], rect[2]), Math.min(rect[1], rect[3]), Math.max(rect[0], rect[2]), Math.max(rect[1], rect[3])]
      : null;
    const ctx = this.ctx, r = 10 / this.scale;
    for (const [x, y] of this.dots) {
      const held = box && x >= box[0] && x <= box[2] && y >= box[1] && y <= box[3];
      ctx.beginPath();
      ctx.arc(x + 0.5, y + 0.5, r + 2.5 / this.scale, 0, 2 * Math.PI);
      ctx.fillStyle = COLORS.halo;                             // a dark rim keeps the dot visible on any agar
      ctx.fill();
      ctx.beginPath();
      ctx.arc(x + 0.5, y + 0.5, r, 0, 2 * Math.PI);
      ctx.fillStyle = held ? COLORS.dotHeld : COLORS.dot;
      ctx.fill();
      ctx.lineWidth = 3 / this.scale;
      ctx.strokeStyle = "#FFFFFF";
      ctx.stroke();
    }
  }

  box(x0, y0, x1, y1, color) {
    const ctx = this.ctx;
    ctx.setLineDash([8 / this.scale, 6 / this.scale]);
    ctx.strokeStyle = color;
    ctx.lineWidth = 3 / this.scale;
    ctx.strokeRect(Math.min(x0, x1) + 0.5, Math.min(y0, y1) + 0.5, Math.abs(x1 - x0), Math.abs(y1 - y0));
    ctx.setLineDash([]);
  }
}

export function isTyping(e) {
  const t = e.target;
  return t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA") && t.type !== "range" && t.type !== "checkbox";
}
