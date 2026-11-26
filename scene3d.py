"""Display-quality 3D scenes for the in-app viewer (GLB only).

The STL files stay as they were (a single watertight, printable relief). The
GLB that the viewer shows is a fuller picture of a plate: a glass Petri dish,
a glossy agar bed, and colonies built from many small glossy beads, domes or
branching arms, depending on how the strain grows (see shape_config.py).
Colours are illustrative, as before, not a claim about a specific strain.

All geometry is built in millimetres with Z up, then written to the GLB with
Y up as the glTF format requires, so the dish lies flat in the viewer.
"""
import json
import struct
from pathlib import Path

import numpy as np

# Dish geometry (mm). A standard 90 mm plate.
DISH_R_OUT = 46.0
DISH_WALL = 1.4
DISH_H = 15.0
DISH_FLOOR = 1.5
AGAR_R = DISH_R_OUT - DISH_WALL - 0.1     # agar fills the dish to the inner wall
AGAR_DEPTH = 5.0



class dish_size:
    """Build a scene for another dish size: `with dish_size(60): ...`. The 90 mm
    figures above are the defaults; wall, height and agar depth stay the same."""

    def __init__(self, diameter_mm):
        self.d = float(diameter_mm or 90.0)

    def __enter__(self):
        global DISH_R_OUT, AGAR_R
        self._old = (DISH_R_OUT, AGAR_R)
        DISH_R_OUT = self.d / 2.0 + 1.0
        AGAR_R = DISH_R_OUT - DISH_WALL - 0.1
        return self

    def __exit__(self, *exc):
        global DISH_R_OUT, AGAR_R
        DISH_R_OUT, AGAR_R = self._old
        return False


AGAR_COLOR = (0.76, 0.58, 0.30)           # sRGB, a golden medium such as MRS or YPD (illustrative)
GLASS_COLOR = (0.90, 0.95, 0.98)


# --------------------------------------------------------------------------- #
# Colour and mesh helpers
# --------------------------------------------------------------------------- #
def _lin(c):
    """sRGB 0..1 to linear, because glTF colours are linear."""
    return np.power(np.clip(np.asarray(c, dtype=np.float64), 0.0, 1.0), 2.2)


class Part:
    """One drawable piece: arrays plus a material."""

    def __init__(self, name, pos, nrm, idx, colors=None, uvs=None, material=None, texture_path=None):
        self.name = name
        self.pos = np.asarray(pos, dtype=np.float32)
        self.nrm = np.asarray(nrm, dtype=np.float32)
        self.idx = np.asarray(idx, dtype=np.uint32).reshape(-1)
        self.colors = None if colors is None else np.asarray(colors, dtype=np.float32)
        self.uvs = None if uvs is None else np.asarray(uvs, dtype=np.float32)
        self.material = material or {}
        self.texture_path = texture_path


def _concat(chunks):
    """chunks: list of (pos, nrm, colors|None, idx). Returns merged arrays."""
    pos, nrm, col, idx = [], [], [], []
    base = 0
    has_col = any(c[2] is not None for c in chunks)
    for p, n, c, i in chunks:
        pos.append(p)
        nrm.append(n)
        if has_col:
            col.append(c if c is not None else np.ones((len(p), 3)))
        idx.append(i + base)
        base += len(p)
    return (np.vstack(pos), np.vstack(nrm), np.vstack(col) if has_col else None,
            np.concatenate(idx).reshape(-1, 3))


_SPHERE_CACHE = {}


def _unit_sphere(n_lat, n_lon):
    key = (n_lat, n_lon)
    if key in _SPHERE_CACHE:
        return _SPHERE_CACHE[key]
    lat = np.linspace(0.0, np.pi, n_lat + 1)
    lon = np.linspace(0.0, 2 * np.pi, n_lon, endpoint=False)
    La, Lo = np.meshgrid(lat, lon, indexing="ij")
    x = np.sin(La) * np.cos(Lo)
    y = np.sin(La) * np.sin(Lo)
    z = np.cos(La)
    v = np.stack([x, y, z], axis=-1).reshape(-1, 3)
    faces = []
    for i in range(n_lat):
        for j in range(n_lon):
            a = i * n_lon + j
            b = i * n_lon + (j + 1) % n_lon
            c = (i + 1) * n_lon + (j + 1) % n_lon
            d = (i + 1) * n_lon + j
            if i != 0:
                faces.append([a, c, b])
            if i != n_lat - 1:
                faces.append([a, d, c])
    f = np.asarray(faces, dtype=np.int64)
    _SPHERE_CACHE[key] = (v, f)
    return v, f


