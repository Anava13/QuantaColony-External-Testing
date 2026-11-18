"""Growth coverage: which pixels of an index are yeast growth, learned from the colonies marked
in Colony Detection (Qt-free).

Each plate teaches its own classifier, because lighting, agar and strain color differ from
photo to photo:

1. Growable area. The index's own region from Cropping, less a band along its edge where the
   cut or the dish rim blurs into the photo: RIM_FRACTION of the region's size, at least RIM_PX.
2. Lighting. The agar's brightness and color drift across a photo. A smooth agar surface is
   estimated from pixels away from every colony (cell medians, outliers rejected, filled
   and smoothed), and every pixel is expressed as its difference from the local agar in
   units of the agar's own noise, per color channel (CIE Lab).
   Only pixels that look like agar may sample it. The plate shows what agar and growth look
   like: a thin ring just outside the marked colonies is agar, their insides are growth. A
   pixel whose color lies past the midpoint from agar toward growth is left out of the agar
   sample. Without this, a confluent lawn far from every marked colony would be sampled as
   agar, the surface would rise to the lawn's brightness, and the lawn would be taught as agar.
   Lighting drifts by far less than half the agar-to-growth difference, so true agar passes.
3. Labels from the plate. Pixels inside marked colonies are growth. Pixels away from
   colonies that sit within the agar's noise are agar.
4. Classifier. A class-balanced logistic regression on the lighting-corrected color, a smoothed copy of it
   and the local texture gives each pixel a probability of growth.
5. Clean-up. Pixels above the low threshold are kept only where they connect to a confident
   pixel or a marked colony (hysteresis). Parts thinner than a small colony are dropped, even
   where they touch thicker growth: the dish rim, scratches and streak marks are bright like
   yeast but thin, and a colony touching the rim must not carry the whole rim in with it.
   Specks smaller than the smallest marked colony are dropped. Marked colonies are always
   growth.
6. Dish wall. Light catching the dish wall forms bright or dark arcs along the edge of the
   region, often thicker than the thin-part rule removes (a wall several colonies thick on
   plates photographed at an angle). A growth part that holds no marked colony and lies
   mostly (WALL_SHARE) within WALL_FRACTION of the region's size from its edge is the wall
   and is dropped. A lawn reaching the edge extends inward and stays; an unmarked colony
   right at the edge is lost, which costs far less than the wall it rules out.
Steps 2 to 4 run twice: growth found in the first pass is removed from the agar sample of
the second, so unmarked lawns do not lift the agar surface.

A plate with no marked colony has no growth labels. It falls back to an unsupervised rule
(pixels far outside the agar's noise, in connected regions) and is flagged as such.

Coverage numbers divide by the growable pixel count:
    growth coverage   growth pixels / growable pixels
    colony coverage   pixels inside the union of marked colonies / growable pixels
                      (overlapping colonies count once)
    separate coverage the same for separate colonies only: colonies split from a blob of touching
                      colonies (kind 'split') are left out
    capture ratio     captured growth / growth: the share of the plate's growth that resolved into
                      separate colonies. Captured growth is every growth pixel of a connected growth
                      piece that holds exactly one marked colony, that colony not split from touching
                      ones. Growth is measured the same way in the numerator and the denominator, so
                      the halo between a marked circle and the colony's growth edge counts as
                      captured. A piece holding two or more marked colonies is merged growth, and so
                      is a piece holding none; split colonies count as merged growth, which keeps the
                      25% cutoff of the dilution series (SEPARABLE_CAPTURE_RATIO) meaningful whether
                      or not detection split them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from skimage import color, draw, measure, morphology

from .colony_set import KIND_SPLIT, ColonySet

SEPARABLE_CAPTURE_RATIO = 0.25   # below this, colonies merged too much to measure (the wild-type dilution
                                 # series: 100X falls below it, 500X and sparser plates stay above)
GROWTH_VERSION = 5               # 2: agar is sampled only from pixels that look like agar (confluent plates)
                                 # 3: the capture ratio counts separate colonies only (split ones are merged growth)
                                 # 4: growth along the dish wall is dropped (Rescue: wall arcs read as growth)
                                 # 5: the capture ratio counts growth pieces holding one separate colony

RIM_PX = 2                       # band along the region edge left out of the growable area: at least this,
RIM_FRACTION = 0.006             # or this share of the region's larger side (9 px on a 1500 px dish)
CELLS = 12                       # agar surface: cells along the longer side of the image
AGAR_Z = 2.0                     # agar labels: within this many noise units of the local agar
STRONG, WEAK = 0.85, 0.5         # hysteresis on the growth probability
MAX_TRAINING = 20000             # pixels per class used to fit the classifier
UNSUPERVISED_STRONG, UNSUPERVISED_WEAK = 8.0, 4.0     # noise units, plates without marked colonies
PASSES = 2
THIN_FRACTION = 0.75             # regions thinner than this share of a small colony's radius are not growth
AGAR_SIDE = 0.5                  # agar samples lie less than this share of the way from agar toward growth
RING_GAP, RING_WIDTH = 0.25, 0.6 # the agar ring outside each marked colony, in colony radii
MIN_CONTRAST = 2.0               # Lab units; below this agar and growth look alike and no pixel is ruled out
WALL_FRACTION = 0.05             # the dish wall zone: within this share of the region's larger side from its edge
WALL_SHARE = 0.6                 # a growth part with this share of its pixels in the zone, and no marked colony, is wall
                                 # (Rescue series: capture ratios agree with the MATLAB pipeline's on the 25% cutoff
                                 # for 114 of 120 plates, against 98 without this step)


@dataclass(frozen=True)
class GrowthResult:
    growth: np.ndarray            # (rows, cols) bool: growth inside the growable area
    probability: np.ndarray       # (rows, cols) float: probability of growth (NaN outside the growable area)
    growable: np.ndarray          # (rows, cols) bool
    colonies: np.ndarray          # (rows, cols) bool: union of marked colonies inside the growable area
    supervised: bool              # False when the plate had no marked colony to learn from
    separate: np.ndarray | None = None   # union of the colonies not split from touching blobs (None: all of them)
    captured: np.ndarray | None = None   # growth pieces holding exactly one separate marked colony

    @property
    def growable_px(self) -> int:
        return int(self.growable.sum())

    def metrics(self) -> dict:
        area = self.growable_px
        growth = int(self.growth.sum())
        colonies = int(self.colonies.sum())
        separate = int(self.separate.sum()) if self.separate is not None else colonies
        inside = self.probability[self.growable]
        uncertain = float(np.mean((inside > 0.25) & (inside < 0.75))) if inside.size else np.nan
        growth_coverage = growth / area if area else np.nan
        colony_coverage = colonies / area if area else np.nan
        separate_coverage = separate / area if area else np.nan
        captured = int(self.captured.sum()) if self.captured is not None else separate
        return {"growable_px": area, "growth_px": growth, "colony_px": colonies,
                "growth_coverage": growth_coverage, "colony_coverage": colony_coverage,
                "separate_coverage": separate_coverage, "captured_px": captured,
                "capture_ratio": captured / growth if growth else np.nan,
                "uncertain_fraction": uncertain, "supervised": self.supervised}


# ----- masks ---------------------------------------------------------------------------
def growable_area(region_mask: np.ndarray) -> np.ndarray:
    rows, cols = np.nonzero(region_mask)
    size = max(np.ptp(rows), np.ptp(cols)) + 1 if rows.size else 0
    band = max(RIM_PX, int(round(RIM_FRACTION * size)))
    return ndimage.binary_erosion(region_mask, iterations=band) if band else region_mask.copy()


def colony_union(shape: tuple[int, int], colonies: ColonySet) -> np.ndarray:
    """Pixels inside any colony: its traced outline where there is one, its circle otherwise."""
    union = np.zeros(shape, dtype=bool)
    for i in range(len(colonies)):
        outline = colonies.boundaries[i]
        if outline is not None and len(outline) > 2:
            rr, cc = draw.polygon(outline[:, 1], outline[:, 0], shape)
        else:
            x, y = colonies.centers[i]
            rr, cc = draw.disk((y, x), max(float(colonies.radii[i]), 0.5), shape=shape)
        union[rr, cc] = True
    return union


# ----- lighting ----------------------------------------------------------------------------
def agar_surface(channel: np.ndarray, agar: np.ndarray) -> np.ndarray:
    """Smooth estimate of the agar level of one channel from the pixels marked agar."""
    rows, cols = channel.shape
    size = max(6, int(np.ceil(max(rows, cols) / CELLS)))
    grid_r, grid_c = int(np.ceil(rows / size)), int(np.ceil(cols / size))
    coarse = np.full((grid_r, grid_c), np.nan)
    for r in range(grid_r):
        for c in range(grid_c):
            block = channel[r * size:(r + 1) * size, c * size:(c + 1) * size]
            values = block[agar[r * size:(r + 1) * size, c * size:(c + 1) * size]]
            if values.size >= max(8, size * size // 8):
                median = np.median(values)
                spread = 1.4826 * np.median(np.abs(values - median)) + 1e-6
                coarse[r, c] = np.median(values[np.abs(values - median) <= 3 * spread])
    known = np.isfinite(coarse)
    if not known.any():
        level = float(np.median(channel[agar])) if agar.any() else float(np.median(channel))
        return np.full(channel.shape, level)
    if not known.all():                                    # fill cells without agar from the nearest that has it
        nearest = ndimage.distance_transform_edt(~known, return_distances=False, return_indices=True)
        coarse = coarse[tuple(nearest)]
    coarse = ndimage.gaussian_filter(coarse, 0.8, mode="nearest")
    surface = ndimage.zoom(coarse, (rows / grid_r, cols / grid_c), order=1, mode="nearest", grid_mode=True)
    return surface[:rows, :cols]


def normalized_channels(lab: np.ndarray, agar: np.ndarray) -> np.ndarray:
    """(rows, cols, 3): each Lab channel minus its local agar level, in units of the agar's noise."""
    out = np.empty_like(lab)
    for k in range(3):
        residual = lab[..., k] - agar_surface(lab[..., k], agar)
        sample = residual[agar]
        noise = 1.4826 * float(np.median(np.abs(sample - np.median(sample)))) if sample.size else 1.0
        out[..., k] = residual / max(noise, 0.5)
    return out


