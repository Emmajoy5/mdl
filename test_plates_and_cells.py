"""Tests for the cell and plate changes made after the first pilot feedback:
cell shapes and arrangements, the dish size, painted areas, glare and ink at the
edges of writing, and the Gram stain photo.

Run with:  python test_plates_and_cells.py
"""
import atexit
import io
import json
import os
import shutil
import sys
import tempfile

_TEST_DATA = tempfile.mkdtemp(prefix="mdl-plates-test-")
os.environ["APP_DATA_DIR"] = _TEST_DATA
for k in ("ADMIN_USERNAME", "ADMIN_PASSWORD", "LAB_NAME", "MDL_DEMO"):
    os.environ.pop(k, None)
atexit.register(shutil.rmtree, _TEST_DATA, True)

import numpy as np                       # noqa: E402
from PIL import Image                    # noqa: E402

import app as core                       # noqa: E402
import microbe3d as m3                   # noqa: E402
import plate_vision as pv                # noqa: E402
import shape_config as sc                # noqa: E402

core.app.config["TESTING"] = True
core.init_db_if_needed()
failures = []
PW = "Lead-pass-2026xyz"


def check(label, condition):
    print(f"{label:70s} -> {'OK' if condition else 'FAIL'}")
    if not condition:
        failures.append(label)


def one(sql, args=()):
    conn = core.db()
    row = conn.execute(sql, args).fetchone()
    conn.close()
    return row


def cell(**org):
    org.setdefault("species", "sp.")
    return sc.resolve_shape_config(org)["cell"]


# --- 1. which cell shape a strain gets ---------------------------------------------------
c = cell(genus="Bacillus", species="cereus", gram_stain="positive")
check("a Bacillus that is not in the species table is drawn as rods in chains",
      c["kind"] == "rod" and c["arrangement"] == "chain" and c["basis"] == "genus")
c = cell(genus="Staphylococcus", species="aureus")
check("Staphylococcus: cocci in clusters", (c["kind"], c["arrangement"]) == ("coccus", "cluster"))
c = cell(genus="Streptococcus", species="thermophilus")
check("Streptococcus: cocci in chains", (c["kind"], c["arrangement"]) == ("coccus", "chain"))
c = cell(genus="Pediococcus", species="damnosus")
check("Pediococcus damnosus keeps its tetrads", (c["kind"], c["arrangement"]) == ("coccus", "tetrad"))
c = cell(genus="Vibrio", species="fischeri", gram_stain="negative")
check("Vibrio: curved rods, coloured Gram negative",
      c["kind"] == "curved_rod" and tuple(c["color"]) == sc.GRAM_NEGATIVE_COLOR)
c = cell(genus="Saccharomyces", species="cerevisiae", gram_stain="positive")
check("yeast stays a budding oval cell, cream, whatever the Gram field says",
      c["kind"] == "ellipsoid" and c["arrangement"] == "budding" and tuple(c["color"]) == sc.YEAST_COLOR)
c = cell(genus="Unknownia", species="x", gram_stain="positive")
check("an unknown genus says the shape is only a guess from the Gram result", c["basis"] == "gram")
c = cell(genus="Novibacillus", species="x")
check("an unknown genus with 'bacillus' in its name is drawn as rods", c["kind"] == "rod")
c = cell(genus="Unknownia", species="x", gram_stain="positive",
         morphology_notes="Gram-positive rods in chains, spore forming")
check("morphology notes decide the shape ('rods in chains')",
      (c["kind"], c["arrangement"], c["basis"]) == ("rod", "chain", "notes"))
c = cell(genus="Unknownia", species="x", morphology_notes="coccobacilli, mostly in pairs")
check("'coccobacilli' is not read as 'cocci'", (c["kind"], c["arrangement"]) == ("coccobacillus", "pair"))
c = cell(genus="Lactobacillus", species="sp.", morphology_notes="Round, cream, glossy colonies")
check("colony words in the notes do not change the cell", c["kind"] == "rod" and c["basis"] == "genus")
c = cell(genus="Bacillus", species="cereus", cell_shape="coccus", cell_arrangement="cluster")
check("a shape chosen on the page wins over everything else",
      (c["kind"], c["arrangement"], c["basis"]) == ("coccus", "cluster", "chosen"))