def _ellipsoids(centers, radii, colors, ao_floor, n_lat=8, n_lon=12, wobble=None):
    """Many ellipsoids in one go. centers (k,3); radii (k,3); colors (k,3) linear.
    ao_floor: z of the agar surface, used to darken the underside of each bead
    so it looks seated on the agar rather than floating."""
    centers = np.asarray(centers, dtype=np.float64)
    radii = np.asarray(radii, dtype=np.float64)
    colors = np.asarray(colors, dtype=np.float64)
    u, f = _unit_sphere(n_lat, n_lon)
    k = len(centers)
    nv = len(u)
    pos = centers[:, None, :] + u[None, :, :] * radii[:, None, :]
    nrm = u[None, :, :] / radii[:, None, :]
    nrm = nrm / np.linalg.norm(nrm, axis=2, keepdims=True)
    # fake contact shadow: darker where a vertex is close to the agar
    height_above = (pos[:, :, 2] - ao_floor) / np.maximum(radii[:, 2:3] * 1.4, 1e-6)
    ao = 0.62 + 0.38 * np.clip(height_above, 0.0, 1.0)
    col = colors[:, None, :] * ao[:, :, None]
    faces = (f[None, :, :] + (np.arange(k) * nv)[:, None, None]).reshape(-1, 3)
    return pos.reshape(-1, 3), nrm.reshape(-1, 3), col.reshape(-1, 3), faces


def _grid_surface(radius, height_fn, n_r=28, n_t=96, color_fn=None):
    """Top surface of a disc: polar grid, height z = height_fn(r)."""
    rs = np.linspace(0.0, radius, n_r + 1)
    ts = np.linspace(0.0, 2 * np.pi, n_t, endpoint=False)
    R, T = np.meshgrid(rs, ts, indexing="ij")
    z = height_fn(R)
    pos = np.stack([R * np.cos(T), R * np.sin(T), z], axis=-1).reshape(-1, 3)
    # normals from the radial slope
    eps = 1e-3
    dz = (height_fn(R + eps) - height_fn(np.maximum(R - eps, 0))) / (R + eps - np.maximum(R - eps, 0))
    nx = -dz * np.cos(T)
    ny = -dz * np.sin(T)
    nz = np.ones_like(R)
    nrm = np.stack([nx, ny, nz], axis=-1)
    nrm = (nrm / np.linalg.norm(nrm, axis=-1, keepdims=True)).reshape(-1, 3)
    faces = []
    for i in range(n_r):
        for j in range(n_t):
            a = i * n_t + j
            b = i * n_t + (j + 1) % n_t
            c = (i + 1) * n_t + (j + 1) % n_t
            d = (i + 1) * n_t + j
            faces.append([a, c, b])
            faces.append([a, d, c])
    col = None
    if color_fn is not None:
        col = color_fn(R.reshape(-1))
    return pos, nrm, col, np.asarray(faces, dtype=np.int64)


def _cylinder_side(radius, z0, z1, n_t=96):
    ts = np.linspace(0.0, 2 * np.pi, n_t, endpoint=False)
    top = np.stack([radius * np.cos(ts), radius * np.sin(ts), np.full(n_t, z1)], axis=-1)
    bot = np.stack([radius * np.cos(ts), radius * np.sin(ts), np.full(n_t, z0)], axis=-1)
    pos = np.vstack([top, bot])
    nrm = np.vstack([np.stack([np.cos(ts), np.sin(ts), np.zeros(n_t)], axis=-1)] * 2)
    faces = []
    for j in range(n_t):
        a, b = j, (j + 1) % n_t
        faces.append([a, n_t + a, n_t + b])
        faces.append([a, n_t + b, b])
    return pos, nrm, np.asarray(faces, dtype=np.int64)


