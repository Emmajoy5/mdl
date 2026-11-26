"""Classical computer-vision analysis of petri-dish plate photographs.

No network calls: everything here is deterministic image processing (OpenCV +
numpy + scipy), so it works offline and gives the same answer every time.

Pipeline:
  0. analyse_plate         - the whole pipeline: pad, find dish, crop to it, analyse
  1. load_square_rgb       - load + center-crop to a square working image (legacy)
  2. detect_dish_circle    - Hough-circle auto-crop to the dish boundary
  3. _dominant_color       - estimate background (agar) color inside the dish
  4. estimate_colony_intensity - per-pixel colony-vs-background signal
                                 (color distance + local texture, grid/writing
                                 filtered out by morphological opening)
  5. extract_morphology_features - coverage / density band / margin description
  6. detect_colonies       - scale-space LoG blob detection for counting
"""
import json

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    from scipy.ndimage import maximum_filter, gaussian_laplace, binary_opening, binary_fill_holes
except ImportError:  # pragma: no cover
    maximum_filter = gaussian_laplace = binary_opening = binary_fill_holes = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


SQUARE_SIZE = 512


def load_square_rgb(path, size=SQUARE_SIZE):
    """Load an image file, convert to RGB, center-crop to a square, resize.
    Kept for callers that want the old behaviour; analyse_plate() pads instead
    of cropping so a dish near the edge of a portrait photo is not cut off."""
    if Image is None:
        return None
    im = Image.open(path)
    try:
        from PIL import ImageOps
        im = ImageOps.exif_transpose(im)
    except Exception:
        pass
    im = im.convert("RGB")
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    im = im.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    return np.asarray(im, dtype=np.uint8)


def _load_padded(path, size=SQUARE_SIZE):
    """Whole photo padded (not cropped) to a square. Returns (small rgb, original
    PIL image, scale original-px per small-px, (pad_x, pad_y) in original px)."""
    im = Image.open(path)
    try:
        # phones may save a photo sideways with a note to turn it; browsers
        # follow the note, so the analysis must too or painted areas and the
        # background marker would land in the wrong place
        from PIL import ImageOps
        im = ImageOps.exif_transpose(im)
    except Exception:
        pass
    im = im.convert("RGB")
    w, h = im.size
    side = max(w, h)
    pad_x, pad_y = (side - w) // 2, (side - h) // 2
    canvas = Image.new("RGB", (side, side), (0, 0, 0))
    canvas.paste(im, (pad_x, pad_y))
    small = np.asarray(canvas.resize((size, size), Image.LANCZOS), dtype=np.uint8)
    return small, im, side / float(size), (pad_x, pad_y)


def detect_dish_circle(rgb):
    """Find the petri-dish boundary with a Hough-circle transform so photo
    background (bench, hands, gloves) outside the dish never pollutes the
    colony signal. Returns (cx, cy, radius) in pixel space, or None.
    Tries progressively looser accumulator thresholds before giving up."""
    if cv2 is None:
        return None
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.medianBlur(gray, 5)
    n = gray.shape[0]
    for param2 in (60, 45, 32):
        circles = cv2.HoughCircles(
            gray, cv2.HOUGH_GRADIENT, dp=1.5, minDist=n,
            param1=80, param2=param2, minRadius=int(n * 0.22), maxRadius=int(n * 0.49))
        if circles is None:
            continue
        circles = np.round(circles[0]).astype(int)
        cx0, cy0 = n / 2, n / 2
        best = min(circles, key=lambda c: (c[0] - cx0) ** 2 + (c[1] - cy0) ** 2)
        return int(best[0]), int(best[1]), int(best[2])
    return None


DISH_MARGIN = 1.02   # crop a little outside the detected edge
EDGE_SHARE_PCT = 30      # agar edge: judged on the 30th percentile of rays ...
EDGE_ON_MOST_RAYS = 0.25  # ... where it must reach this share of the strongest ring
EDGE_STRONG = 0.45        # ... and this share on the median or on that percentile


def _fit_circle(xs, ys):
    """Least-squares circle through points (Kasa fit)."""
    A = np.column_stack([xs, ys, np.ones_like(xs)])
    b = xs * xs + ys * ys
    c, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = c[0] / 2, c[1] / 2
    return cx, cy, float(np.sqrt(c[2] + cx * cx + cy * cy))


def _refine_agar_circle(orig, cx, cy, r, work=768):
    """Find the edge of the agar surface inside a detected dish.

    The Hough circle is usually the outermost rim, which may be the lid or the
    outer glass wall. Rays are cast from the centre; along each the radial
    brightness gradient is measured, and its median over all angles (robust to
    colonies, writing and grids, which do not form rings) gives a profile with a
    peak at every circular edge: lid, wall, agar meniscus. The agar edge is the
    innermost strong peak beyond 55% of the radius that also shows on most rays
    (so the lines of a counting grid are not taken for it). Points of maximum gradient
    near it are then fitted with a circle, which also corrects a centre that is
    off because of perspective. Returns (cx, cy, r) in original-photo pixels."""
    box = (int(cx - 1.08 * r), int(cy - 1.08 * r), int(cx + 1.08 * r), int(cy + 1.08 * r))
    crop = np.asarray(orig.crop(box).convert("L").resize((work, work), Image.BILINEAR), dtype=np.float32)
    valid = np.asarray(orig.crop(box).convert("L").getbbox() is not None)
    k = work / (box[2] - box[0])
    c0, R = (cx - box[0]) * k, r * k
    crop = cv2.GaussianBlur(crop, (0, 0), 1.5)
    n_ang, radii = 360, np.linspace(0.55 * R, 1.04 * R, 200)
    ang = np.linspace(0, 2 * np.pi, n_ang, endpoint=False)
    X = c0 + np.outer(np.cos(ang), radii)
    Y = c0 + np.outer(np.sin(ang), radii)
    inside = (X >= 0) & (X < work - 1) & (Y >= 0) & (Y < work - 1)
    vals = cv2.remap(crop, X.astype(np.float32), Y.astype(np.float32), cv2.INTER_LINEAR)
    vals = np.where(inside, vals, np.nan)
    # black padding outside the photo is not an edge
    pad = cv2.remap((crop > 2).astype(np.float32), X.astype(np.float32), Y.astype(np.float32), cv2.INTER_NEAREST)
    vals = np.where(pad > 0, vals, np.nan)
    grad = np.abs(np.diff(vals, axis=1))
    with np.errstate(all="ignore"):
        prof = np.nanmedian(grad, axis=0)
        wide = np.nanpercentile(grad, EDGE_SHARE_PCT, axis=0)
    if not np.isfinite(prof).any():
        return cx, cy, r
    smooth = lambda p: np.convolve(np.nan_to_num(p), np.ones(3) / 3, mode="same")
    prof, wide = smooth(prof), smooth(wide)
    strong, strong_w = prof.max(), max(wide.max(), 1e-6)
    # A ring counts as an edge when it shows on most rays (the low percentile:
    # a grid or a row of colonies crosses only some of them) and is clearly
    # strong on one of the two measures (growth can hide part of a real edge,
    # which lowers the low percentile but not the median).
    local_max = lambda p, i: p[i] == p[i - 2:i + 3].max()
    peaks = [i for i in range(2, len(prof) - 2)
             if (local_max(prof, i) or local_max(wide, i))
             and wide[i] >= EDGE_ON_MOST_RAYS * strong_w
             and max(prof[i] / strong, wide[i] / strong_w) >= EDGE_STRONG]
    if not peaks:
        return cx, cy, r
    i0 = peaks[0]
    # trace that edge around the plate and fit a circle
    lo, hi = max(0, i0 - 8), min(grad.shape[1], i0 + 9)
    seg = np.nan_to_num(grad[:, lo:hi], nan=-1)
    best = seg.argmax(axis=1) + lo
    ok = seg.max(axis=1) > 0.3 * strong
    if ok.sum() < 60:
        rr = radii[i0]
        fx, fy, fr = c0, c0, rr
    else:
        rr = radii[best[ok]]
        fx, fy, fr = _fit_circle(c0 + np.cos(ang[ok]) * rr, c0 + np.sin(ang[ok]) * rr)
        if not (0.5 * R < fr < 1.05 * R) or np.hypot(fx - c0, fy - c0) > 0.15 * R:
            fx, fy, fr = c0, c0, radii[i0]
    return box[0] + fx / k, box[1] + fy / k, fr / k