def features(z: np.ndarray) -> np.ndarray:
    """Per pixel: the three normalized channels, a smoothed copy of them, and local texture of L."""
    smooth = np.stack([ndimage.gaussian_filter(z[..., k], 1.5) for k in range(3)], axis=-1)
    mean = ndimage.uniform_filter(z[..., 0], 5)
    texture = np.sqrt(np.maximum(ndimage.uniform_filter(z[..., 0] ** 2, 5) - mean ** 2, 0))
    return np.concatenate([z, smooth, texture[..., None]], axis=-1)


def agar_side(lab: np.ndarray, marked: np.ndarray, growth_labels: np.ndarray, growable: np.ndarray,
              radius: float) -> np.ndarray:
    """Pixels whose color lies on the agar side of the midpoint between agar and growth.

    Agar's color is the median of a thin ring just outside the marked colonies; growth's is the
    median inside them. Each pixel is projected onto the line from agar to growth: 0 at agar,
    1 at growth. Pixels below AGAR_SIDE may sample the agar surface."""
    gap = max(1, int(round(RING_GAP * radius)))
    outer = max(gap + 1, int(round((RING_GAP + RING_WIDTH) * radius)))
    ring = ndimage.binary_dilation(marked, iterations=outer) & ~ndimage.binary_dilation(marked, iterations=gap)
    ring &= growable
    if ring.sum() < 100 or not growth_labels.any():
        return np.ones(lab.shape[:2], dtype=bool)
    agar = np.median(lab[ring], axis=0)
    growth = np.median(lab[growth_labels], axis=0)
    direction = growth - agar
    contrast = float(np.dot(direction, direction))
    if contrast < MIN_CONTRAST ** 2:
        return np.ones(lab.shape[:2], dtype=bool)
    position = ((lab - agar) @ direction) / contrast
    return position < AGAR_SIDE