def _revolve(profile, n_t=120):
    """Revolve a closed (r, z) polyline that does not touch the axis. Smooth
    normals come from the profile's own tangent, so corners stay crisp where
    the profile has a deliberate corner and smooth where it is filleted."""
    prof = np.asarray(profile, dtype=np.float64)
    m = len(prof)
    ts = np.linspace(0.0, 2 * np.pi, n_t, endpoint=False)
    tang = np.zeros_like(prof)
    tang[1:-1] = prof[2:] - prof[:-2]
    tang[0] = prof[1] - prof[0]
    tang[-1] = prof[-1] - prof[-2]
    tl = np.linalg.norm(tang, axis=1, keepdims=True)
    tang = tang / np.maximum(tl, 1e-9)
    # outward normal of a (r,z) path is the tangent rotated by -90 degrees
    pn = np.stack([tang[:, 1], -tang[:, 0]], axis=1)
    pos = np.empty((m, n_t, 3))
    nrm = np.empty((m, n_t, 3))
    for i in range(m):
        pos[i, :, 0] = prof[i, 0] * np.cos(ts)
        pos[i, :, 1] = prof[i, 0] * np.sin(ts)
        pos[i, :, 2] = prof[i, 1]
        nrm[i, :, 0] = pn[i, 0] * np.cos(ts)
        nrm[i, :, 1] = pn[i, 0] * np.sin(ts)
        nrm[i, :, 2] = pn[i, 1]
    faces = []
    for i in range(m - 1):
        for j in range(n_t):
            a = i * n_t + j
            b = i * n_t + (j + 1) % n_t
            c = (i + 1) * n_t + (j + 1) % n_t
            d = (i + 1) * n_t + j
            faces.append([a, b, c])
            faces.append([a, c, d])
    return pos.reshape(-1, 3), nrm.reshape(-1, 3), np.asarray(faces, dtype=np.int64)


def _arc(cr, cz, r, a0, a1, n=6):
    a = np.linspace(a0, a1, n)
    return [(cr + r * np.cos(t), cz + r * np.sin(t)) for t in a]


# --------------------------------------------------------------------------- #
# Dish and agar
# --------------------------------------------------------------------------- #
def dish_glass_part():
    """A glass dish: a floor, a rounded foot and a straight wall with a rounded rim."""
    ro, ri = DISH_R_OUT, DISH_R_OUT - DISH_WALL
    f = DISH_FLOOR
    prof = []
    prof.append((0.5, 0.0))
    prof.append((ro - 2.0, 0.0))
    prof += _arc(ro - 2.0, 2.0, 2.0, -np.pi / 2, 0.0)          # outer foot fillet
    prof.append((ro, DISH_H - DISH_WALL / 2))
    prof += _arc(ro - DISH_WALL / 2, DISH_H - DISH_WALL / 2, DISH_WALL / 2, 0.0, np.pi)  # rim
    prof.append((ri, f + 1.2))
    prof += _arc(ri - 1.2, f + 1.2, 1.2, 0.0, -np.pi / 2)      # inner floor fillet
    prof.append((0.5, f))
    # a closed ring-profile: first and last points sit close to the axis, so the
    # floor reads as one sheet from the centre out
    pos, nrm, faces = _revolve(prof)
    return Part("glass", pos, nrm, faces, material=dict(
        color=_lin(GLASS_COLOR), alpha=0.18, roughness=0.04, metallic=0.0,
        transmission=0.92, ior=1.5, double_sided=True))
# The glass carries both a low alpha and KHR_materials_transmission. Viewers that
# support transmission (model-viewer / three.js) render it as glass and ignore
# the base alpha for the transmitted light; viewers that do not fall back to the
# alpha. In the bundled model-viewer the dish reads as clear glass.