def analyse_plate(path, bg_marker=None, size=1024, texture_size=1024, diameter_mm=None,
                  ignore=None):
    """Load a plate photo, find the dish, crop and centre on its agar bed, analyse it.

    Returns dict(rgb, texture, circle, intensity, height, morphology, dish_detected):
      rgb       -- (size, size, 3) crop centred on the agar, for analysis
      texture   -- PIL image of the same crop at texture_size, for the 3D model,
                   with the area outside the agar filled with the agar colour
      circle    -- the plate in rgb's pixel space (centred, radius about size/2)
      height    -- relief_height() of the colony signal, for the 3D relief
    bg_marker is (x, y) as fractions of the original photo (0..1, top-left = 0).
    diameter_mm is the dish size (60 or 90 mm, say); every size rule is in mm.
    ignore is a list of [x, y, r] circles painted over the photo by hand (writing,
    a label, glare), as fractions of the photo's width (x, r) and height (y);
    nothing inside them is read as growth or raised in the 3D model.
    If no dish is found, the centre of the photo is used and dish_detected is False."""
    if Image is None:
        return None
    small, orig, scale, (px, py) = _load_padded(path, size)
    circle = detect_dish_circle(small)
    detected = circle is not None
    w, h = orig.size
    if detected:
        cx = circle[0] * scale - px
        cy = circle[1] * scale - py
        r = circle[2] * scale * DISH_MARGIN
    else:
        cx, cy, r = w / 2.0, h / 2.0, min(w, h) / 2.0
    # The Hough circle is often the lid or the outer glass wall. Narrow the crop
    # to the edge of the agar surface itself (see _refine_agar_circle).
    if cv2 is not None and detected:
        cx, cy, ra = _refine_agar_circle(orig, cx, cy, r / DISH_MARGIN)
        r = ra * DISH_MARGIN
    box = (int(round(cx - r)), int(round(cy - r)), int(round(cx + r)), int(round(cy + r)))
    crop = orig.crop(box)        # areas outside the photo come back black
    rgb = np.asarray(crop.resize((size, size), Image.LANCZOS), dtype=np.uint8)
    texture = crop.resize((texture_size, texture_size), Image.LANCZOS)
    c = (size - 1) / 2.0
    dish = (c, c, size / 2.0 / DISH_MARGIN)
    marker = None
    if bg_marker is not None:
        mx = (float(bg_marker[0]) * w - box[0]) / (box[2] - box[0])
        my = (float(bg_marker[1]) * h - box[1]) / (box[3] - box[1])
        if 0 <= mx <= 1 and 0 <= my <= 1:
            marker = (mx, my)
    diameter_mm = float(diameter_mm or PLATE_DIAMETER_MM)
    intensity, details = estimate_colony_intensity(rgb, circle=dish, bg_marker=marker,
                                                   return_details=True, diameter_mm=diameter_mm)
    ignored = ignore_mask(ignore, (w, h), box, size)
    if ignored is not None:
        intensity = np.where(ignored, 0.0, intensity)
    morph = extract_morphology_features(intensity, circle=dish, mask=details["agar_mask"],
                                        diameter_mm=diameter_mm)
    agar_n = int(details["agar_mask"].sum())
    morph["ignored_pct"] = (round(float((ignored & details["agar_mask"]).sum()) / agar_n * 100.0, 1)
                            if ignored is not None and agar_n else 0.0)
    morph["plate_diameter_mm"] = diameter_mm
    morph["dish_detected"] = detected
    morph["excluded_marks_pct"] = details["excluded_marks_pct"]
    morph["uneven_background"] = bool(details.get("uneven_background", False))
    # Outside the agar bed the photo shows the glass wall and rim; inside a 3D
    # dish that reads as a second dish, so the texture is filled with the agar
    # colour there (feathered, so the edge is not a hard line).
    if cv2 is not None:
        agar_big = cv2.resize(details["agar_mask"].astype(np.float32), (texture_size, texture_size),
                              interpolation=cv2.INTER_LINEAR)
        agar_big = cv2.GaussianBlur(agar_big, (0, 0), texture_size / 200.0)[:, :, None]
        bgc = _dominant_color(rgb, details["agar_mask"]).reshape(1, 1, 3)
        tex = np.asarray(texture, dtype=np.float32)
        texture = Image.fromarray(np.clip(tex * agar_big + bgc * (1 - agar_big), 0, 255).astype(np.uint8))
    return dict(rgb=rgb, texture=texture, circle=dish, intensity=intensity,
                height=relief_height(intensity, dish, diameter_mm=diameter_mm), morphology=morph,
                dish_detected=detected, diameter_mm=diameter_mm)