c = cell(genus="Bacillus", species="cereus", cell_arrangement="palisade")
check("choosing only an arrangement keeps the genus shape", (c["kind"], c["arrangement"]) == ("rod", "palisade"))
check("describe_cell reads naturally", sc.describe_cell(dict(kind="coccus", arrangement="cluster"))
      == "cocci in clusters")

# --- 2. every shape and arrangement builds a closed, printable model --------------------
ok = True
for kind, arrs in sc.ARRANGEMENTS_FOR.items():
    for arr in arrs:
        params = dict(sc.CELL_DIMENSIONS[kind], kind=kind, arrangement=arr)
        mesh, scale = m3.cell_assembly(params)
        ext = mesh.vertices.max(axis=0) - mesh.vertices.min(axis=0)
        ok &= mesh.is_watertight() and abs(float(ext.max()) - m3.PRINT_SIZE_MM) < 1e-6 and scale > 0
check("all 18 shape and arrangement pairs are watertight and scaled to print", ok)
check("the page and the geometry agree on which arrangements go with which shape",
      {k: tuple(v) for k, v in sc.ARRANGEMENTS_FOR.items()} == {k: tuple(v) for k, v in m3.ARRANGEMENTS.items()})
sp = m3.spiral_cell()
ext = sp.vertices.max(axis=0) - sp.vertices.min(axis=0)
check("a spiral cell is a helix (long, with turns), not a straight rod", ext[0] > 2.5 * max(ext[1], ext[2]) > 0)

# --- 3. the lab, a strain, and the page --------------------------------------------------
c0 = core.app.test_client()
c0.post("/setup", data=dict(setup_code=core.setup_code(), lab_name="Brewing QC Lab", username="lead",
                            password=PW, full_name="Dr Lead"))
lead = core.app.test_client()
lead.post("/login", data=dict(username="lead", password=PW))
r = lead.post("/species/new", data=dict(genus="Bacillus", species="cereus", gram_stain="positive",
                                        storage_freezer="F1", storage_box="B1"), follow_redirects=True)
cid = one("SELECT collection_id FROM organisms WHERE genus='Bacillus' AND species='cereus'")[0]
lead.post(f"/species/{cid}/generate")
page = lead.get(f"/species/{cid}").data
check("the strain page says what the cell model shows and why",
      b"Rods in chains" in page and b"typical for the genus" in page)
note = one("SELECT m.notes FROM models m JOIN organisms o USING (organism_id) "
           "WHERE o.collection_id=? AND model_type='cell'", (cid,))[0]
check("the cell model's note says 'rods in chains'", "rods in chains" in note)

r = lead.post(f"/species/{cid}/cell-shape", data=dict(cell_shape="coccus", cell_arrangement="cluster"),
              follow_redirects=True)
row = one("SELECT cell_shape, cell_arrangement FROM organisms WHERE collection_id=?", (cid,))
note = one("SELECT m.notes FROM models m JOIN organisms o USING (organism_id) "
           "WHERE o.collection_id=? AND model_type='cell'", (cid,))[0]
check("choosing a shape saves it and rebuilds the cell model",
      tuple(row) == ("coccus", "cluster") and "cocci in clusters" in note and b"corrected on this page" in r.data)
check("the change is in the strain's history",
      one("SELECT COUNT(*) FROM audit_log WHERE entity='organism' AND detail LIKE '%cell_shape%'")[0] >= 1)
r = lead.post(f"/species/{cid}/cell-shape", data=dict(cell_shape="spiral", cell_arrangement="tetrad"),
              follow_redirects=True)
check("a shape and arrangement that don't go together are refused, nothing changed",
      b"don&#39;t go with" in r.data or b"don't go with" in r.data)
check("...and the saved choice is untouched",
      tuple(one("SELECT cell_shape, cell_arrangement FROM organisms WHERE collection_id=?", (cid,)))
      == ("coccus", "cluster"))