def agar_part(top_z=None, flat=False, color=None):
    """The agar bed. A slight meniscus climbs the wall unless flat is set
    (used under a photo relief, which brings its own surface). color is sRGB;
    under a photo relief it is the agar colour measured from the photo."""
    top = (DISH_FLOOR + AGAR_DEPTH) if top_z is None else top_z
    AGAR = tuple(color) if color is not None else AGAR_COLOR

    def h(r):
        if flat:
            return np.full_like(r, top)
        return top + 1.1 * np.power(np.clip(r / AGAR_R, 0, 1), 8)

    def col(r):
        t = np.clip(r / AGAR_R, 0, 1)[:, None]
        centre = _lin(AGAR)
        edge = _lin((AGAR[0] * 0.88, AGAR[1] * 0.86, AGAR[2] * 0.8))
        return centre[None, :] * (1 - t) + edge[None, :] * t

    tp, tn, tc, tf = _grid_surface(AGAR_R, h, color_fn=col)
    sp, sn, sf = _cylinder_side(AGAR_R, DISH_FLOOR, top + (0 if flat else 1.1))
    sc = np.tile(_lin((AGAR[0] * 0.88, AGAR[1] * 0.86, AGAR[2] * 0.8)), (len(sp), 1))
    pos, nrm, colors, faces = _concat([(tp, tn, tc, tf), (sp, sn, sc, sf)])
    return Part("agar", pos, nrm, faces, colors=colors, material=dict(
        color=[1, 1, 1], roughness=0.62, metallic=0.0, double_sided=True))


# --------------------------------------------------------------------------- #
# Colonies
# --------------------------------------------------------------------------- #
def _noise_wobble(theta, lobes, amp, phase):
    return 1.0 + amp * np.sin(lobes * theta + phase)


def _group_beads_arms(rng, G, n_arms, bead_r, branch=True):
    """Branching arms of beads, like a rhizoid or filamentous colony. Returns
    a list of (x, y, r) in group-local millimetres."""
    out = []
    # central knot
    for _ in range(5):
        ang, rad = rng.uniform(0, 2 * np.pi), rng.uniform(0, bead_r * 0.9)
        out.append((rad * np.cos(ang), rad * np.sin(ang), bead_r * rng.uniform(1.0, 1.25)))
    base_ang = rng.uniform(0, 2 * np.pi)
    for a in range(n_arms):
        ang = base_ang + a * 2 * np.pi / n_arms + rng.normal(0, 0.18)
        length = G * rng.uniform(1.0, 1.45)
        _walk(rng, out, 0.0, 0.0, ang, length, bead_r, bead_r * 0.8, branch)
    return out


def _walk(rng, out, x, y, ang, length, r0, r1, branch, depth=0):
    n = max(2, int(length / (r0 * 1.6)))
    for i in range(n):
        t = i / max(1, n - 1)
        r = (r0 * (1 - t) + r1 * t) * rng.uniform(0.9, 1.12)
        x += np.cos(ang) * r * 1.5
        y += np.sin(ang) * r * 1.5
        ang += rng.normal(0, 0.12)
        out.append((x, y, r))
        if branch and depth == 0 and 0.25 < t < 0.8 and rng.random() < 0.22:
            side = ang + rng.choice([-1, 1]) * rng.uniform(0.7, 1.1)
            _walk(rng, out, x, y, side, length * 0.35, r * 0.9, r * 0.7, False, depth + 1)


def _group_beads_rosette(rng, G, bead_r):
    """A compact cauliflower-like heap of beads."""
    out = []
    ring = 0
    step = bead_r * 1.7
    while ring * step <= G:
        rho = ring * step
        cnt = max(1, int(round(2 * np.pi * rho / (bead_r * 1.7)))) if ring else 1
        ph = rng.uniform(0, 2 * np.pi)
        for k in range(cnt):
            a = ph + 2 * np.pi * k / cnt
            jr = rng.normal(0, bead_r * 0.12, 2)
            r = bead_r * rng.uniform(0.9, 1.1) * (1.0 - 0.25 * rho / max(G, 1e-6))
            out.append((rho * np.cos(a) + jr[0], rho * np.sin(a) + jr[1], r))
        ring += 1
    return out


