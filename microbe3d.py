"""Procedural + photo-driven 3D mesh generation for the Microbial Digital Library.

Generates watertight meshes for:
  - colonies       (petri-dish colony: base disc + dome profile, stylized)
  - cells          (sphere / ellipsoid / rod / capsule, stylized)
  - photo reliefs  (real plate photo -> height map -> textured dish mesh)

Exports binary STL (print-ready) and GLB (web preview, with baked photo texture
for photo reliefs) without any external mesh library beyond numpy + Pillow.
Meshes are closed, manifold, consistently outward-oriented; unit = millimetre.
"""
import base64
import json
import struct
from pathlib import Path

import numpy as np

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


# --------------------------------------------------------------------------- #
# Mesh container + geometry
# --------------------------------------------------------------------------- #
class Mesh:
    def __init__(self, vertices, faces, uvs=None):
        self.vertices = np.asarray(vertices, dtype=np.float64)
        self.faces = np.asarray(faces, dtype=np.int64)
        self.uvs = np.asarray(uvs, dtype=np.float64) if uvs is not None else None
        self.face_normals = None
        self.vertex_normals = None

    def finalize(self, orient=True):
        if orient:
            self._orient_outward()
        self._compute_normals()
        return self

    def _orient_outward(self):
        v = self.vertices
        f = self.faces
        vol = float(np.einsum("ij,ij->i", v[f[:, 0]], np.cross(v[f[:, 1]], v[f[:, 2]])).sum())
        if vol < 0:
            self.faces = f[:, ::-1]

    def _compute_normals(self):
        v = self.vertices
        f = self.faces
        fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
        ln = np.linalg.norm(fn, axis=1, keepdims=True)
        ln[ln == 0] = 1.0
        self.face_normals = fn / ln
        vn = np.zeros_like(v)
        for k in range(3):
            np.add.at(vn, f[:, k], fn)
        nrm = np.linalg.norm(vn, axis=1, keepdims=True)
        nrm[nrm == 0] = 1.0
        self.vertex_normals = vn / nrm

    @property
    def volume(self):
        v, f = self.vertices, self.faces
        return sum(np.dot(v[a], np.cross(v[b], v[c])) for a, b, c in f) / 6.0

    def is_watertight(self):
        edges = {}
        for a, b, c in self.faces:
            for e in ((a, b), (b, c), (c, a)):
                key = tuple(sorted(e))
                edges[key] = edges.get(key, 0) + 1
        return all(cnt == 2 for cnt in edges.values())


# --------------------------------------------------------------------------- #
# Surface of revolution (lathe)
# --------------------------------------------------------------------------- #
def lathe(profile, n_theta=64):
    prof = np.asarray(profile, dtype=np.float64)
    prof[0, 0] = 0.0
    prof[-1, 0] = 0.0
    assert np.all(prof[1:-1, 0] > 1e-12), "interior profile points must have r>0"

    n_interior = len(prof) - 2
    n_vert = 1 + n_interior * n_theta + 1
    vertices = np.empty((n_vert, 3), dtype=np.float64)
    vertices[0] = [0.0, 0.0, prof[0, 1]]
    for k in range(n_interior):
        r, z = prof[k + 1]
        for j in range(n_theta):
            th = 2 * np.pi * j / n_theta
            vertices[1 + k * n_theta + j] = [r * np.cos(th), r * np.sin(th), z]
    top = 1 + n_interior * n_theta
    vertices[top] = [0.0, 0.0, prof[-1, 1]]

    def vid(k, j):
        return 1 + k * n_theta + (j % n_theta)

    faces = []
    for j in range(n_theta):
        faces.append([0, vid(0, j + 1), vid(0, j)])
    for k in range(n_interior - 1):
        for j in range(n_theta):
            a, b = vid(k, j), vid(k, j + 1)
            c, d = vid(k + 1, j + 1), vid(k + 1, j)
            faces.append([a, b, c])
            faces.append([a, c, d])
    for j in range(n_theta):
        faces.append([top, vid(n_interior - 1, j), vid(n_interior - 1, j + 1)])

    return Mesh(vertices, np.asarray(faces, dtype=np.int64)).finalize()


# --------------------------------------------------------------------------- #
# Cell shapes
# --------------------------------------------------------------------------- #
def sphere(radius=1.0, n_seg=32, n_theta=64):
    pts = []
    for i in range(n_seg + 1):
        phi = np.pi * (1 - i / n_seg)
        pts.append((radius * np.sin(phi), radius * np.cos(phi)))
    return lathe(pts, n_theta)