# ----- classification ---------------------------------------------------------------------
def _sample(mask: np.ndarray, limit: int, rng) -> np.ndarray:
    picks = np.flatnonzero(mask)
    return rng.choice(picks, limit, replace=False) if picks.size > limit else picks


def fit_logistic(x: np.ndarray, y: np.ndarray, l2: float = 1e-2, iterations: int = 25) -> np.ndarray:
    """Weights of a class-balanced, L2-regularized logistic regression (Newton's method). Column 0 of x is 1."""
    weights = np.where(y == 1, 0.5 / max(y.sum(), 1), 0.5 / max((1 - y).sum(), 1)) * len(y)
    beta = np.zeros(x.shape[1])
    penalty = l2 * np.eye(x.shape[1])
    penalty[0, 0] = 0.0                                    # no penalty on the intercept
    for _ in range(iterations):
        p = 1.0 / (1.0 + np.exp(-np.clip(x @ beta, -30, 30)))
        gradient = x.T @ (weights * (p - y)) / len(y) + penalty @ beta
        hessian = (x * (weights * p * (1 - p))[:, None]).T @ x / len(y) + penalty + 1e-9 * np.eye(x.shape[1])
        step = np.linalg.solve(hessian, gradient)
        beta -= step
        if np.max(np.abs(step)) < 1e-6:
            break
    return beta