def _bead_arrays(rng, beads, cx, cy, ao_floor, base_col, dome_h, stack=True):
    """Turn local (x,y,r) beads into 3D bead centres: heap them upward toward the
    middle of the group and add a second layer on dense areas."""
    pts = np.array([(b[0], b[1]) for b in beads])
    rad = np.array([b[2] for b in beads])
    # neighbour density, to build a second layer where beads crowd together
    d = np.linalg.norm(pts[:, None, :] - pts[None, :, :], axis=2)
    dens = (d < (rad[:, None] + rad[None, :]) * 1.15).sum(axis=1) - 1
    centers, radii, cols = [], [], []
    far = np.max(np.linalg.norm(pts, axis=1)) + 1e-6
    for i in range(len(pts)):
        lift = dome_h * (1.0 - (np.linalg.norm(pts[i]) / far) ** 2)
        z = ao_floor + rad[i] * 0.55 + lift
        j = rng.uniform(0.9, 1.08)
        c = np.clip(base_col * j, 0, 1)
        centers.append((cx + pts[i, 0], cy + pts[i, 1], z))
        radii.append((rad[i],) * 3)
        cols.append(c)
        if stack and dens[i] >= 4 and rng.random() < 0.55:
            r2 = rad[i] * 0.82
            centers.append((cx + pts[i, 0] + rng.normal(0, rad[i] * 0.2),
                            cy + pts[i, 1] + rng.normal(0, rad[i] * 0.2),
                            z + rad[i] * 0.95))
            radii.append((r2, r2, r2))
            cols.append(np.clip(base_col * rng.uniform(0.95, 1.12), 0, 1))
    return centers, radii, cols


def colony_layout(params, seed):
    """Where each colony sits and how it is built, shared by the viewer scene
    (colony_part) and the printable STL (colony_height_map), so the two always
    show the same plate.

    Sizes are real millimetres on a 90 mm plate (colony_radius_mm in the shape
    config is a radius); colonies are placed one by one without overlap, each a
    single colony, not a cluster. Returns a list of dicts:
      dict(kind="dome", x, y, r, zs, lobes, phase)          smooth colony
      dict(kind="beads", x, y, centers, radii)              granular / branching
    plus the colour to use, as (layout, base_col_linear)."""
    rng = np.random.default_rng(seed)
    base_srgb = np.array(params.get("color", (0.87, 0.84, 0.76)), dtype=np.float64)
    lum = base_srgb @ np.array([0.30, 0.59, 0.11])
    base_srgb = np.clip(lum + (base_srgb - lum) * 1.6, 0, 1) * 0.95
    base_col = _lin(base_srgb)
    style = params.get("texture_style", "granular")
    edge = params.get("edge", "entire")
    profile = params.get("profile", "raised")
    r0, r1 = params.get("colony_radius_mm", (1.0, 2.5))
    n_col = int(params.get("n_colonies", 40))

    ao_floor = DISH_FLOOR + AGAR_DEPTH
    lim = AGAR_R - 2.5
    placed, tries = [], 0
    while len(placed) < n_col and tries < n_col * 80:
        tries += 1
        # a few large, many small: real plates have a spread of sizes
        G = r0 + (r1 - r0) * rng.beta(1.6, 2.4)
        ff = 1.5 if edge in ("rhizoid", "filamentous") else 1.08
        foot = G * ff
        if lim - foot <= 0:
            continue
        rad = np.sqrt(rng.uniform(0, 1)) * (lim - foot)
        ang = rng.uniform(0, 2 * np.pi)
        x, y = rad * np.cos(ang), rad * np.sin(ang)
        if all(np.hypot(x - ox, y - oy) > (foot + of) + 0.6 for ox, oy, _, of in placed):
            placed.append((x, y, G, foot))

    zs_by_profile = {"flat": 0.22, "raised": 0.42, "convex": 0.5, "umbonate": 0.6, "crateriform": 0.3}
    layout = []
    for (x, y, G, foot) in placed:
        zs = zs_by_profile.get(profile, 0.42)
        branching = edge in ("rhizoid", "filamentous") and G >= 1.5
        beaded = style in ("granular", "dry") and G >= 1.6 and not branching
        if branching or beaded:
            if branching:
                beads = _group_beads_arms(rng, G, int(rng.integers(3, 6)), max(0.3, G * 0.15))
                dome_h = G * 0.06
            else:
                beads = _group_beads_rosette(rng, G, max(0.3, G * (0.24 if style == "granular" else 0.3)))
                dome_h = G * zs
            tint = np.clip(base_col * rng.uniform(0.94, 1.05), 0, 1)
            centers, radii, cols = _bead_arrays(rng, beads, x, y, ao_floor, tint, dome_h)
            layout.append(dict(kind="beads", x=x, y=y, centers=centers, radii=radii, cols=cols))
        else:
            lobes = 5 if edge == "undulate" else (9 if edge in ("rhizoid", "filamentous") else 0)
            layout.append(dict(kind="dome", x=x, y=y, r=G, zs=zs, lobes=lobes,
                               wobble=0.16 if edge == "undulate" else 0.28,
                               phase=float(rng.uniform(0, 6.28)),
                               tint=np.clip(base_col * rng.uniform(0.94, 1.06), 0, 1)))
    return layout, style