def ellipsoid(rx=1.0, ry=1.0, rz=1.0, n_seg=32, n_theta=64):
    m = sphere(1.0, n_seg, n_theta)
    m.vertices[:, 0] *= rx
    m.vertices[:, 1] *= ry
    m.vertices[:, 2] *= rz
    return m.finalize()


def capsule(radius=1.0, length=2.0, n_cap=16, n_theta=64):
    L = length / 2.0
    pts = [(0.0, -L - radius)]
    for i in range(1, n_cap):
        t = (np.pi / 2) * i / n_cap
        pts.append((radius * np.sin(t), -L - radius * np.cos(t)))
    pts.append((radius, -L))
    pts.append((radius, L))
    for i in range(1, n_cap):
        t = (np.pi / 2) * (1 - i / n_cap)
        pts.append((radius * np.sin(t), L + radius * np.cos(t)))
    pts.append((0.0, L + radius))
    return lathe(pts, n_theta)


def merge_meshes(meshes):
    """Several closed, outward-facing meshes as one (separate shells; each stays
    watertight, so the edge-count check still holds for the whole)."""
    verts, faces, off = [], [], 0
    for m in meshes:
        verts.append(m.vertices)
        faces.append(m.faces + off)
        off += len(m.vertices)
    return Mesh(np.vstack(verts), np.vstack(faces)).finalize(orient=False)


def _translated(mesh, dx=0.0, dy=0.0, dz=0.0):
    m = Mesh(mesh.vertices + np.array([dx, dy, dz]), mesh.faces.copy())
    return m.finalize(orient=False)


def _rotated_x90(mesh):
    """Lay a z-axis shape along x."""
    v = mesh.vertices
    m = Mesh(np.stack([v[:, 2], v[:, 1], -v[:, 0]], axis=1), mesh.faces.copy())
    return m.finalize(orient=False)


PRINT_SIZE_MM = 24.0   # cell models are scaled so their largest dimension is this


def _ringed_capsule(radius, length, n_cap=12, n_len=24, n_theta=40):
    """A capsule along z with extra rings on the straight part, so it can be bent."""
    L = length / 2.0
    pts = [(0.0, -L - radius)]
    for i in range(1, n_cap):
        t = (np.pi / 2) * i / n_cap
        pts.append((radius * np.sin(t), -L - radius * np.cos(t)))
    for i in range(n_len + 1):
        pts.append((radius, -L + length * i / n_len))
    for i in range(1, n_cap):
        t = (np.pi / 2) * (1 - i / n_cap)
        pts.append((radius * np.sin(t), L + radius * np.cos(t)))
    pts.append((0.0, L + radius))
    return lathe(pts, n_theta)