check("an unknown shape is rejected", lead.post(f"/species/{cid}/cell-shape",
                                                 data=dict(cell_shape="cube")).status_code == 400)
lead.post(f"/species/{cid}/cell-shape", data=dict(cell_shape="", cell_arrangement=""))
check("'Automatic' goes back to the genus shape",
      tuple(one("SELECT cell_shape, cell_arrangement FROM organisms WHERE collection_id=?", (cid,)))
      == (None, None))
check("someone signed out cannot change the shape",
      core.app.test_client().post(f"/species/{cid}/cell-shape",
                                  data=dict(cell_shape="rod")).status_code in (302, 401, 403))

# --- 4. the dish size -----------------------------------------------------------------
def photo_bytes(path):
    with open(path, "rb") as fh:
        return io.BytesIO(fh.read())


check("the upload form offers the plate sizes", b'name="plate_size"' in lead.get(f"/species/{cid}/upload").data)
r = lead.post(f"/species/{cid}/upload", data=dict(collection_id=cid, image=(photo_bytes("test_fixtures/handwritten_label_plate.jpg"),
                                                                    "plate.jpg"),
                                          media="WL agar", plate_size="60"),
              content_type="multipart/form-data", follow_redirects=True)
img = one("SELECT * FROM plate_images ORDER BY image_id DESC LIMIT 1")
check("a plate uploaded as 60 mm is stored as 60 mm", img is not None and img["plate_diameter_mm"] == 60)
up = lead.get(f"/species/{cid}/upload").data
check("the next upload offers 60 mm first, the size the lab used last",
      b'value="60" selected' in up)
lead.post(f"/species/{cid}/upload", data=dict(collection_id=cid, image=(photo_bytes("test_fixtures/handwritten_label_plate.jpg"),
                                                               "p2.jpg"), plate_size="47"),
          content_type="multipart/form-data")
check("a size that is not on the list falls back to 90 mm",
      one("SELECT plate_diameter_mm FROM plate_images ORDER BY image_id DESC LIMIT 1")[0] == 90)
r = lead.post(f"/species/{cid}/rebuild/{img['image_id']}", follow_redirects=True)
model_note = one("SELECT notes FROM models WHERE model_type='photo_relief' AND file_path LIKE ?",
                 (f"%img{img['image_id']}-%",))
check("the photo relief is built on the plate's own dish size", model_note and "60 mm dish" in model_note[0])
lead.post(f"/species/{cid}/generate")
cnote = one("SELECT m.notes FROM models m JOIN organisms o USING (organism_id) "
            "WHERE o.collection_id=? AND model_type='colony'", (cid,))[0]
check("the illustrated plate uses the size of the strain's latest photo",
      "90 mm dish" in cnote)  # the latest photo of this strain was the 90 mm fallback
d90 = pv.analyse_plate("test_fixtures/handwritten_label_plate.jpg", diameter_mm=90)
d60 = pv.analyse_plate("test_fixtures/handwritten_label_plate.jpg", diameter_mm=60)
check("the analysis reports which dish size it assumed",
      d90["morphology"]["plate_diameter_mm"] == 90 and d60["morphology"]["plate_diameter_mm"] == 60)

# --- 5. painting over writing ----------------------------------------------------------
page = lead.get(f"/species/{cid}").data
check("each plate photo has the 'fix the 3D' panel with a paint canvas",
      b"Fix the 3D for this photo" in page and b"paint-canvas" in page and b"plate-paint.js" in page)
whole = json.dumps([[x / 10, y / 10, 0.12] for x in range(11) for y in range(11)])
r = lead.post(f"/species/{cid}/plate/{img['image_id']}/fix-3d",
              data=dict(plate_size="60", ignore_areas=whole), follow_redirects=True)
row = one("SELECT plate_diameter_mm, ignore_areas, cv_morphology_json FROM plate_images WHERE image_id=?",
          (img["image_id"],))