def classify(feature_stack: np.ndarray, growth_labels: np.ndarray, agar_labels: np.ndarray, seed: int = 0):
    """Growth probability for every pixel from a logistic regression fit on this plate's labels."""
    rng = np.random.default_rng(seed)
    flat = feature_stack.reshape(-1, feature_stack.shape[-1])
    positive, negative = _sample(growth_labels, MAX_TRAINING, rng), _sample(agar_labels, MAX_TRAINING, rng)
    x = np.vstack([flat[positive], flat[negative]])
    center, scale = x.mean(axis=0), x.std(axis=0) + 1e-9
    design = lambda a: np.hstack([np.ones((len(a), 1)), (a - center) / scale])     # noqa: E731
    beta = fit_logistic(design(x), np.concatenate([np.ones(positive.size), np.zeros(negative.size)]))
    logits = np.clip(design(flat) @ beta, -30, 30)
    return (1.0 / (1.0 + np.exp(-logits))).reshape(feature_stack.shape[:2])


def clean(probability: np.ndarray, growable: np.ndarray, colonies: np.ndarray, min_area: int,
          thickness: int = 1) -> np.ndarray:
    """Hysteresis, then: parts less than `thickness` px in radius are dropped, even where they touch
    thicker growth (the dish rim, scratches and streak marks are thin; colonies and lawns are not),
    and so are specks smaller than min_area that touch no marked colony."""
    weak = (probability >= WEAK) & growable
    seeds = ((probability >= STRONG) & growable) | (colonies & weak)
    eight = np.ones((3, 3), dtype=bool)
    kept = ndimage.binary_propagation(seeds & weak, structure=eight, mask=weak)
    if thickness >= 1:                                  # the opening keeps thick parts; a short regrowth restores their edges
        thick = ndimage.binary_opening(kept, structure=morphology.disk(thickness)) | (colonies & kept)
        kept = ndimage.binary_dilation(thick, structure=eight, iterations=2) & kept
    labels = measure.label(kept, connectivity=2)
    if labels.max():
        sizes = np.bincount(labels.ravel())
        touches = np.zeros(sizes.size, dtype=bool)
        touches[np.unique(labels[colonies & kept])] = True
        keep = (sizes >= min_area) | touches
        keep[0] = False
        kept = keep[labels]
    return (kept | colonies) & growable