def _bend_along(mesh, curve, s_min, s_max, n=400):
    """Carry a straight mesh along a centre line. A vertex at height z and
    offset (x, y) goes to curve(z) + x*N + y*B, with N and B from a frame
    carried along the curve without twisting. curve(s) must be parameterised
    by arc length and pass through the origin at s = 0."""
    s = np.linspace(s_min, s_max, n)
    c = np.array([curve(t) for t in s])
    tang = np.gradient(c, s, axis=0)
    tang /= np.linalg.norm(tang, axis=1, keepdims=True)
    ref = np.array([0.0, 0.0, 1.0]) if abs(tang[0][2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    nrm = np.empty_like(tang)
    nrm[0] = np.cross(tang[0], ref)
    nrm[0] /= np.linalg.norm(nrm[0])
    for i in range(1, n):
        v = nrm[i - 1] - np.dot(nrm[i - 1], tang[i]) * tang[i]
        nrm[i] = v / np.linalg.norm(v)
    bin_ = np.cross(tang, nrm)
    v = mesh.vertices
    out = np.empty_like(v)
    for k in range(3):
        cz = np.interp(v[:, 2], s, c[:, k])
        nz = np.interp(v[:, 2], s, nrm[:, k])
        bz = np.interp(v[:, 2], s, bin_[:, k])
        out[:, k] = cz + v[:, 0] * nz + v[:, 1] * bz
    return Mesh(out, mesh.faces.copy()).finalize(orient=False)


def curved_rod(radius=0.3, length=1.8, bend_deg=95.0):
    """A comma-shaped rod (vibrio): a capsule bent along a circular arc."""
    total = length + 2 * radius
    R = total / np.radians(bend_deg)
    mesh = _ringed_capsule(radius, length)

    def arc(t):
        a = t / R
        return np.array([R * np.sin(a), R * (1 - np.cos(a)), 0.0])
    return _bend_along(mesh, arc, -total, total)


def spiral_cell(radius=0.18, length=8.5, helix_radius=0.35, pitch=1.4):
    """A helical cell (spirillum): a long capsule wound along a helix."""
    total = length + 2 * radius
    c = pitch / (2 * np.pi)
    k = np.sqrt(helix_radius ** 2 + c ** 2)
    mesh = _ringed_capsule(radius, length, n_len=80)

    def helix(t):
        u = t / k
        return np.array([c * u, helix_radius * np.sin(u), helix_radius * (1 - np.cos(u))])
    return _bend_along(mesh, helix, -total, total, n=800)


# Positions (in cell diameters) for arrangements of round cells.
_CLUSTER = [(0, 0, 0), (1, 0, 0), (0.5, 0.87, 0), (-0.5, 0.87, 0), (-1, 0, 0),
            (-0.5, -0.87, 0), (0.5, -0.87, 0), (0.5, 0.29, 0.82), (-0.5, 0.29, 0.82),
            (0, -0.58, 0.82), (1.5, 0.87, 0.1)]
_CHAIN = [(-2.5, 0.06, 0), (-1.5, -0.06, 0), (-0.5, 0.06, 0), (0.5, -0.06, 0),
          (1.5, 0.06, 0), (2.5, -0.06, 0)]
_PACKET = [(x, y, z) for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)]
_TETRAD = [(-0.5, -0.5, 0), (0.5, -0.5, 0), (-0.5, 0.5, 0), (0.5, 0.5, 0)]
_PAIR = [(-0.5, 0, 0), (0.5, 0, 0)]

SHAPE_KINDS = ("coccus", "rod", "coccobacillus", "curved_rod", "spiral", "ellipsoid")
ARRANGEMENTS = {
    "coccus": ("single", "pair", "chain", "tetrad", "cluster", "packet"),
    "rod": ("single", "pair", "chain", "palisade"),
    "coccobacillus": ("single", "pair", "chain"),
    "curved_rod": ("single", "pair"),
    "spiral": ("single",),
    "ellipsoid": ("single", "budding"),
}


def cell_assembly(params):
    """A cell model in its typical arrangement, from shape_config cell params.

    Geometry is given in micrometres (kind + radius/length or rx/ry/rz). Kinds:
    coccus, rod, coccobacillus, curved_rod, spiral and ellipsoid (yeast).
    Arrangements: single, pair, chain, tetrad, cluster, packet, palisade and
    budding (see ARRANGEMENTS for which go with which kind). The result is
    scaled so its largest dimension is PRINT_SIZE_MM, and the scale is returned
    so the page can state the real size. Returns (mesh, scale_mm_per_um)."""
    kind = params["kind"]
    arr = params.get("arrangement", "single")
    if kind not in ARRANGEMENTS:
        raise ValueError(f"unknown kind: {kind}")
    if arr not in ARRANGEMENTS[kind]:
        arr = "single"
    if kind == "ellipsoid":
        cell = ellipsoid(params["rx"], params["ry"], params["rz"], n_seg=28, n_theta=56)
        parts = [cell]
        if arr == "budding":
            rb = 0.42 * min(params["rx"], params["ry"])
            bud = sphere(rb, n_seg=20, n_theta=40)
            parts.append(_translated(bud, dz=params["rz"] + rb * 0.55))
    elif kind in ("rod", "coccobacillus"):
        r = params.get("radius", 0.4)
        L = params.get("length", 2.0 if kind == "rod" else 0.5)
        rod = _rotated_x90(capsule(r, L, n_cap=12, n_theta=40))
        span = L + 2 * r
        gap = 0.06 * span
        if arr == "palisade":
            # side by side, a little staggered, as corynebacteria lie
            parts = [_translated(rod, dx=(i - 1) * 0.18 * span, dy=(i - 1) * 2.15 * r)
                     for i in range(3)]
        else:
            n = {"pair": 2, "chain": 4}.get(arr, 1)
            parts = [_translated(rod, dx=(i - (n - 1) / 2.0) * (span + gap)) for i in range(n)]
    elif kind == "curved_rod":
        r, L = params.get("radius", 0.3), params.get("length", 1.8)
        cell = curved_rod(r, L)
        parts = [cell]
        if arr == "pair":
            ext = cell.vertices.max(axis=0) - cell.vertices.min(axis=0)
            parts.append(_translated(cell, dx=ext[0] * 1.02))
    elif kind == "spiral":
        parts = [spiral_cell(params.get("radius", 0.18), params.get("length", 8.5))]
    else:  # coccus
        r = params.get("radius", 0.5)
        ball = sphere(r, n_seg=20, n_theta=40)
        d = 2 * r * 1.02
        layout = {"pair": _PAIR, "chain": _CHAIN, "tetrad": _TETRAD,
                  "cluster": _CLUSTER, "packet": _PACKET}.get(arr, [(0, 0, 0)])
        parts = [_translated(ball, dx=x * d, dy=y * d, dz=z * d) for x, y, z in layout]
    mesh = merge_meshes(parts) if len(parts) > 1 else parts[0]
    ext = mesh.vertices.max(axis=0) - mesh.vertices.min(axis=0)
    scale = PRINT_SIZE_MM / float(ext.max())
    centre = (mesh.vertices.max(axis=0) + mesh.vertices.min(axis=0)) / 2.0
    mesh = Mesh((mesh.vertices - centre) * scale, mesh.faces).finalize(orient=False)
    return mesh, scale


# --------------------------------------------------------------------------- #
# Stylized colony shapes
# --------------------------------------------------------------------------- #
def _colony_height(profile, s):
    if profile == "flat":
        return 1.0
    dome = 1.0 - s * s
    if profile in ("raised", "convex"):
        return dome
    if profile == "umbonate":
        return dome + 0.6 * np.exp(-((s / 0.25) ** 2))
    if profile == "crateriform":
        return np.clip(dome - 0.5 * np.exp(-((s / 0.30) ** 2)), 0.0, None)
    raise ValueError(f"unknown colony profile: {profile}")


def colony(radius=25.0, base_h=2.0, height=6.0, profile="convex", n_r=24, n_theta=64):
    pts = [(0.0, 0.0)]
    pts.append((radius, 0.0))
    pts.append((radius, base_h))
    for i in range(1, n_r):
        s = 1.0 - i / n_r
        pts.append((radius * s, base_h + height * _colony_height(profile, s)))
    pts.append((0.0, base_h + height * _colony_height(profile, 0.0)))
    return lathe(pts, n_theta)


# --------------------------------------------------------------------------- #
# Procedural surface noise (no external noise/texture library required)
# --------------------------------------------------------------------------- #
def _value_noise_2d(shape, cell=8.0, seed=0):
    """Smooth value noise on an (h, w) grid. `cell` is the feature size in
    pixels; interpolation uses a quintic smoothstep so there's no visible
    grid-cell edge, which a plain bilinear lattice noise tends to show."""
    h, w = shape
    cell = max(2.0, float(cell))
    gh, gw = int(h / cell) + 2, int(w / cell) + 2
    rng = np.random.default_rng(seed)
    lattice = rng.random((gh, gw))

    ys = np.arange(h) / cell
    xs = np.arange(w) / cell
    y0 = np.clip(ys.astype(int), 0, gh - 2)
    x0 = np.clip(xs.astype(int), 0, gw - 2)
    fy = ys - y0
    fx = xs - x0

    def smooth(t):
        return t * t * t * (t * (t * 6 - 15) + 10)

    fy_s = smooth(fy)[:, None]
    fx_s = smooth(fx)[None, :]
    v00 = lattice[np.ix_(y0, x0)]
    v01 = lattice[np.ix_(y0, x0 + 1)]
    v10 = lattice[np.ix_(y0 + 1, x0)]
    v11 = lattice[np.ix_(y0 + 1, x0 + 1)]
    top = v00 * (1 - fx_s) + v01 * fx_s
    bot = v10 * (1 - fx_s) + v11 * fx_s
    return top * (1 - fy_s) + bot * fy_s


def _fbm(shape, base_cell=32.0, octaves=4, seed=0, persistence=0.5):
    """Fractal/multi-octave value noise (fractional Brownian motion),
    normalized to roughly [0, 1]. Combining a few frequencies is what gives
    a surface genuine granularity instead of one smooth blobby wobble."""
    out = np.zeros(shape, dtype=np.float64)
    amp = 1.0
    amp_sum = 0.0
    cell = base_cell
    for o in range(octaves):
        out += amp * _value_noise_2d(shape, cell=cell, seed=seed + o * 101)
        amp_sum += amp
        amp *= persistence
        cell = max(2.0, cell / 2.0)
    return out / max(amp_sum, 1e-9)


# --------------------------------------------------------------------------- #
# Synthetic colony-cluster scene (procedural "photo") for the default, no-
# upload 3D model -- a textured, colored cluster of individual colonies on
# agar rather than one smooth stylized dome, fed into photo_colony_plate()
# exactly like a real scanned plate would be.
# --------------------------------------------------------------------------- #
def colony_cluster_scene(n=256, diameter_mm=90.0, color=(0.90, 0.86, 0.75),
                         agar_color=(0.74, 0.58, 0.33), n_colonies=14,
                         colony_radius_mm=(5.0, 14.0), profile="raised",
                         texture_style="granular", edge="entire", seed=0):
    """Procedurally render a plausible petri-dish scene: a scatter of
    individual colonies in the given species color on agar, with per-style
    surface granularity, irregular edges where realistic, and simple baked
    directional shading (including a specular pass for glossy/mucoid
    strains). Returns (height_map in [0,1], PIL.Image RGB | None), the same
    shape of data build_photo_relief() derives from a real photo, so it can
    be handed to photo_colony_plate() unchanged.

    Colors and layout are illustrative, not a claim about a specific
    strain's real-world pigmentation -- they're meant to read clearly as
    distinct colonies rather than reproduce a lab photo exactly."""
    rng = np.random.default_rng(seed)
    radius_px = n / 2.0
    yy, xx = np.mgrid[0:n, 0:n]
    cx = cy = (n - 1) / 2.0
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    dish_mask = dist <= radius_px * 0.99

    height = np.zeros((n, n), dtype=np.float64)
    chans = [np.full((n, n), c, dtype=np.float64) for c in agar_color]

    # faint agar grain so bare agar isn't a dead flat color
    agar_grain = _fbm((n, n), base_cell=n / 10, octaves=3, seed=seed + 1)
    agar_shade = 1.0 + 0.06 * (agar_grain - 0.5)
    chans = [np.clip(ch * agar_shade, 0, 1) for ch in chans]

    px_per_mm = n / diameter_mm

    # Two-tier placement: a handful of well-separated colony *groups* (so
    # agar stays visible between them, matching how real plates look), each
    # realized as a small scatter of overlapping micro-bumps (so each group
    # itself reads as a fused, cauliflower-like cluster rather than one
    # perfect dome). A single-tier scatter at realistic colony counts tends
    # to blanket the whole dish with no bare agar showing at all.
    n_groups = max(3, min(8, int(round(n_colonies / 2.2))))
    max_group_r = radius_px * 0.74
    min_sep = radius_px * 0.32
    group_centers = []
    attempts = 0
    while len(group_centers) < n_groups and attempts < n_groups * 60:
        attempts += 1
        ang = rng.uniform(0, 2 * np.pi)
        rad = rng.uniform(0, max_group_r)
        gx, gy = cx + rad * np.cos(ang), cy + rad * np.sin(ang)
        if all(np.hypot(gx - ox, gy - oy) > min_sep for ox, oy in group_centers):
            group_centers.append((gx, gy))

    placed = []
    micro_per_group = max(3, int(round(n_colonies / max(1, len(group_centers)))))
    for (gx, gy) in group_centers:
        footprint_mm = rng.uniform(*colony_radius_mm) * rng.uniform(1.3, 2.0)
        n_micro = rng.integers(max(3, micro_per_group - 2), micro_per_group + 3)
        for _ in range(int(n_micro)):
            jr = rng.uniform(0, footprint_mm * 0.55) * px_per_mm
            jang = rng.uniform(0, 2 * np.pi)
            px, py = gx + jr * np.cos(jang), gy + jr * np.sin(jang)
            micro_r_mm = rng.uniform(colony_radius_mm[0] * 0.45, colony_radius_mm[1] * 0.60)
            placed.append((px, py, micro_r_mm * px_per_mm))

    fine_noise = _fbm((n, n), base_cell=max(2.0, n / 40), octaves=3, seed=seed + 2)
    coarse_noise = _fbm((n, n), base_cell=max(3.0, n / 8), octaves=2, seed=seed + 3)

    bump_amp = {"granular": 0.22, "dry": 0.30, "glossy": 0.06, "mucoid": 0.05}.get(texture_style, 0.15)

    for (px, py, r_px) in placed:
        d = np.sqrt((xx - px) ** 2 + (yy - py) ** 2)
        if edge in ("undulate", "rhizoid", "filamentous"):
            theta = np.arctan2(yy - py, xx - px)
            lobes = 5 if edge == "undulate" else 9
            wobble = 0.18 if edge == "undulate" else 0.35
            edge_noise = np.sin(theta * lobes + seed) * wobble
            edge_noise = edge_noise + (coarse_noise - 0.5) * (0.30 if edge == "rhizoid" else 0.15)
            r_eff = r_px * (1.0 + edge_noise)
        else:
            r_eff = r_px * (1.0 + (coarse_noise - 0.5) * 0.08)
        s = d / np.maximum(r_eff, 1e-6)
        dome = np.clip(1.0 - s * s, 0.0, None)
        if profile == "umbonate":
            dome = dome + 0.5 * np.exp(-((s / 0.20) ** 2))
        elif profile == "crateriform":
            dome = np.clip(dome - 0.45 * np.exp(-((s / 0.28) ** 2)), 0.0, None)
        inside = s < 1.0
        bump = (fine_noise - 0.5) * bump_amp * inside
        local_h = np.clip(dome + bump, 0.0, None)
        take = inside & (local_h > height)
        height = np.where(take, local_h, height)
        jitter = 1.0 + rng.normal(0, 0.05)
        for i, base in enumerate(color):
            chans[i] = np.where(take, np.clip(base * jitter, 0, 1), chans[i])

    height = np.where(dish_mask, height, 0.0)
    if height.max() > 0:
        height = height / height.max()

    # cheap relief shading from the local height gradient, instead of a flat
    # unlit color fill -- this alone is most of why the old models read as
    # plastic rather than biological
    gy, gx = np.gradient(height)
    light = np.array([-0.5, -0.4, 0.8])
    light = light / np.linalg.norm(light)
    slope = 22.0
    nx, ny, nz = -gx * slope, -gy * slope, np.ones_like(height)
    nlen = np.sqrt(nx * nx + ny * ny + nz * nz)
    nx, ny, nz = nx / nlen, ny / nlen, nz / nlen
    ndotl = np.clip(nx * light[0] + ny * light[1] + nz * light[2], 0.0, 1.0)
    shade = 0.55 + 0.55 * ndotl
    chans = [np.clip(ch * shade, 0, 1) for ch in chans]
    if texture_style in ("glossy", "mucoid"):
        spec = np.clip(ndotl, 0, 1) ** 40
        hl = spec * 0.30
        chans = [np.clip(ch + hl, 0, 1) for ch in chans]

    img = None
    if Image is not None:
        rgb = np.stack(chans, axis=-1)
        img = Image.fromarray((rgb * 255).astype(np.uint8), mode="RGB")
    return height, img


# --------------------------------------------------------------------------- #
# Photo-driven petri-dish relief
# --------------------------------------------------------------------------- #
def _sample_center_area(intensity, grid, i0, j0, radius_px):
    """Adaptive-radius area average around (i0, j0) to avoid pinwheel aliasing
    at the mesh's polar center, where many angular vertices would otherwise
    collapse onto a near-single pixel."""
    n = intensity.shape[0]
    r = max(2, int(radius_px))
    i_lo, i_hi = max(0, i0 - r), min(n, i0 + r + 1)
    j_lo, j_hi = max(0, j0 - r), min(n, j0 + r + 1)
    patch = intensity[i_lo:i_hi, j_lo:j_hi]
    return float(patch.mean()) if patch.size else float(intensity[i0, j0])


def photo_colony_plate(intensity_map, diameter_mm=90.0, base_h=1.2, relief_mm=6.0,
                       n_r=40, n_theta=96, rim_h=1.0):
    """Build a lathe-free, grid-based disc mesh whose height follows a 2D
    intensity map (colony density/brightness), so the model is a genuine
    relief of the actual plate rather than a generic stylized dome.

    intensity_map: 2D numpy array, values in [0,1], square, where 1 = more
    colony growth (raised) and 0 = bare agar background.
    Returns a Mesh plus a UV array for photo-texture baking.
    """
    n = intensity_map.shape[0]
    radius = diameter_mm / 2.0
    cx = cy = (n - 1) / 2.0

    # Build a polar sampling grid (rings x angular steps) sampling the square
    # intensity map via bilinear interpolation, with adaptive-radius area
    # sampling very close to the center to prevent pinwheel artifacts.
    def bilinear(ii, jj):
        ii = np.clip(ii, 0, n - 1)
        jj = np.clip(jj, 0, n - 1)
        i0 = int(np.floor(ii)); j0 = int(np.floor(jj))
        i1 = min(i0 + 1, n - 1); j1 = min(j0 + 1, n - 1)
        fi, fj = ii - i0, jj - j0
        v00, v01 = intensity_map[i0, j0], intensity_map[i0, j1]
        v10, v11 = intensity_map[i1, j0], intensity_map[i1, j1]
        return (v00 * (1 - fi) * (1 - fj) + v01 * (1 - fi) * fj +
                v10 * fi * (1 - fj) + v11 * fi * fj)

    verts = []
    uvs = []
    heights = []

    # center vertex placeholder (fixed below to mean of surrounding ring)
    verts.append([0.0, 0.0, base_h])
    uvs.append([0.5, 0.5])
    heights.append(None)

    px_per_mm = n / diameter_mm
    for k in range(1, n_r + 1):
        s = k / n_r  # 0..1 normalized radius
        r_mm = s * radius
        ring_px_radius = max(1.0, 0.5 * (radius / n_r) * px_per_mm)
        for j in range(n_theta):
            th = 2 * np.pi * j / n_theta
            x_mm = r_mm * np.cos(th)
            y_mm = r_mm * np.sin(th)
            ii = cy - (y_mm * px_per_mm)
            jj = cx + (x_mm * px_per_mm)
            if k <= 2:
                val = _sample_center_area(intensity_map, n, int(round(ii)), int(round(jj)),
                                          ring_px_radius * 2)
            else:
                val = bilinear(ii, jj)
            # taper intensity influence to 0 right at the rim so the dish wall reads cleanly
            taper = 1.0 if s < 0.93 else max(0.0, (1.0 - s) / 0.07)
            z = base_h + relief_mm * val * taper
            if s >= 0.985:
                z = base_h + rim_h  # raised dish rim
            verts.append([x_mm, y_mm, z])
            uvs.append([0.5 + x_mm / diameter_mm, 0.5 - y_mm / diameter_mm])
            heights.append(val)

    verts = np.array(verts, dtype=np.float64)
    uvs = np.array(uvs, dtype=np.float64)

    # Fix center vertex = mean of the innermost ring (removes pinwheel spikes)
    first_ring = verts[1:1 + n_theta]
    verts[0, 2] = float(first_ring[:, 2].mean())

    def vid(k, j):
        if k == 0:
            return 0
        return 1 + (k - 1) * n_theta + (j % n_theta)

    faces = []
    for j in range(n_theta):
        faces.append([vid(0, 0), vid(1, j), vid(1, j + 1)])
    for k in range(1, n_r):
        for j in range(n_theta):
            a, b = vid(k, j), vid(k, j + 1)
            c, d = vid(k + 1, j + 1), vid(k + 1, j)
            faces.append([a, b, c])
            faces.append([a, c, d])

    # base (flat bottom disc) so the mesh is watertight/printable
    base_center_idx = len(verts)
    verts = np.vstack([verts, [[0.0, 0.0, 0.0]]])
    uvs = np.vstack([uvs, [[0.5, 0.5]]])
    outer_ring_start = 1 + (n_r - 1) * n_theta
    base_ring_idx = {}
    base_verts = []
    for j in range(n_theta):
        outer = verts[outer_ring_start + j]
        base_verts.append([outer[0], outer[1], 0.0])
    base_start = len(verts)
    verts = np.vstack([verts, base_verts])
    uvs = np.vstack([uvs, uvs[outer_ring_start:outer_ring_start + n_theta]])
    for j in range(n_theta):
        faces.append([base_center_idx, base_start + ((j + 1) % n_theta), base_start + j])
    # side wall connecting top rim to base rim
    for j in range(n_theta):
        top_a, top_b = vid(n_r, j), vid(n_r, j + 1)
        bot_a, bot_b = base_start + j, base_start + ((j + 1) % n_theta)
        faces.append([top_a, top_b, bot_b])
        faces.append([top_a, bot_b, bot_a])

    mesh = Mesh(verts, np.asarray(faces, dtype=np.int64), uvs=uvs)
    mesh.finalize()
    return mesh


# --------------------------------------------------------------------------- #
# Exporters
# --------------------------------------------------------------------------- #
def write_stl_binary(mesh: Mesh, path):
    v = mesh.vertices.astype(np.float32)
    f = mesh.faces.astype(np.int32)
    n = mesh.face_normals.astype(np.float32)
    with open(path, "wb") as fh:
        fh.write(b"\x00" * 80)
        fh.write(struct.pack("<I", len(f)))
        for i, tri in enumerate(f):
            fh.write(struct.pack("<3f", *n[i]))
            for vi in tri:
                fh.write(struct.pack("<3f", *v[vi]))
            fh.write(struct.pack("<H", 0))


def _pad4(buf: bytes, pad: bytes):
    return buf + pad * ((4 - len(buf) % 4) % 4)


def write_glb(mesh: Mesh, path, texture_path=None, base_color=(0.92, 0.90, 0.85), roughness=0.9):
    v = mesh.vertices.astype(np.float32)
    n = mesh.vertex_normals.astype(np.float32)
    idx = mesh.faces.astype(np.uint32)
    pos_bin = v.tobytes()
    nrm_bin = n.tobytes()
    idx_bin = idx.tobytes()

    uv_bin = b""
    has_uv = mesh.uvs is not None
    if has_uv:
        uv = mesh.uvs.astype(np.float32)
        uv_bin = uv.tobytes()

    img_bin = b""
    has_tex = has_uv and texture_path is not None and Path(texture_path).exists() and Image is not None
    if has_tex:
        img_bin = Path(texture_path).read_bytes()

    nvert = len(v)
    nidx = len(idx)

    chunks = [("POS", pos_bin), ("NRM", nrm_bin)]
    if has_uv:
        chunks.append(("UV", uv_bin))
    chunks.append(("IDX", idx_bin))
    if has_tex:
        chunks.append(("IMG", img_bin))

    offsets = {}
    off = 0
    for name, data in chunks:
        offsets[name] = off
        off += len(data)
    bin_len = off

    attributes = {"POSITION": 0, "NORMAL": 1}
    accessors = [
        {"bufferView": 0, "componentType": 5126, "count": nvert, "type": "VEC3",
         "min": v.min(axis=0).tolist(), "max": v.max(axis=0).tolist()},
        {"bufferView": 1, "componentType": 5126, "count": nvert, "type": "VEC3"},
    ]
    buffer_views = [
        {"buffer": 0, "byteOffset": offsets["POS"], "byteLength": len(pos_bin)},
        {"buffer": 0, "byteOffset": offsets["NRM"], "byteLength": len(nrm_bin)},
    ]
    next_acc = 2
    next_bv = 2
    if has_uv:
        attributes["TEXCOORD_0"] = next_acc
        buffer_views.append({"buffer": 0, "byteOffset": offsets["UV"], "byteLength": len(uv_bin)})
        accessors.append({"bufferView": next_bv, "componentType": 5126, "count": nvert, "type": "VEC2"})
        next_acc += 1
        next_bv += 1
    idx_bv = next_bv
    buffer_views.append({"buffer": 0, "byteOffset": offsets["IDX"], "byteLength": len(idx_bin)})
    accessors.append({"bufferView": idx_bv, "componentType": 5125, "count": nidx, "type": "SCALAR"})
    idx_acc = next_acc
    next_acc += 1
    next_bv += 1

    materials = []
    textures = []
    images = []
    material_index = None
    if has_tex:
        buffer_views.append({"buffer": 0, "byteOffset": offsets["IMG"], "byteLength": len(img_bin)})
        images.append({"bufferView": next_bv, "mimeType": "image/png"})
        textures.append({"source": 0})
        materials.append({
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": 0},
                "metallicFactor": 0.0,
                "roughnessFactor": roughness,
            },
            "doubleSided": True,
        })
        material_index = 0
    else:
        materials.append({
            "pbrMetallicRoughness": {"baseColorFactor": [base_color[0], base_color[1], base_color[2], 1.0],
                                     "metallicFactor": 0.0, "roughnessFactor": roughness},
            "doubleSided": True,
        })
        material_index = 0

    primitive = {"attributes": attributes, "indices": idx_acc, "mode": 4, "material": material_index}

    gltf = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [primitive]}],
        "materials": materials,
        "buffers": [{"byteLength": bin_len}],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }
    if has_tex:
        gltf["images"] = images
        gltf["textures"] = textures

    json_bin = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_pad = _pad4(json_bin, b" ")
    full_bin = b"".join(data for _, data in chunks)
    bin_padded = _pad4(full_bin, b"\x00")

    json_chunk = struct.pack("<II", len(json_pad), 0x4E4F534A) + json_pad
    bin_chunk = struct.pack("<II", len(bin_padded), 0x004E4942) + bin_padded
    total = 12 + 8 + len(json_pad) + 8 + len(bin_padded)
    header = struct.pack("<4sII", b"glTF", 2, total)
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(json_chunk)
        fh.write(bin_chunk)


def write_obj(mesh: Mesh, path):
    with open(path, "w") as fh:
        for x, y, z in mesh.vertices:
            fh.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")
        for a, b, c in mesh.faces:
            fh.write(f"f {a + 1} {b + 1} {c + 1}\n")