def ignore_mask(circles, photo_size, box, size):
    """The painted circles as a (size, size) mask over the crop `box` of a photo
    of photo_size (w, h). Returns None when nothing usable is painted."""
    if not circles:
        return None
    w, h = photo_size
    bw, bh = box[2] - box[0], box[3] - box[1]
    if bw <= 0 or bh <= 0:
        return None
    yy, xx = np.mgrid[0:size, 0:size]
    out = np.zeros((size, size), dtype=bool)
    for c in circles:
        try:
            x, y, r = (float(v) for v in c[:3])
        except (TypeError, ValueError):
            continue
        cx = (x * w - box[0]) / bw * size
        cy = (y * h - box[1]) / bh * size
        rr = r * w / bw * size
        if rr <= 0:
            continue
        x0, x1 = int(max(0, cx - rr - 1)), int(min(size, cx + rr + 2))
        y0, y1 = int(max(0, cy - rr - 1)), int(min(size, cy + rr + 2))
        if x0 >= x1 or y0 >= y1:
            continue
        sub = (xx[y0:y1, x0:x1] - cx) ** 2 + (yy[y0:y1, x0:x1] - cy) ** 2 <= rr * rr
        out[y0:y1, x0:x1] |= sub
    return out if out.any() else None


def _dish_mask(shape, circle, margin=0.0):
    n = shape[0]
    yy, xx = np.mgrid[0:n, 0:n]
    if circle is None:
        cx = cy = n / 2
        r = n * 0.46
    else:
        cx, cy, r = circle
        r = r * (1.0 - margin)
    return ((xx - cx) ** 2 + (yy - cy) ** 2) <= r ** 2