def _dome_mesh(c, ao_floor, n_lat=12, n_lon=24):
    u, f = _unit_sphere(n_lat, n_lon)
    theta = np.arctan2(u[:, 1], u[:, 0])
    sc = _noise_wobble(theta, c["lobes"], c["wobble"], c["phase"]) if c["lobes"] else np.ones(len(u))
    r, zs = c["r"], c["zs"]
    p = np.empty_like(u)
    p[:, 0] = c["x"] + u[:, 0] * r * sc
    p[:, 1] = c["y"] + u[:, 1] * r * sc
    p[:, 2] = ao_floor + u[:, 2] * r * zs
    keep_up = u[:, 2] >= -0.05   # the lower half sits inside the agar
    nrm = np.stack([u[:, 0] / r, u[:, 1] / r, u[:, 2] / (r * zs)], axis=1)
    nrm = nrm / np.linalg.norm(nrm, axis=1, keepdims=True)
    shade = 0.78 + 0.22 * np.clip(u[:, 2], 0, 1)
    col = c["tint"][None, :] * shade[:, None]
    ok = keep_up[f].all(axis=1)
    return p, nrm, col, f[ok]


def colony_part(params, seed):
    """Build the colonies for one plate from a shape_config colony entry."""
    layout, style = colony_layout(params, seed)
    ao_floor = DISH_FLOOR + AGAR_DEPTH
    chunks = []
    for c in layout:
        if c["kind"] == "dome":
            res = (8, 16) if c["r"] < 0.8 else (12, 24)
            chunks.append(_dome_mesh(c, ao_floor, *res))
        else:
            chunks.append(_ellipsoids(c["centers"], c["radii"], c["cols"], ao_floor, 6, 10))
    if not chunks:   # an empty plate still needs a valid primitive
        chunks.append(_ellipsoids([(0, 0, ao_floor - 1)], [(0.01,) * 3], [(1, 1, 1)], ao_floor, 4, 6))
    pos, nrm, col, faces = _concat(chunks)
    return Part("colonies", pos, nrm, faces, colors=col, material=dict(
        color=[1, 1, 1], roughness=0.30 if style in ("glossy", "mucoid") else 0.5,
        metallic=0.0, double_sided=False))