def drop_wall(growth: np.ndarray, colonies: np.ndarray, region: np.ndarray) -> np.ndarray:
    """Growth without the parts that lie mostly along the region's edge and hold no marked colony."""
    unmarked = growth & ~colonies
    labels = measure.label(unmarked, connectivity=2)
    if not labels.max():
        return growth
    rows, cols = np.nonzero(region)
    size = max(np.ptp(rows), np.ptp(cols)) + 1 if rows.size else 0
    zone = ndimage.distance_transform_edt(region) <= WALL_FRACTION * size
    sizes = np.bincount(labels.ravel())
    inside = np.bincount(labels.ravel(), weights=zone.ravel(), minlength=sizes.size)
    wall = inside / np.maximum(sizes, 1) >= WALL_SHARE
    wall[0] = False
    return growth & ~wall[labels]


def captured_growth(growth: np.ndarray, colonies: ColonySet) -> np.ndarray:
    """Growth pixels of the connected growth pieces (8-connected) that hold exactly one marked colony,
    that colony not split from touching ones. A colony belongs to the piece under its center."""
    if not len(colonies) or not growth.any():
        return np.zeros_like(growth)
    labels, count = ndimage.label(growth, structure=np.ones((3, 3), dtype=bool))
    rows, cols = growth.shape
    x = np.clip(np.round(colonies.centers[:, 0]).astype(int), 0, cols - 1)
    y = np.clip(np.round(colonies.centers[:, 1]).astype(int), 0, rows - 1)
    piece = labels[y, x]
    holds = np.bincount(piece, minlength=count + 1)
    split = np.array([kind == KIND_SPLIT for kind in colonies.kinds], dtype=bool)
    keep = np.zeros(count + 1, dtype=bool)
    keep[piece[(piece > 0) & ~split]] = True
    keep &= holds == 1
    keep[0] = False
    return keep[labels]