morph = json.loads(row["cv_morphology_json"])
check("painted areas are saved and the relief is rebuilt", json.loads(row["ignore_areas"]) and b"rebuilt" in r.data)
check("painting over the whole plate leaves no growth", morph["coverage_pct"] < 0.5 and morph["ignored_pct"] > 90)
bad = json.dumps([[0.5, 0.5, 0.05], [2, 0.5, 0.1], [0.5, 0.5, 9], "x", [0.1]])
lead.post(f"/species/{cid}/plate/{img['image_id']}/fix-3d", data=dict(plate_size="60", ignore_areas=bad))
check("out-of-range or malformed dabs are dropped, good ones kept",
      json.loads(one("SELECT ignore_areas FROM plate_images WHERE image_id=?", (img["image_id"],))[0])
      == [[0.5, 0.5, 0.05]])
lead.post(f"/species/{cid}/plate/{img['image_id']}/fix-3d", data=dict(plate_size="90", ignore_areas="not json"))
row = one("SELECT plate_diameter_mm, ignore_areas FROM plate_images WHERE image_id=?", (img["image_id"],))
check("text that is not JSON clears the painting rather than breaking", row["ignore_areas"] is None
      and row["plate_diameter_mm"] == 90)
check("painting needs a sign-in",
      core.app.test_client().post(f"/species/{cid}/plate/{img['image_id']}/fix-3d",
                                  data=dict(ignore_areas=whole)).status_code in (302, 401, 403))
r0 = pv.analyse_plate("test_fixtures/handwritten_label_plate.jpg")
m = pv.ignore_mask([[0.5, 0.5, 0.1]], (100, 100), (0, 0, 100, 100), 50)
check("a dab maps to the right place on the crop", m[25, 25] and not m[2, 2] and abs(m.sum() - np.pi * 25) < 12)
check("painting nothing changes nothing", pv.ignore_mask([], (10, 10), (0, 0, 10, 10), 10) is None)