def colony_height_map(params, seed, n=512, diameter_mm=90.0):
    """Height above the agar (mm) on an n x n grid covering the plate, from the
    same layout the viewer draws. Feeds microbe3d.photo_colony_plate for the STL."""
    layout, _ = colony_layout(params, seed)
    ao_floor = DISH_FLOOR + AGAR_DEPTH
    px = diameter_mm / n
    coords = (np.arange(n) - (n - 1) / 2.0) * px
    X, Y = np.meshgrid(coords, -coords)        # row 0 = +y (top), as photo_colony_plate expects
    H = np.zeros((n, n))

    def window(cx, cy, rad):
        j0 = max(0, int((cx - rad) / px + (n - 1) / 2.0) - 1)
        j1 = min(n, int((cx + rad) / px + (n - 1) / 2.0) + 2)
        i0 = max(0, int((-cy - rad) / px + (n - 1) / 2.0) - 1)
        i1 = min(n, int((-cy + rad) / px + (n - 1) / 2.0) + 2)
        return slice(i0, i1), slice(j0, j1)

    for c in layout:
        if c["kind"] == "dome":
            si, sj = window(c["x"], c["y"], c["r"] * 1.35)
            dx, dy = X[si, sj] - c["x"], Y[si, sj] - c["y"]
            th = np.arctan2(dy, dx)
            sc = _noise_wobble(th, c["lobes"], c["wobble"], c["phase"]) if c["lobes"] else 1.0
            s2 = (dx * dx + dy * dy) / (c["r"] * sc) ** 2
            h = c["r"] * c["zs"] * np.sqrt(np.clip(1.0 - s2, 0, None))
            H[si, sj] = np.maximum(H[si, sj], h)
        else:
            for (bx, by, bz), (rr, _, _) in zip(c["centers"], c["radii"]):
                si, sj = window(bx, by, rr)
                dx, dy = X[si, sj] - bx, Y[si, sj] - by
                h = (bz - ao_floor) + np.sqrt(np.clip(rr * rr - dx * dx - dy * dy, 0, None))
                h = np.where(dx * dx + dy * dy <= rr * rr, h, 0.0)
                H[si, sj] = np.maximum(H[si, sj], h)
    H[np.hypot(X, Y) > AGAR_R] = 0.0
    return H


# --------------------------------------------------------------------------- #
# GLB writer
# --------------------------------------------------------------------------- #
def _pad4(buf, pad):
    return buf + pad * ((4 - len(buf) % 4) % 4)


def _to_y_up(a):
    """Z-up (x, y, z) to glTF Y-up (x, z, -y). A proper rotation, so winding holds."""
    a = np.asarray(a, dtype=np.float32)
    return np.stack([a[:, 0], a[:, 2], -a[:, 1]], axis=1)