def _dominant_color(rgb, mask, bg_marker=None):
    """Estimate the agar/background color. If the user manually marked a
    background point (bg_marker, fractional x/y), sample a small patch around
    it directly -- the most reliable signal when the auto-estimate is fooled
    by a dense/confluent plate. Otherwise use the modal color within the dish,
    which is robust to the glass rim's specular highlight (border sampling is
    not, since the rim is almost never the agar's true color)."""
    n = rgb.shape[0]
    if bg_marker is not None:
        bx = int(np.clip(bg_marker[0], 0, 1) * (n - 1))
        by = int(np.clip(bg_marker[1], 0, 1) * (n - 1))
        r0, r1 = max(0, by - 6), min(n, by + 7)
        c0, c1 = max(0, bx - 6), min(n, bx + 7)
        patch = rgb[r0:r1, c0:c1].reshape(-1, 3)
        if patch.size:
            return patch.mean(axis=0)
    pixels = rgb[mask]
    if pixels.size == 0:
        return np.array([200.0, 200.0, 200.0])
    # coarse-bin modal color (robust to a long tail of colony-colored pixels).
    # On a plate fully covered by a lawn the lawn is the commonest colour and is
    # taken for agar, so the plate reads as empty. That case cannot be told apart
    # from a bare plate by colour alone; the page asks the user to mark a bare
    # agar point (bg_marker) when the result looks wrong.
    bins = (pixels // 16).astype(np.int32)
    keys, counts = np.unique(bins, axis=0, return_counts=True)
    mode_bin = keys[np.argmax(counts)]
    close = np.all(bins == mode_bin, axis=1)
    return pixels[close].mean(axis=0)


def _keep_blob_like(region, min_area, extent_min=0.40, aspect_max=3.0):
    """Connected-component shape filter that tells real colonies apart from
    handwriting, printed lot/expiry text, or a counting grid: a colony --
    even an irregular one -- forms a comparatively compact, well-filled
    blob, while a pen stroke or a line of print forms a thin, sparsely
    filled shape relative to its own bounding box. Returns a boolean mask of
    the same shape keeping only components that look blob-like.

    This can mistakenly drop a genuinely thread-like/filamentous or very
    elongated colony morphology; cheap classical CV has no way to tell that
    case apart from a line of handwriting by shape alone, so on a plate
    with that kind of growth this will under-count the affected colonies
    rather than fold handwriting into the height map."""
    if cv2 is None:
        return region
    region_u8 = region.astype(np.uint8)
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(region_u8, connectivity=8)
    keep = np.zeros_like(region)
    for i in range(1, n_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < min_area:
            continue
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        extent = area / float(w * h)
        aspect = max(w, h) / max(1, min(w, h))
        if extent >= extent_min and aspect <= aspect_max:
            keep |= (labels == i)
    return keep


# Absolute scales (v4.7). Every signal used to be divided by its own maximum in
# the dish, so a blank plate had its camera noise stretched to full scale and read
# as "confluent", and results could not be compared between plates. These fixed
# ranges are in CIELAB units (L* 0..100), so a plate with nothing on it stays near 0.
DELTA_E_FLOOR, DELTA_E_FULL = 6.0, 30.0      # colour difference from the agar
TEXTURE_FLOOR, TEXTURE_FULL = 2.5, 12.0      # local std-dev of L* in a 9x9 window
GROWTH_THRESHOLD = 0.28
THIN_MARK_MM = 1.2     # leftover strokes up to this wide are not growth
MIN_COLONY_MM = 0.45   # smallest colony kept (print dots are about 0.35 mm)
COMPACT_RATIO = 2.6    # area / (pi * r_inscribed^2): ~1 for a disc, large for a stroke
MARK_RATIO = 5.0       # dark marks: a few touching colonies stay under this, pen strokes do not
AGAR_DELTA_E = 14.0    # pixels this close to the agar colour belong to the agar bed
MARK_DARKER_L = 12.0   # writing: at least this much darker than the agar (L*) ...
MARK_CHROMA_DELTA = 8.0  # ... with chroma within this of the agar's
MARK_NEUTRAL_CHROMA = 6.0  # ... or near-neutral (black or grey ink)
PLATE_DIAMETER_MM = 90.0


def _lab(rgb):
    return cv2.cvtColor(rgb.astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB)


def _disk(radius):
    r = max(1, int(round(radius)))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    return (xx * xx + yy * yy) <= r * r


def _agar_mask(lab, bg_lab, dish_mask):
    """The agar bed: the dish circle (already refined to the agar edge by
    analyse_plate), stepped in from the edge where the meniscus sits."""
    r_px = np.sqrt(dish_mask.sum() / np.pi)
    circ = cv2.erode(dish_mask.astype(np.uint8), _disk(max(2, 0.035 * r_px)).astype(np.uint8)).astype(bool)
    return circ if circ.any() else dish_mask


def _rim_arcs(growth, agar, px_per_mm):
    """Growth-like regions that are reflections of the dish wall: they lie mostly
    in the outer band of the agar and are long and thin along it (arcs), while a
    colony at the edge is a compact blob."""
    if not growth.any():
        return np.zeros_like(growth)
    r_px = np.sqrt(agar.sum() / np.pi)
    inner = cv2.erode(agar.astype(np.uint8), _disk(max(2, 0.14 * r_px)).astype(np.uint8)).astype(bool)
    band = agar & ~inner
    g8 = growth.astype(np.uint8)
    dt = cv2.distanceTransform(g8, cv2.DIST_L2, 5)
    n_lab, labels, stats, _ = cv2.connectedComponentsWithStats(g8, connectivity=8)
    out = np.zeros_like(growth)
    for i in range(1, n_lab):
        comp = labels == i
        area = stats[i, cv2.CC_STAT_AREA]
        if (comp & band).sum() < 0.6 * area:
            continue
        r_in = float(dt[comp].max())
        if area / (np.pi * max(r_in, 0.5) ** 2) > 2.0 * COMPACT_RATIO:
            out |= comp
    return out


def _remove_thin_marks(growth, px_per_mm):
    """Keep growth that is thick (a colony) and small compact blobs; drop strokes.

    Thick parts survive an opening with a disk THIN_MARK_MM wide, even where a pen
    stroke touches a colony. What is left over is kept only if it is a compact
    blob (a small colony), judged by area / area of its largest inscribed disc."""
    r_thick = max(1.5, 0.5 * THIN_MARK_MM * px_per_mm)
    g8 = growth.astype(np.uint8)
    thick = cv2.morphologyEx(g8, cv2.MORPH_OPEN, _disk(r_thick).astype(np.uint8))
    keep = growth & cv2.dilate(thick, _disk(2).astype(np.uint8)).astype(bool)
    resid = (growth & ~keep).astype(np.uint8)
    resid = cv2.morphologyEx(resid, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    if resid.any():
        dt = cv2.distanceTransform(resid, cv2.DIST_L2, 5)
        n_lab, labels, stats, _ = cv2.connectedComponentsWithStats(resid, connectivity=8)
        r_min = 0.5 * MIN_COLONY_MM * px_per_mm
        for i in range(1, n_lab):
            comp = labels == i
            area = stats[i, cv2.CC_STAT_AREA]
            r_in = float(dt[comp].max())
            if r_in >= r_min and area / (np.pi * max(r_in, 0.5) ** 2) <= COMPACT_RATIO:
                keep |= comp
    return keep


GLARE_CHROMA_FRACTION = 0.5   # rim glare: under half the agar's chroma ...
GLARE_BAND_SHARE = 0.6        # ... with most of it in the outer band of the agar
INK_DARKER_L = 8.0            # ink left over beside writing: this much darker ...
INK_CHROMA_MARGIN = 2.0       # ... and no more colourful than the agar (plus this)
INK_NEAR_MM = 0.6             # ... and touching found writing within this distance
INK_STROKE_MM = 2.0           # writing counted here: marks at least this big (area of a disc)


def _glare_and_ink(growth, lab, bg_lab, agar, writing, px_per_mm):
    """Growth-like regions that are really glare at the rim or ink beside writing.

    Glare: lies mostly in the outer band of the agar, is much greyer than the
    agar and not much darker (the lid and wall reflect white light). A colony at the edge has the
    colour of the growth, so on a coloured agar it is at least as colourful.
    Ink: dark, no more colourful than the agar, and touching writing that was
    already found. Real colonies on these plates are clearly more colourful than
    the agar (cream, teal); a dark colony away from any writing is not touched."""
    out = np.zeros_like(growth)
    if not growth.any():
        return out
    r_px = np.sqrt(agar.sum() / np.pi)
    inner = cv2.erode(agar.astype(np.uint8), _disk(max(2, 0.14 * r_px)).astype(np.uint8)).astype(bool)
    band = agar & ~inner
    if writing.any():
        # strokes only: single dots (dot-matrix print, or dark colonies in rows
        # that were taken for print) are too small to judge colonies by
        n_w, lab_w, st_w, _ = cv2.connectedComponentsWithStats(writing.astype(np.uint8), connectivity=8)
        big = st_w[:, cv2.CC_STAT_AREA] >= np.pi * (INK_STROKE_MM * px_per_mm / 2) ** 2
        big[0] = False
        writing = big[lab_w]
    near = cv2.dilate(writing.astype(np.uint8),
                      _disk(max(2, INK_NEAR_MM * px_per_mm)).astype(np.uint8)).astype(bool) \
        if writing.any() else np.zeros_like(growth)
    chroma = np.hypot(lab[:, :, 1], lab[:, :, 2])
    bg_chroma = np.hypot(bg_lab[:, :, 1], bg_lab[:, :, 2])
    d_light = lab[:, :, 0] - bg_lab[:, :, 0]
    n_lab, labels, stats, _ = cv2.connectedComponentsWithStats(growth.astype(np.uint8), connectivity=8)
    if n_lab <= 1:
        return out
    idx = labels.ravel()
    cnt = np.maximum(np.bincount(idx, minlength=n_lab), 1)
    mean = lambda arr: np.bincount(idx, weights=arr.ravel(), minlength=n_lab) / cnt
    c_mean, bc_mean, dl_mean = mean(chroma), mean(bg_chroma), mean(d_light)
    in_band = np.bincount(idx, weights=band.ravel().astype(float), minlength=n_lab) / cnt
    touches = np.bincount(idx, weights=near.ravel().astype(float), minlength=n_lab) > 0
    glare = ((in_band >= GLARE_BAND_SHARE) & (bc_mean > 4.0) & (dl_mean > -INK_DARKER_L)
             & (c_mean < GLARE_CHROMA_FRACTION * bc_mean))
    ink = touches & (dl_mean < -INK_DARKER_L) & (c_mean <= bc_mean + INK_CHROMA_MARGIN)
    drop = glare | ink
    drop[0] = False
    return drop[labels]


LINE_MM = 3.0            # straight dark lines at least this long are printed/drawn marks
LOCAL_BG_MM = 15.0       # window for the local background (larger than a colony)
TEX_FLOOR, TEX_FULL = 2.0, 7.0          # sustained texture (L* std-dev), fixed scale
TEX_REGION_DE = 6.0      # a textured region must differ this much from bare agar ...
TEX_STRONG = 8.0         # ... or be this strongly textured (wrinkled lawn)
UNEVEN_BG_DE = 15.0      # background varying more than this behind the agar: warn
MARGIN_SOLIDITY = 0.82   # area-weighted median solidity below this: irregular margins
MARGIN_CLOSE_MM = 0.4    # notches this narrow (a removed grid line) are closed before judging
LOCAL_DE_FLOOR, LOCAL_DE_FULL = 5.0, 22.0


def _local_background(rgb, agar, px_per_mm, fill):
    """Slowly varying background: a median over LOCAL_BG_MM, computed on a 4x
    smaller copy. Follows lighting gradients and whatever shows through clear
    agar (a glove, a bench), while colonies, being smaller than the window, are
    filtered out. Outside the agar the photo is replaced by the agar colour first."""
    n = rgb.shape[0]
    work = rgb.copy()
    work[~agar] = np.asarray(fill, dtype=np.uint8)
    small = cv2.resize(work, (n // 4, n // 4), interpolation=cv2.INTER_AREA)
    k = int(LOCAL_BG_MM * px_per_mm / 4) | 1
    k = max(5, min(k, 255))
    bg = cv2.medianBlur(small, k)
    return cv2.resize(bg, (n, n), interpolation=cv2.INTER_LINEAR)


def _line_marks(mask, px_per_mm):
    """Pixels of `mask` that belong to straight segments at least LINE_MM long,
    at any of 12 orientations: a printed counting grid, ruled lines, long pen
    strokes. A colony (round) never contains such a segment."""
    length = max(7, int(LINE_MM * px_per_mm))
    m8 = mask.astype(np.uint8)
    out = np.zeros_like(m8)
    for ang in range(0, 180, 15):
        k = np.zeros((length, length), np.uint8)
        c = (length - 1) / 2.0
        dx, dy = np.cos(np.radians(ang)), np.sin(np.radians(ang))
        p0 = (int(round(c - dx * c)), int(round(c - dy * c)))
        p1 = (int(round(c + dx * c)), int(round(c + dy * c)))
        cv2.line(k, p0, p1, 1, 1)
        out |= cv2.morphologyEx(m8, cv2.MORPH_OPEN, k)
    # a line is long AND thin: anything that also survives an opening with a
    # 0.8 mm disc is a blob (a colony contains long segments too) and is kept
    thick = cv2.morphologyEx(m8, cv2.MORPH_OPEN, _disk(max(1.5, 0.4 * px_per_mm)).astype(np.uint8))
    return out.astype(bool) & ~thick.astype(bool)


def estimate_colony_intensity(rgb, circle=None, bg_marker=None, return_details=False,
                              diameter_mm=None):
    """Return an (n,n) float array in [0,1]: how 'colony-like' each pixel is.

    v4.7 pipeline, all on fixed (absolute) scales so plates are comparable and a
    blank plate stays near 0:
      1. the agar bed (glass wall and rim excluded) and a LOCAL background, so a
         glove or bench seen through clear agar, or uneven light, is not growth
      2. marks are found first and excluded: straight dark lines at least
         LINE_MM long (a printed grid), and dark, low-chroma regions that are
         not compact blobs (marker or print seen through the agar)
      3. colour: CIELAB delta-E from the local background
      4. texture: the local std-dev of lightness, averaged over about 2 mm, which
         picks up translucent colonies whose colour is close to the agar's, and
         wrinkled or granular lawns whose colour IS the background
      5. leftover strokes narrower than THIN_MARK_MM are removed; compact blobs at
         least MIN_COLONY_MM across are kept
    With return_details=True also returns dict(excluded_marks_pct, agar_mask).
    """
    n = rgb.shape[0]
    mask = _dish_mask(rgb.shape, circle, margin=0.03)
    gbg = _dominant_color(rgb, mask, bg_marker=bg_marker)
    if cv2 is None:
        dist = np.linalg.norm(rgb.astype(np.float32) - gbg[None, None, :], axis=2) / 2.55
        signal = np.clip((dist - DELTA_E_FLOOR) / (DELTA_E_FULL - DELTA_E_FLOOR), 0, 1)
        signal = np.where(mask, signal, 0.0)
        return (signal, dict(excluded_marks_pct=0.0, agar_mask=mask)) if return_details else signal

    lab = _lab(rgb)
    gbg_lab = _lab(np.asarray(gbg, dtype=np.float32).reshape(1, 1, 3).clip(0, 255))[0, 0]
    agar = _agar_mask(lab, gbg_lab, mask)
    dish_r = circle[2] if circle is not None else n * 0.46
    px_per_mm = (2.0 * dish_r) / float(diameter_mm or PLATE_DIAMETER_MM)

    if bg_marker is not None:
        # the operator marked bare agar: one colour for the whole plate
        bg_lab = np.broadcast_to(gbg_lab, lab.shape).astype(np.float32)
    else:
        bg_lab = _lab(_local_background(rgb, agar, px_per_mm, gbg))

    # --- marks -------------------------------------------------------------
    d_light = lab[:, :, 0] - bg_lab[:, :, 0]
    chroma = np.hypot(lab[:, :, 1], lab[:, :, 2])
    bg_chroma = np.hypot(bg_lab[:, :, 1], bg_lab[:, :, 2])
    dark = (d_light < -8.0) & agar
    lines = _line_marks(dark, px_per_mm)
    # judged after the lines are taken out: a printed grid is one connected dark
    # network, and every colony touching it would otherwise be judged with it
    lines_wide = cv2.dilate(lines.astype(np.uint8), _disk(2).astype(np.uint8)).astype(bool)
    # ink is either the agar's own colour darkened (writing seen through the agar)
    # or near-neutral grey/black (marker or print on a pale medium)
    mark_like = (dark & ~lines_wide & (d_light < -MARK_DARKER_L)
                 & ((np.abs(chroma - bg_chroma) < MARK_CHROMA_DELTA) | (chroma < MARK_NEUTRAL_CHROMA)))
    mark_raw = mark_like.astype(np.uint8)
    mark_like = cv2.morphologyEx(mark_raw, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    # the dark rim of a colony is a ring; filled, it is a disc and passes as compact
    if binary_fill_holes is not None and mark_like.any():
        mark_like = binary_fill_holes(mark_like).astype(np.uint8)
    marks = lines.copy()
    def _shape(comp, dtm, area):
        r_in = float(dtm[comp].max())
        ratio = area / (np.pi * max(r_in, 0.5) ** 2)
        pts = cv2.findNonZero(comp.astype(np.uint8))
        hull = cv2.contourArea(cv2.convexHull(pts)) if pts is not None and len(pts) > 2 else area
        return r_in, ratio, area / max(hull, 1.0)

    if mark_like.any():
        # dark, low-chroma regions (writing seen through the agar, print): a mark
        # if long (ratio above MARK_RATIO), or elongated and poorly filled (letters,
        # loops: solidity under 0.7), or a tiny dot (dot-matrix print). A few
        # touching colonies stay compact and well filled.
        dtm = cv2.distanceTransform(mark_like, cv2.DIST_L2, 5)
        n_lab, labels, stats, centroids = cv2.connectedComponentsWithStats(mark_like, connectivity=8)
        dot_r = 0.22 * px_per_mm
        small_r = 0.45 * px_per_mm
        dots = []
        for i in range(1, n_lab):
            comp = labels == i
            r_in, ratio, solidity = _shape(comp, dtm, stats[i, cv2.CC_STAT_AREA])
            if ratio > MARK_RATIO or (ratio > COMPACT_RATIO and solidity < 0.7) or r_in < dot_r:
                marks |= comp
            elif r_in < small_r:
                dots.append((i, centroids[i]))
        # dot-matrix print: small dark dots in rows, each with two or more others
        # within 1 mm. Colonies that close would touch and merge.
        if len(dots) >= 3:
            cen = np.array([c for _, c in dots])
            d = np.hypot(cen[:, None, 0] - cen[None, :, 0], cen[:, None, 1] - cen[None, :, 1])
            close = ((d < 1.0 * px_per_mm).sum(axis=1) - 1) >= 2
            for (i, _), c in zip(dots, close):
                if c:
                    marks |= labels == i
    # any other dark region that is long and thin (a glove fold or shadow under
    # clear agar, a pen line of another colour)
    other_dark = (dark & ~lines_wide & ~marks).astype(np.uint8)
    other_dark = cv2.morphologyEx(other_dark, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    if binary_fill_holes is not None and other_dark.any():
        other_dark = binary_fill_holes(other_dark).astype(np.uint8)
    if other_dark.any():
        dto = cv2.distanceTransform(other_dark, cv2.DIST_L2, 5)
        n_lab, labels, stats, _ = cv2.connectedComponentsWithStats(other_dark, connectivity=8)
        for i in range(1, n_lab):
            comp = labels == i
            r_in, ratio, solidity = _shape(comp, dto, stats[i, cv2.CC_STAT_AREA])
            if ratio > 1.5 * MARK_RATIO or (ratio > MARK_RATIO and solidity < 0.6):
                marks |= comp
    # texture is measured with the marks (and their texture halo) filled in; the
    # signal itself is only suppressed on the marks plus a thin edge, so a colony
    # touching a grid line keeps its body
    halo_r = max(2, int(round(0.45 * px_per_mm)))
    marks_halo = cv2.dilate(marks.astype(np.uint8), _disk(halo_r).astype(np.uint8)).astype(bool)
    marks_edge = cv2.dilate(marks.astype(np.uint8), _disk(2).astype(np.uint8)).astype(bool)

    # --- colour and texture -------------------------------------------------
    delta_e = np.linalg.norm(lab - bg_lab, axis=2)
    colour = np.clip((delta_e - LOCAL_DE_FLOOR) / (LOCAL_DE_FULL - LOCAL_DE_FLOOR), 0, 1)
    L = lab[:, :, 0].copy()
    # marks are filled with the local background before measuring texture, so a
    # grid or a pen line does not make the agar around it look textured
    L[marks_halo] = bg_lab[:, :, 0][marks_halo]
    win = max(3, int(round(0.8 * px_per_mm)) | 1)
    mean = cv2.blur(L, (win, win))
    sd = np.sqrt(np.clip(cv2.blur(L * L, (win, win)) - mean * mean, 0, None))
    avg = max(3, int(round(1.0 * px_per_mm)) | 1)
    sustained = cv2.blur(sd, (avg, avg))
    texture = np.clip((sustained - TEX_FLOOR) / (TEX_FULL - TEX_FLOOR), 0, 1)

    # A textured region counts as growth only if, apart from its dark specks, it is
    # not plain agar: its colour differs from the bare-agar colour, or its texture
    # is strong (a wrinkled lawn). Print and pen dots on agar make texture too,
    # but the agar between them is agar-coloured.
    tex_reg = (texture > GROWTH_THRESHOLD) & agar
    if tex_reg.any():
        de_global = np.linalg.norm(lab - gbg_lab[None, None, :], axis=2)
        not_dark = ~dark
        n_lab, labels, _, _ = cv2.connectedComponentsWithStats(tex_reg.astype(np.uint8), connectivity=8)
        cnt = np.bincount(labels[not_dark], minlength=n_lab).astype(float)
        de_mean = np.bincount(labels[not_dark], weights=de_global[not_dark], minlength=n_lab) / np.maximum(cnt, 1)
        sd_mean = (np.bincount(labels.ravel(), weights=sustained.ravel(), minlength=n_lab)
                   / np.maximum(np.bincount(labels.ravel(), minlength=n_lab), 1))
        ok = (de_mean > TEX_REGION_DE) | (sd_mean > TEX_STRONG)
        ok[0] = False
        texture = np.where(ok[labels], texture, 0.0)
    # near marks, only colour counts (texture there is the mark's own edge)
    signal = np.where(marks_halo, colour, np.maximum(colour, texture))
    signal = np.where(agar & ~marks_edge, signal, np.where(agar, signal * 0.1, 0.0))

    # --- leftover strokes ---------------------------------------------------
    growth = signal > GROWTH_THRESHOLD
    # a translucent colony is found by its rim and highlight; fill its interior
    if binary_fill_holes is not None:
        filled = binary_fill_holes(growth) & agar & ~marks_edge
        holes = filled & ~growth
        small = cv2.connectedComponentsWithStats(holes.astype(np.uint8), connectivity=8)
        cap = np.pi * (4.0 * px_per_mm) ** 2       # only holes the size of a colony
        for i in range(1, small[0]):
            if small[2][i, cv2.CC_STAT_AREA] <= cap:
                comp = small[1] == i
                signal = np.where(comp, np.maximum(signal, GROWTH_THRESHOLD + 0.05), signal)
                growth |= comp
    keep = _remove_thin_marks(growth, px_per_mm)
    # Thin print: judged on the colour signal alone, because the texture window
    # widens every stroke by a halo. A region whose colour core is all thin
    # strokes goes; texture-only regions (lawns) are not affected.
    colour_core = (colour > GROWTH_THRESHOLD) & agar & ~marks_edge
    core_keep = _remove_thin_marks(colour_core, px_per_mm)
    n_lab, labels, _, _ = cv2.connectedComponentsWithStats(growth.astype(np.uint8), connectivity=8)
    if n_lab > 1:
        has_core = np.bincount(labels[colour_core], minlength=n_lab) > 0
        has_kept = np.bincount(labels[core_keep], minlength=n_lab) > 0
        drop = has_core & ~has_kept
        drop[0] = False
        keep &= ~drop[labels]
    thin = (growth & ~keep) | _rim_arcs(growth & keep, agar, px_per_mm)
    # fragments left on a mark (grid crossings, the edge of a stroke) are not colonies
    rest = (growth & ~thin).astype(np.uint8)
    if rest.any() and marks_halo.any():
        dtr = cv2.distanceTransform(rest, cv2.DIST_L2, 5)
        n_lab, labels, stats, _ = cv2.connectedComponentsWithStats(rest, connectivity=8)
        body_r = 0.35 * px_per_mm
        for i in range(1, n_lab):
            comp = labels == i
            if (comp & marks_halo).any() and float(dtr[comp].max()) < body_r:
                thin |= comp
    thin |= _glare_and_ink(growth & ~thin, lab, bg_lab, agar, marks & ~lines, px_per_mm)
    signal = np.where(thin, signal * 0.1, signal)
    excluded = round(float((thin | (marks_edge & agar))[agar].mean() * 100.0), 1) if agar.any() else 0.0
    if return_details:
        # how much the background behind the agar varies (a hand, a glove, folds)
        bg_spread = float(np.percentile(np.linalg.norm(bg_lab - gbg_lab[None, None, :], axis=2)[agar], 90)) \
            if agar.any() else 0.0
        return signal, dict(excluded_marks_pct=excluded, agar_mask=agar,
                            uneven_background=bg_spread > UNEVEN_BG_DE,
                            marks=marks, px_per_mm=px_per_mm)
    return signal


def relief_height(intensity, circle=None, diameter_mm=None):
    """A height map for the 3D relief: each colony becomes a dome whose height
    grows with its width (square root of the distance to its edge), smoothed.
    The photo gives no height, so the shape is drawn, not measured; using
    contrast directly gave flat-topped "mesas" wherever the colour saturated."""
    growth = (intensity > GROWTH_THRESHOLD).astype(np.uint8)
    if cv2 is None or not growth.any():
        return np.zeros_like(intensity)
    n = intensity.shape[0]
    dish_r = circle[2] if circle is not None else n * 0.46
    px_per_mm = (2.0 * dish_r) / float(diameter_mm or PLATE_DIAMETER_MM)
    dt = cv2.distanceTransform(growth, cv2.DIST_L2, 5)
    h = np.sqrt(np.clip(dt / (2.5 * px_per_mm), 0, 1))     # colonies 5 mm across reach full height
    h = cv2.GaussianBlur(h.astype(np.float32), (0, 0), 1.2)
    return np.clip(h, 0, 1)


def extract_morphology_features(intensity, circle=None, mask=None, diameter_mm=None):
    """Coverage, a density band and a margin description from the intensity map.

    density_desc is a band of the coverage percentage only; it says nothing about
    colony height (the old key elevation_desc is kept with the same text so older
    pages still show something). margin_desc is judged on colony-sized
    components only, so stray specks and leftover marks do not decide it."""
    if mask is None:
        mask = _dish_mask(intensity.shape, circle, margin=0.03)
    dish_px = intensity[mask]
    if dish_px.size == 0:
        return dict(coverage_pct=0.0, density_desc="unknown", elevation_desc="unknown",
                    margin_desc="unknown")

    growth = (intensity > GROWTH_THRESHOLD) & mask
    coverage_pct = float(growth[mask].mean() * 100.0)

    if coverage_pct > 60:
        density = "confluent growth (individual colonies not distinguishable)"
    elif coverage_pct > 25:
        density = "dense growth across much of the plate"
    elif coverage_pct > 3:
        density = "discrete colonies"
    elif coverage_pct > 0.2:
        density = "sparse growth, mostly bare agar"
    else:
        density = "no growth detected (if the plate is covered by a lawn, mark a bare-agar point)"

    margin_desc = "unknown"
    if cv2 is not None and growth.any():
        n = intensity.shape[0]
        dish_r = circle[2] if circle is not None else n * 0.46
        px_per_mm = (2.0 * dish_r) / float(diameter_mm or PLATE_DIAMETER_MM)
        min_area = np.pi * (1.0 * px_per_mm) ** 2       # colonies >= 2 mm across
        lawn_area = 0.05 * mask.sum()
        g = growth.astype(np.uint8)
        # Close notches as wide as a grid line or pen stroke first: where a removed
        # mark crosses a round colony it leaves a cut that is not part of its margin.
        k = max(1, int(round(MARGIN_CLOSE_MM * px_per_mm)))
        g = cv2.morphologyEx(g, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
        g = cv2.morphologyEx(g, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        edge = mask & ~cv2.erode(mask.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
        contours, _ = cv2.findContours(g, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        sols, areas = [], []
        for c in contours:
            area = cv2.contourArea(c)
            if area < min_area:
                continue
            comp = np.zeros_like(g)
            cv2.drawContours(comp, [c], -1, 1, thickness=-1)
            if (comp.astype(bool) & edge).any() and area < lawn_area:
                continue          # cut off by the edge of the agar: shape unknown
            solidity = area / (cv2.contourArea(cv2.convexHull(c)) or 1.0)
            # Solidity (area / convex-hull area) rather than 4*pi*A/P^2: a pixel
            # outline's perimeter is inflated by its stair-steps, so round colonies
            # scored as irregular. Undulate and lobate margins leave concavities;
            # clusters of touching round colonies stay above about 0.83 (measured on the
            # test photographs: round 0.84-0.99, lobed and wrinkled 0.79-0.81). Ragged leftovers of
            # marks (very low solidity, small) are not colonies and are skipped.
            if solidity < 0.6 and area < lawn_area:
                continue
            sols.append(solidity)
            areas.append(area)
        if sols:
            order = np.argsort(sols)
            cum = np.cumsum(np.asarray(areas)[order])
            median = np.asarray(sols)[order][np.searchsorted(cum, cum[-1] / 2.0)]
            margin_desc = ("irregular / undulate margins" if median < MARGIN_SOLIDITY
                           else "entire / circular margins")

    return dict(coverage_pct=round(coverage_pct, 1), density_desc=density,
                elevation_desc=density, margin_desc=margin_desc)


# --------------------------------------------------------------------------- #
# Colony counting: scale-space Laplacian-of-Gaussian blob detection
# --------------------------------------------------------------------------- #
def detect_colonies(rgb, diameter_mm=90.0, circle=None, bg_marker=None,
                    sigmas=(6.5, 8.0, 10.0, 12.5, 15.0),
                    abs_threshold=0.08, rel_threshold=0.42, rim_margin=0.89):
    """Count and size individual colonies directly from the photo using
    scale-space LoG blob detection (not the smoothed relief/intensity map,
    which over-merges adjacent colonies).

    Returns a dict with count, colonies (list of {x_frac,y_frac,diameter_mm}),
    median_diameter_mm, confidence (0..1), reliable (bool), and a short note
    -- or None if scipy is unavailable. On a confluent/dense plate, individual
    colonies can't be reliably separated, so confidence is capped low and the
    note says so explicitly rather than returning a confident-looking number.
    """
    if gaussian_laplace is None or maximum_filter is None or cv2 is None:
        return None

    n = rgb.shape[0]
    mask = _dish_mask(rgb.shape, circle, margin=1.0 - rim_margin)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float64) / 255.0

    intensity = estimate_colony_intensity(rgb, circle=circle, bg_marker=bg_marker,
                                          diameter_mm=diameter_mm)
    coverage_hint = float(intensity[mask].mean()) if mask.any() else 0.0
    colony_region = intensity > 0.28

    # LoG alone responds to any dark-blob-shaped structure in the raw photo --
    # including handwriting loops, printed lot-code characters, and grid-line
    # junctions, none of which are biological growth, and a single-pixel gate
    # against colony_region is too strict in the other direction: a real
    # colony's own LoG-detected center can land on a specular highlight (a
    # camera-flash reflection off glossy growth), which briefly reads close to
    # the agar background color at that exact pixel even though the colony
    # all around it is unambiguous. So artifacts are screened out by *shape*
    # instead of by a single pixel: handwriting and grid lines form thin,
    # sparsely-filled connected components (low area-to-bounding-box ratio),
    # while colonies -- even irregular ones -- form comparatively compact,
    # well-filled blobs. Only components passing that shape test survive,
    # and candidate colony blobs are then matched against this cleaned,
    # physical-size-aware region (slightly dilated, so a peak a few pixels
    # from a kept component's edge due to the glare above still counts).
    px_per_mm = n / diameter_mm
    min_colony_area = np.pi * (0.5 * 1.2 * px_per_mm) ** 2  # ~1.2 mm minimum countable colony
    region_u8 = colony_region.astype(np.uint8)
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(region_u8, connectivity=8)
    shape_ok = np.zeros_like(colony_region)
    for i in range(1, n_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < min_colony_area:
            continue
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        extent = area / float(w * h)        # filled fraction of the bounding box
        aspect = max(w, h) / max(1, min(w, h))
        if extent >= 0.40 and aspect <= 2.8:  # compact & roundish; rejects thin ink strokes
            shape_ok |= (labels == i)
    if binary_opening is not None:
        from scipy.ndimage import binary_dilation
        colony_region_clean = binary_dilation(shape_ok, structure=np.ones((5, 5)))
    else:
        colony_region_clean = shape_ok

    # detected_area backs the confidence ratio below, so it must count the same
    # shape-filtered growth area the counting step actually searches -- using
    # the raw, unfiltered colony_region here would let leftover ink/grid pixels
    # (correctly excluded from the count) still drag confidence down.
    detected_area = float(shape_ok[mask].sum()) if mask.any() else 0.0

    # Multi-scale LoG response stack (scipy's gaussian_laplace already sign-flips,
    # so a bright-or-dark blob both give a strong negative/positive response
    # depending on contrast direction; we use the signed response consistently).
    stack = []
    for s in sigmas:
        resp = -gaussian_laplace(gray, sigma=s) * (s ** 2)
        stack.append(resp)
    stack = np.stack(stack, axis=0)

    candidates = []
    for si, s in enumerate(sigmas):
        layer = stack[si]
        local_max = maximum_filter(layer, size=max(3, int(s)))
        peak_mask = (layer == local_max) & mask & (layer > abs_threshold) & colony_region_clean
        ys, xs = np.nonzero(peak_mask)
        for y, x in zip(ys, xs):
            val = layer[y, x]
            candidates.append((val, x, y, s))

    if not candidates:
        return dict(count=0, colonies=[], median_diameter_mm=None,
                   confidence=0.15 if coverage_hint < 0.05 else 0.2,
                   reliable=False,
                   note="No distinct colonies detected; plate may be blank or too low-contrast to analyze.")

    candidates.sort(key=lambda c: -c[0])
    max_val = candidates[0][0]
    accepted = []
    for val, x, y, s in candidates:
        if val < rel_threshold * max_val:
            continue
        r = s * np.sqrt(2)
        too_close = False
        for (ax, ay, ar) in accepted:
            d = np.hypot(x - ax, y - ay)
            if d < 0.85 * max(r, ar):
                too_close = True
                break
        if not too_close:
            accepted.append((x, y, r))

    px_per_mm = n / diameter_mm
    colonies = []
    for x, y, r in accepted:
        colonies.append(dict(
            x_frac=round(x / n, 4),
            y_frac=round(y / n, 4),
            diameter_mm=round((2 * r) / px_per_mm, 2),
        ))

    count = len(colonies)
    diam_list = sorted(c["diameter_mm"] for c in colonies)
    median_d = diam_list[len(diam_list) // 2] if diam_list else None

    explained_area = sum(np.pi * (c["diameter_mm"] * px_per_mm / 2) ** 2 for c in colonies)
    coverage_ratio = (explained_area / detected_area) if detected_area > 0 else 0.0
    confidence = float(np.clip(coverage_ratio, 0.0, 1.0))

    reliable = True
    note = f"Detected {count} colonies."
    if coverage_hint > 0.5:
        confidence = min(confidence, 0.25)
        reliable = False
        note = ("Growth looks confluent/overlapping across much of the plate. "
               "Individual colonies can't be reliably counted here. Treat this "
               "count as a rough lower bound, not a true tally.")
    elif count == 0:
        reliable = False
        note = "No distinct colonies detected; plate may be blank or too low-contrast to analyze."
    elif confidence < 0.35:
        reliable = False
        note = f"Detected {count} candidate colonies, but with low confidence, so it is worth a visual double-check."
    # touching or overlapping colonies cannot be split reliably by this method
    if reliable and count > 1:
        pts = [(x, y, r) for x, y, r in accepted]
        touching = any(np.hypot(a[0] - b[0], a[1] - b[1]) < 1.1 * (a[2] + b[2])
                       for k, a in enumerate(pts) for b in pts[k + 1:])
        if touching:
            reliable = False
            confidence = min(confidence, 0.5)
            note = (f"Detected {count} colonies, but some touch or overlap, so the count may be "
                    f"off by several. Count by hand.")

    return dict(count=count, colonies=colonies, median_diameter_mm=median_d,
               confidence=round(confidence, 2), reliable=reliable, note=note)