# ----- the whole method ------------------------------------------------------------------------
def growth_mask(image_rgb: np.ndarray, region_mask: np.ndarray, colonies: ColonySet,
                exclude: np.ndarray | None = None) -> GrowthResult:
    """Growth on one index image. colonies are in the image's own (crop) coordinates.

    exclude: colonies' pixels to leave unlabeled and out of the final union (held-out checks)."""
    shape = region_mask.shape
    region_mask = region_mask.astype(bool)
    growable = growable_area(region_mask)
    marked = colony_union(shape, colonies) & growable
    split = np.array([kind == KIND_SPLIT for kind in colonies.kinds], dtype=bool)
    separate = colony_union(shape, colonies.subset(~split)) & growable if split.any() else None
    lab = color.rgb2lab(np.ascontiguousarray(image_rgb[..., :3]))
    radius = float(np.median(colonies.radii)) if len(colonies) else 3.0
    near_colony = ndimage.binary_dilation(marked, iterations=max(2, int(round(0.5 * radius))))
    if exclude is not None:
        near_colony |= ndimage.binary_dilation(exclude, iterations=max(2, int(round(0.5 * radius))))
    min_area = max(4, int(np.pi * max(1.0, float(np.min(colonies.radii)) if len(colonies) else 2.0) ** 2 * 0.5))
    small = float(np.percentile(colonies.radii, 10)) if len(colonies) else 3.0
    thickness = max(1, int(np.floor(THIN_FRACTION * small)))
    growth_labels = ndimage.binary_erosion(marked, iterations=1) if marked.sum() > 50 else marked
    candidates = growable & ~near_colony

    if not growth_labels.any():                              # nothing marked: unsupervised fallback
        z = normalized_channels(lab, candidates)
        distance = np.sqrt(np.sum(np.stack([ndimage.gaussian_filter(z[..., k], 1.0) for k in range(3)]) ** 2,
                                  axis=0))
        probability = np.clip((distance - UNSUPERVISED_WEAK) / (UNSUPERVISED_STRONG - UNSUPERVISED_WEAK) * (
            STRONG - WEAK) + WEAK, 0, 1)
        probability = np.where(distance < UNSUPERVISED_WEAK, distance / UNSUPERVISED_WEAK * WEAK, probability)
        growth = drop_wall(clean(probability, growable, marked, max(min_area, 20), thickness), marked, region_mask)
        return GrowthResult(growth, np.where(growable, probability, np.nan), growable, marked, False, separate,
                            captured_growth(growth, colonies))

    plausible = candidates & agar_side(lab, marked, growth_labels, growable, radius)
    if plausible.sum() < 50:                                 # almost nothing looks like agar: keep every candidate
        plausible = candidates
    agar_sample = plausible
    probability = np.zeros(shape)
    for _ in range(PASSES):
        z = normalized_channels(lab, agar_sample)
        agar_labels = agar_sample & (np.abs(z[..., 0]) < AGAR_Z) & (np.abs(z[..., 1]) < AGAR_Z + 1) \
            & (np.abs(z[..., 2]) < AGAR_Z + 1)
        if agar_labels.sum() < 50:                           # almost no agar visible (a lawn): use the faintest pixels
            agar_labels = agar_sample & (z[..., 0] ** 2 <= np.percentile(z[..., 0][agar_sample] ** 2, 20))
        probability = classify(features(z), growth_labels, agar_labels)
        found = clean(probability, growable, marked, min_area, thickness)
        agar_sample = plausible & ~ndimage.binary_dilation(found, iterations=2)
        if agar_sample.sum() < 50:
            agar_sample = plausible
            break
    growth = drop_wall(clean(probability, growable, marked, min_area, thickness), marked, region_mask)
    return GrowthResult(growth, np.where(growable, probability, np.nan), growable, marked, True, separate,
                        captured_growth(growth, colonies))


def held_out_check(image_rgb: np.ndarray, region_mask: np.ndarray, colonies: ColonySet, folds: int = 3,
                   seed: int = 0) -> dict:
    """How well growth is found where it was not taught: the marked colonies are split into folds;
    each fold is left out of the labels, and the share of its pixels found as growth is its recall.

    Also reports how many confident agar pixels (far from every colony, inside the agar's noise)
    were called growth. Needs at least two colonies per fold."""
    if len(colonies) < 2 * folds:
        return {"colony_recall": np.nan, "agar_false_positive": np.nan}
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(colonies))
    shape = region_mask.shape
    growable = growable_area(region_mask.astype(bool))
    found = total = 0
    for fold in range(folds):
        held = np.zeros(len(colonies), dtype=bool)
        held[order[fold::folds]] = True
        held_mask = colony_union(shape, colonies.subset(held)) & growable
        held_mask &= ~colony_union(shape, colonies.subset(~held))          # pixels only the held-out colonies cover
        result = growth_mask(image_rgb, region_mask, colonies.subset(~held), exclude=held_mask)
        found += int((result.growth & held_mask).sum())
        total += int(held_mask.sum())
    full = growth_mask(image_rgb, region_mask, colonies)
    lab = color.rgb2lab(np.ascontiguousarray(image_rgb[..., :3]))
    far = growable & ~ndimage.binary_dilation(full.colonies, iterations=6)
    radius = float(np.median(colonies.radii))
    far &= agar_side(lab, full.colonies, ndimage.binary_erosion(full.colonies), growable, radius)   # lawn is not agar
    z = normalized_channels(lab, far)
    confident_agar = far & np.all(np.abs(z) < 1.5, axis=-1)
    false_positive = float(np.mean(full.growth[confident_agar])) if confident_agar.any() else np.nan
    return {"colony_recall": found / total if total else np.nan, "agar_false_positive": false_positive}