# --- 6. glare and ink beside writing ---------------------------------------------------
hw = r0["morphology"]
check("teal plate: six colonies on the agar, no glare or ink (coverage 5 to 9%)", 5 <= hw["coverage_pct"] <= 9)
grid = pv.analyse_plate("test_fixtures/grid_small_dark_colonies.jpg")["morphology"]
check("grid plate: dark colonies in rows are still found (coverage 5.5% or more)", grid["coverage_pct"] >= 5.5)
n = 400
yy, xx = np.mgrid[0:n, 0:n]
agar = np.full((n, n, 3), (120, 190, 175), np.float64)            # teal medium
agar[((xx - 200) ** 2 + (yy - 200) ** 2) > 190 ** 2] = (40, 40, 40)
th = np.arctan2(yy - 200, xx - 200)
rr = np.hypot(xx - 200, yy - 200)
glare = (rr > 160) & (rr < 178) & (np.abs(th - np.pi) < 0.35)     # a white arc at 9 o'clock
agar[glare] = (235, 240, 238)
col = ((xx - 250) ** 2 + (yy - 200) ** 2) < 14 ** 2                 # one real colony
agar[col] = (20, 110, 110)
Image.fromarray(agar.astype(np.uint8)).save(os.path.join(_TEST_DATA, "glare.png"))
g = pv.analyse_plate(os.path.join(_TEST_DATA, "glare.png"))
h = g["height"]
check("a white glare arc at the rim is not raised", float(h[:, : n // 6].max()) < 0.05 if h.shape[0] == n else
      float(h[:, : h.shape[1] // 6].max()) < 0.05)
check("...while the colony beside it is", float(h.max()) > 0.3)

# --- 7. the Gram stain photo -----------------------------------------------------------
page = lead.get(f"/species/{cid}").data
check("the strain page has a visible 'Add a Gram stain photo' button", b"Add a Gram stain photo" in page)
png = io.BytesIO()
Image.new("RGB", (40, 40), (120, 40, 140)).save(png, "PNG")
png.seek(0)
r = lead.post(f"/species/{cid}/gram-stain", data=dict(image=(png, "gram.png")),
              content_type="multipart/form-data", follow_redirects=True)
check("a Gram stain photo can be saved without a result", b"Gram stain photo saved" in r.data)
check("...and it is listed with the strain",
      one("SELECT COUNT(*) FROM gram_stain_images WHERE file_path IS NOT NULL")[0] == 1)
r = lead.post(f"/species/{cid}/gram-stain", data=dict(), follow_redirects=True)
check("an empty Gram stain form saves nothing and says why",
      b"Choose a result, the cell shape or a photo" in r.data
      and one("SELECT COUNT(*) FROM gram_stain_images")[0] == 1)

# --- 8. the cell shape recorded with a Gram stain drives the model ----------------------
lead.post("/species/new", data=dict(genus="Unknownia", species="sp.", storage_freezer="F1", storage_box="B2"))
uid = one("SELECT collection_id FROM organisms WHERE genus='Unknownia'")[0]
lead.post(f"/species/{uid}/generate")
page = lead.get(f"/species/{uid}").data
check("the Gram stain form asks for the cell shape and arrangement seen",
      b'id="gram_cell_shape"' in page and b'id="gram_cell_arrangement"' in page and b"Not recorded" in page)
r = lead.post(f"/species/{uid}/gram-stain", data=dict(result="positive", stain_date="2026-11-10",
                                                     cell_shape="rod", cell_arrangement="chain"),
              follow_redirects=True)
note = one("SELECT m.notes FROM models m JOIN organisms o USING (organism_id) "
           "WHERE o.collection_id=? AND model_type='cell'", (uid,))[0]
check("a Gram stain with 'rods in chains' rebuilds the cell model to match",
      b"Cell model updated: rods in chains" in r.data and "rods in chains" in note)
page = lead.get(f"/species/{uid}").data
check("the page says the shape comes from that Gram stain",
      b"as recorded with the Gram stain of 2026-11-10" in page)
check("the Gram stain history shows what was seen", b"Rods in chains" in page)
lead.post(f"/species/{uid}/cell-shape", data=dict(cell_shape="coccus", cell_arrangement="pair"))
check("a correction on the model wins over the Gram record",
      b"Cocci in pairs" in lead.get(f"/species/{uid}").data)
r = lead.post(f"/species/{uid}/gram-stain", data=dict(result="positive", stain_date="2026-11-12",
                                                     cell_shape="coccus", cell_arrangement="cluster"),
              follow_redirects=True)
check("a newer Gram stain with a shape replaces the earlier correction",
      tuple(one("SELECT cell_shape, cell_arrangement FROM organisms WHERE collection_id=?", (uid,)))
      == (None, None) and b"Cell model updated: cocci in clusters" in r.data)
r = lead.post(f"/species/{uid}/gram-stain", data=dict(cell_arrangement="chain"), follow_redirects=True)
check("an arrangement without a shape is refused", b"Choose the cell shape as well" in r.data)
r = lead.post(f"/species/{uid}/gram-stain", data=dict(cell_shape="spiral", cell_arrangement="cluster"),
              follow_redirects=True)
check("a shape and arrangement that don't go together are refused", b"go with" in r.data)
n_before = one("SELECT COUNT(*) FROM gram_stain_images WHERE organism_id="
               "(SELECT organism_id FROM organisms WHERE collection_id=?)", (uid,))[0]
check("...and neither refused form saved anything", n_before == 2)
latest = one("SELECT image_id FROM gram_stain_images WHERE organism_id="
             "(SELECT organism_id FROM organisms WHERE collection_id=?) ORDER BY image_id DESC", (uid,))[0]
lead.post(f"/species/{uid}/gram-stain/{latest}/delete", data=dict(reason="wrong strain"))
note = one("SELECT m.notes FROM models m JOIN organisms o USING (organism_id) "
           "WHERE o.collection_id=? AND model_type='cell'", (uid,))[0]
check("voiding that Gram stain takes the model back to the one before", "rods in chains" in note)
r = lead.post(f"/species/{uid}/gram-stain", data=dict(cell_shape="curved_rod"), follow_redirects=True)
check("a Gram stain can record just the shape", b"Cell model updated: single curved rods" in r.data)

# --- 9. the strain's Gram result is the latest stain, not the earliest -------------------
lead.post(f"/species/{uid}/gram-stain", data=dict(result="negative", stain_date="2026-11-11"))
lead.post(f"/species/{uid}/gram-stain", data=dict(result="variable", stain_date="2025-01-05"))
check("an older stain entered later does not replace the latest result",
      one("SELECT gram_stain FROM organisms WHERE collection_id=?", (uid,))[0] == "negative")

# --- 10. the feedback question about lost data -----------------------------------------
import feedback_survey as fs                                        # noqa: E402
q = [q for sec in fs.SECTIONS for q in sec.get("questions", []) if q["key"] == "data_problem"][0]
check("the data question asks about lost or changed records, and says a wrong 3D model is not that",
      "lose a record" in q["label"] and "3D model" in q.get("help", ""))

# --- 11. the agar edge, not a grid ring or the glass wall ------------------------------
def edge_ratio(path):
    small, orig, scale, (px, py) = pv._load_padded(path, 1024)
    c = pv.detect_dish_circle(small)
    cx, cy, r = c[0] * scale - px, c[1] * scale - py, c[2] * scale
    return pv._refine_agar_circle(orig, cx, cy, r)[2] / r


grid_ratio = edge_ratio("test_fixtures/grid_small_dark_colonies.jpg")
check("grid plate: the agar edge is found, not a ring of the counting grid inside it",
      0.72 <= grid_ratio <= 0.80)
check("teal plate: the agar edge is found, not the glass wall outside it",
      0.82 <= edge_ratio("test_fixtures/handwritten_label_plate.jpg") <= 0.88)
src = Image.open("test_fixtures/grid_small_dark_colonies.jpg").convert("RGB")

# --- 12. photos are stored in less space, the right way up ------------------------------
import photo_store                                                  # noqa: E402
from PIL import JpegImagePlugin                                     # noqa: E402

rng = np.random.default_rng(3)
big = np.asarray(src.resize((4500, 6000), Image.LANCZOS)).astype(float) + rng.normal(0, 2.0, (6000, 4500, 3))
big = Image.fromarray(np.clip(big, 0, 255).astype(np.uint8))
buf = io.BytesIO()
big.save(buf, "JPEG", quality=96, subsampling=0)
raw = buf.getvalue()
new, ext, info = photo_store.compact(raw, ".jpg")
out = Image.open(io.BytesIO(new))
check("a 27 megapixel phone photo is capped at 4096 pixels and stored in under half the space",
      max(out.size) == 4096 and len(new) < 0.5 * len(raw) and info["resized"] and ext == ".jpg")
check("...with full colour resolution (4:4:4), not halved colour",
      JpegImagePlugin.get_sampling(out) == 0)
p_raw = os.path.join(_TEST_DATA, "big_raw.jpg")
p_new = os.path.join(_TEST_DATA, "big_new.jpg")
open(p_raw, "wb").write(raw)
open(p_new, "wb").write(new)
a_raw = pv.analyse_plate(p_raw)["morphology"]["coverage_pct"]
a_new = pv.analyse_plate(p_new)["morphology"]["coverage_pct"]
check("...and the plate analysis reads the same colony coverage (within 0.5 points)",
      abs(a_raw - a_new) <= 0.5)

small = src.resize((800, 600))
exif = Image.Exif()
exif[274] = 6                                                      # 'turn 90 degrees clockwise'
buf = io.BytesIO()
small.save(buf, "JPEG", quality=90, exif=exif.tobytes())
new, ext, info = photo_store.compact(buf.getvalue(), ".jpg")
turned = Image.open(io.BytesIO(new))
check("a small photo saved sideways is turned upright and the note dropped",
      info["turned"] and turned.size == (600, 800) and turned.getexif().get(274) in (None, 1))
p_side = os.path.join(_TEST_DATA, "side.jpg")
p_up = os.path.join(_TEST_DATA, "up.jpg")
open(p_side, "wb").write(buf.getvalue())
open(p_up, "wb").write(new)
check("the analysis reads a sideways photo the way the browser shows it",
      pv._load_padded(p_side)[1].size == pv._load_padded(p_up)[1].size == (600, 800))

exif = Image.Exif()
exif[34853] = {1: "N", 2: (51.0, 30.0, 0.0)}
buf = io.BytesIO()
small.save(buf, "JPEG", quality=90, exif=exif.tobytes())
new, ext, info = photo_store.compact(buf.getvalue(), ".jpg")
check("a photo with GPS location is stored without it",
      info["had_location"] and 34853 not in Image.open(io.BytesIO(new)).getexif())
buf = io.BytesIO()
small.save(buf, "JPEG", quality=90)
plain = buf.getvalue()
new, ext, info = photo_store.compact(plain, ".jpg")
check("a small photo that is upright and has no location is kept byte for byte",
      new == plain and not info["changed"])
rgba = Image.new("RGBA", (5000, 300), (10, 120, 60, 128))
buf = io.BytesIO()
rgba.save(buf, "PNG")
new, ext, info = photo_store.compact(buf.getvalue(), ".png")
check("a large picture with transparency stays a PNG", ext == ".png" and Image.open(io.BytesIO(new)).mode == "RGBA")
again, _, info2 = photo_store.compact(photo_store.compact(raw, ".jpg")[0], ".jpg")
check("a photo that is already compact is not encoded a second time",
      not info2["changed"] and again == photo_store.compact(raw, ".jpg")[0])

# --- 13. uploads, the lab setting, and photos already stored ---------------------------
def upload_big(name):
    return lead.post(f"/species/{cid}/upload", data=dict(image=(io.BytesIO(raw), name), plate_size="90"),
                     content_type="multipart/form-data", follow_redirects=True)


r = upload_big("big.jpg")
stored = one("SELECT file_path FROM plate_images ORDER BY image_id DESC LIMIT 1")[0]
size_stored = os.path.getsize(os.path.join(core.DATA_DIR, stored))
check("an uploaded phone photo is stored compactly and the page says how much was saved",
      size_stored < 0.5 * len(raw) and b"Stored 1 large photo(s) in" in r.data)
check("the photo storage setting is on the admin page", b"Photo storage" in lead.get("/admin").data)
lead.post("/admin/photo-storage", data=dict(keep_original_photos="1"))
r = upload_big("big2.jpg")
stored = one("SELECT file_path FROM plate_images ORDER BY image_id DESC LIMIT 1")[0]
with open(os.path.join(core.DATA_DIR, stored), "rb") as fh:
    kept = fh.read()
check("a lab that keeps originals gets the file byte for byte", kept == raw)
check("...and the change of setting is in the audit log",
      one("SELECT COUNT(*) FROM audit_log WHERE detail LIKE '%keep_original_photos%'")[0] == 1)
lead.post("/admin/photo-storage", data=dict(keep_original_photos="0"))
check("someone signed out cannot change the photo setting",
      core.app.test_client().post("/admin/photo-storage", data=dict(keep_original_photos="1")).status_code
      in (302, 401, 403))

import manage                                                       # noqa: E402
old = io.StringIO()
sys.stdout, real_out = old, sys.stdout
try:
    manage.compact_photos()
finally:
    sys.stdout = real_out
check("compact-photos without --apply only reports", "Would store 1 photo(s)" in old.getvalue()
      and open(os.path.join(core.DATA_DIR, stored), "rb").read() == raw)
sys.stdout = io.StringIO()
try:
    manage.compact_photos("--apply")
finally:
    sys.stdout = real_out
check("compact-photos --apply stores the old photo compactly",
      os.path.getsize(os.path.join(core.DATA_DIR, stored)) < 0.5 * len(raw))
served = lead.get("/uploads/" + os.path.basename(stored))
check("the compacted photo is still served at the same address",
      served.status_code == 200 and len(served.data) < 0.5 * len(raw))

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(" -", f)
    raise SystemExit(1)
print("All plate and cell checks passed.")