def write_scene_glb(parts, path):
    chunks = []      # (name, bytes)
    views = []
    accs = []
    prims = []
    materials = []
    textures, images = [], []
    ext_used = set()
    offset = 0

    def add_blob(data, target=None):
        nonlocal offset
        data = _pad4(data, b"\x00")
        bv = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            bv["target"] = target
        views.append(bv)
        chunks.append(data)
        offset += len(data)
        return len(views) - 1

    for part in parts:
        pos = _to_y_up(part.pos)
        nrm = _to_y_up(part.nrm)
        attrs = {}
        v = add_blob(pos.tobytes(), 34962)
        accs.append({"bufferView": v, "componentType": 5126, "count": len(pos), "type": "VEC3",
                     "min": pos.min(axis=0).tolist(), "max": pos.max(axis=0).tolist()})
        attrs["POSITION"] = len(accs) - 1
        v = add_blob(nrm.tobytes(), 34962)
        accs.append({"bufferView": v, "componentType": 5126, "count": len(nrm), "type": "VEC3"})
        attrs["NORMAL"] = len(accs) - 1
        if part.uvs is not None:
            v = add_blob(part.uvs.tobytes(), 34962)
            accs.append({"bufferView": v, "componentType": 5126, "count": len(part.uvs), "type": "VEC2"})
            attrs["TEXCOORD_0"] = len(accs) - 1
        if part.colors is not None:
            v = add_blob(part.colors.astype(np.float32).tobytes(), 34962)
            accs.append({"bufferView": v, "componentType": 5126, "count": len(part.colors), "type": "VEC3"})
            attrs["COLOR_0"] = len(accs) - 1
        v = add_blob(part.idx.tobytes(), 34963)
        accs.append({"bufferView": v, "componentType": 5125, "count": len(part.idx), "type": "SCALAR"})
        idx_acc = len(accs) - 1

        m = part.material
        col = list(np.asarray(m.get("color", [1, 1, 1]), dtype=float))
        pbr = {"metallicFactor": float(m.get("metallic", 0.0)),
               "roughnessFactor": float(m.get("roughness", 0.5))}
        mat = {"name": part.name, "pbrMetallicRoughness": pbr,
               "doubleSided": bool(m.get("double_sided", False))}
        if part.texture_path and Path(part.texture_path).exists() and part.uvs is not None:
            v = add_blob(Path(part.texture_path).read_bytes())
            images.append({"bufferView": v, "mimeType": "image/png"})
            textures.append({"source": len(images) - 1})
            pbr["baseColorTexture"] = {"index": len(textures) - 1}
        elif "alpha" in m:
            pbr["baseColorFactor"] = [col[0], col[1], col[2], float(m["alpha"])]
        else:
            pbr["baseColorFactor"] = [col[0], col[1], col[2], 1.0]
        if "alpha" in m and m.get("transmission") is None:
            mat["alphaMode"] = "BLEND"
        if m.get("transmission") is not None:
            # Real glass where the viewer supports it; viewers that do not will
            # fall back to the translucent base colour (alpha blend).
            mat["alphaMode"] = "BLEND"
            mat["extensions"] = {
                "KHR_materials_transmission": {"transmissionFactor": float(m["transmission"])},
                "KHR_materials_ior": {"ior": float(m.get("ior", 1.5))},
            }
            ext_used.update(["KHR_materials_transmission", "KHR_materials_ior"])
        materials.append(mat)
        prims.append({"attributes": attrs, "indices": idx_acc, "mode": 4,
                      "material": len(materials) - 1})

    gltf = {
        "asset": {"version": "2.0", "generator": "microbe3d scene3d"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": prims}],
        "materials": materials,
        "buffers": [{"byteLength": offset}],
        "bufferViews": views,
        "accessors": accs,
    }
    if images:
        gltf["images"] = images
        gltf["textures"] = textures
    if ext_used:
        gltf["extensionsUsed"] = sorted(ext_used)
    jb = _pad4(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), b" ")
    bb = b"".join(chunks)
    total = 12 + 8 + len(jb) + 8 + len(bb)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<4sII", b"glTF", 2, total))
        fh.write(struct.pack("<II", len(jb), 0x4E4F534A) + jb)
        fh.write(struct.pack("<II", len(bb), 0x004E4942) + bb)


# --------------------------------------------------------------------------- #
# High-level entry points
# --------------------------------------------------------------------------- #
def write_colony_scene(path, params, seed):
    """Glass dish, agar and colonies built from the strain's growth style."""
    write_scene_glb([agar_part(color=params.get("agar_color")), colony_part(params, seed),
                     dish_glass_part()], path)


def write_photo_scene(path, mesh, texture_path=None, agar_color=None):
    """The photo-driven relief mesh (a microbe3d.Mesh with uvs) set into a glass
    dish. The relief carries the plate photo as its colour."""
    scale = (AGAR_R - 0.2) / (np.max(np.linalg.norm(mesh.vertices[:, :2], axis=1)) or 1.0)
    base_top = DISH_FLOOR + 3.6
    v = mesh.vertices.copy()
    v[:, 0] *= scale
    v[:, 1] *= scale
    v[:, 2] += base_top
    relief = Part("plate", v, mesh.vertex_normals, mesh.faces.reshape(-1), uvs=mesh.uvs,
                  material=dict(color=[1, 1, 1], roughness=0.38, metallic=0.0, double_sided=True),
                  texture_path=texture_path)
    write_scene_glb([agar_part(top_z=base_top, flat=True, color=agar_color), relief, dish_glass_part()], path)


def write_cell_scene(path, mesh, color, roughness=0.3):
    """A single glossy cell model (a microbe3d.Mesh), standing on its own."""
    v = mesh.vertices.copy()
    v[:, 2] -= v[:, 2].min()            # rest it on the floor
    col = _lin(color)
    part = Part("cell", v, mesh.vertex_normals, mesh.faces.reshape(-1),
                material=dict(color=col, roughness=roughness, metallic=0.0, double_sided=True))
    write_scene_glb([part], path)
