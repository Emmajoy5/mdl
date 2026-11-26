"""Tests for the Microbial Digital Library, run in-process with Flask's test client.

An installation holds one lab. The suite sets up "Riverside Brewing QC Lab" through
the first-run setup page, with the example strains, and runs the functional checks
against it. The app still filters every query by lab, so a second lab is added
directly in the database further down to check that those filters hold.
"""
import base64
import io
import json
import os

# Run against a throwaway data folder, never the installation's own database,
# photos and models -- so the suite can be run twice, or on a live install.
import atexit, shutil, tempfile
_TEST_DATA = tempfile.mkdtemp(prefix="mdl-test-")
os.environ["APP_DATA_DIR"] = _TEST_DATA
atexit.register(shutil.rmtree, _TEST_DATA, True)
for _k in ("ADMIN_USERNAME", "ADMIN_PASSWORD", "LAB_NAME"):   # the suite uses the setup page
    os.environ.pop(_k, None)

from app import app, init_db_if_needed, db, LOGIN_LOCKOUT_THRESHOLD, _ensure_join_codes, create_lab, setup_code

# CSRF protection (see app.py's before_request _enforce_csrf) is skipped
# whenever app.config["TESTING"] is set: this suite drives routes directly
# through the test client with plain data dicts rather than rendering and
# resubmitting an actual page, so it never has a real token to send. The
# protection itself gets one dedicated check further down instead, toggling
# TESTING off around that single request.
app.config["TESTING"] = True
init_db_if_needed()

# 1x1 transparent PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

c = app.test_client()
failures = []


def check(label, condition):
    status = "OK" if condition else "FAIL"
    print(f"{label:55s} -> {status}")
    if not condition:
        failures.append(label)


import importlib.util as _ilu
HAS_SEGNO = _ilu.find_spec("segno") is not None
skipped = []


def check_qr(label, condition):
    """QR codes need the optional segno package; skip, don't fail, without it."""
    if not HAS_SEGNO:
        print(f"{label:55s} -> SKIP (segno not installed)")
        skipped.append(label)
        return
    check(label, condition)


# --- First run: the setup page ---------------------------------------------------
r = c.get("/", follow_redirects=False)
check("a new installation sends every page to setup", r.status_code == 302 and "/setup" in r.headers.get("Location", ""))
check("...including the sign-in page", "/setup" in c.get("/login").headers.get("Location", ""))
r = c.get("/setup")
check("GET /setup", r.status_code == 200 and b"Setup code" in r.data)
from app import SETUP_CODE_FILE
code0 = setup_code()
check("the setup code is saved in the data folder", SETUP_CODE_FILE.exists() and code0 in SETUP_CODE_FILE.read_text())
lab_a_form = dict(lab_name="Riverside Brewing QC Lab", username="adminA", password="adminA-pass-123",
                  full_name="Admin A", include_examples="on")
r = c.post("/setup", data=dict(lab_a_form, setup_code="WRONG-CODE"), follow_redirects=True)
check("setup with a wrong code is refused", b"setup code isn" in r.data)
r = c.post("/setup", data=dict(lab_a_form, setup_code=code0, password="short"), follow_redirects=True)
check("setup refuses a weak password", b"at least" in r.data)
conn = db()
check("...and nothing was created", conn.execute("SELECT COUNT(*) FROM tenants").fetchone()[0] == 0)
conn.close()
r = c.post("/setup", data=dict(lab_a_form, setup_code=code0.lower().replace("-", " ")), follow_redirects=True)
check("setup with the right code creates the lab", r.status_code == 200 and b"Riverside Brewing QC Lab" in r.data)
check("the setup code is deleted once used", not SETUP_CODE_FILE.exists())
check("the setup page is closed once the lab exists", "/login" in app.test_client().get("/setup").headers.get("Location", ""))
r = app.test_client().post("/setup", data=dict(lab_a_form, lab_name="Second Lab", username="intruder",
                                               setup_code=code0), follow_redirects=True)
conn = db()
check("a second setup attempt creates nothing", conn.execute("SELECT COUNT(*) FROM tenants").fetchone()[0] == 1
      and conn.execute("SELECT 1 FROM users WHERE username='intruder'").fetchone() is None)
check("no account sits above the lab admins", conn.execute(
    "SELECT COUNT(*) FROM users WHERE role NOT IN ('admin','user')").fetchone()[0] == 0)
conn.close()
c.get("/logout")

# --- Accounts & login ---------------------------------------------------------------
r = c.get("/", follow_redirects=False)
check("anonymous GET / redirects to login", r.status_code == 302 and "/login" in r.headers.get("Location", ""))

r = c.get("/login")
check("GET /login", r.status_code == 200)
check("login page offers a way to ask to join", b"Ask to join" in r.data)

r = c.get("/signup")
check("GET /signup", r.status_code == 200 and b"Join code" in r.data and b"lab_name" not in r.data)

r = c.post("/login", data=dict(username="adminA", password="wrong-password"), follow_redirects=True)
check("login with wrong password is rejected", b"Incorrect username or password" in r.data)

# --- Login lockout ---------------------------------------------------------------
for _ in range(LOGIN_LOCKOUT_THRESHOLD - 1):
    c.post("/login", data=dict(username="adminA", password="wrong-password"), follow_redirects=True)
r = c.post("/login", data=dict(username="adminA", password="wrong-password"), follow_redirects=True)
check(f"account is locked after {LOGIN_LOCKOUT_THRESHOLD} failed attempts",
     b"Too many failed attempts" in r.data)

r = c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)
check("correct password is still rejected while the account is locked", b"Too many failed attempts" in r.data)

from app import reset_password
conn = db()
check("a password reset on the server clears the lockout", reset_password(conn, "adminA", "adminA-pass-123"))
check("...and an unknown username is reported", not reset_password(conn, "nobody-here", "x-pass-12345"))
conn.close()

r = c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)
check("Lab A admin login succeeds", r.status_code == 200 and b"Riverside Brewing QC Lab" in r.data)

r = c.get("/admin")
check("Lab A admin can reach /admin", r.status_code == 200)
check("admin page shows the Users section", b"Users" in r.data)
check("admin page shows the Source labs section", b"Source lab" in r.data)
check("admin page shows the lab's own name", b"Riverside Brewing QC Lab" in r.data)

# Renaming the lab's own account (tenant), distinct from the source-labs list
r = c.post("/admin/lab-name", data=dict(name="Riverside QC (Renamed)"), follow_redirects=True)
check("admin can rename their own lab", r.status_code == 200)
check("renamed lab name shown on admin page", b"Riverside QC (Renamed)" in r.data)

# Create a regular-user account + a source lab from the admin page, used
# throughout the rest of the suite (sample-source tests need a source lab).
r = c.post("/admin/users", data=dict(
    username="labuser", password="labuser-pass-123", role="user", full_name="Lab User"),
    follow_redirects=True)
check("admin can create a regular-user account", r.status_code == 200)

r = c.post("/admin/labs", data=dict(name="Central QC Lab"), follow_redirects=True)
check("admin can add a source lab", r.status_code == 200)
check("new source lab appears on the admin page", b"Central QC Lab" in r.data)

conn = db()
test_lab = conn.execute("SELECT * FROM labs WHERE name='Central QC Lab'").fetchone()
tenant_a = conn.execute("SELECT * FROM tenants WHERE name='Riverside QC (Renamed)'").fetchone()
conn.close()
check("source lab was actually stored", test_lab is not None)
check("Lab A's tenant row was renamed", tenant_a is not None)

# Regular users can't reach admin-only routes (export, admin pages) -- but can
# still fully manage strain/sample records, same as before accounts existed.
c.get("/logout")  # end the admin session before switching to the regular user
r = c.post("/login", data=dict(username="labuser", password="labuser-pass-123"), follow_redirects=True)
check("regular-user login succeeds", r.status_code == 200)

r = c.get("/export")
check("regular user is forbidden from /export", r.status_code == 403)
r = c.get("/export/xlsx")
check("regular user is forbidden from /export/xlsx", r.status_code == 403)
r = c.get("/admin")
check("regular user is forbidden from /admin", r.status_code == 403)

r = c.get("/species/new")
check("regular user can still open Add strain", b'name="sample_source_type"' in r.data)
check("Add strain offers the Source-lab/Online source choice", b"Online reference database" in r.data)
check("Add strain's source-lab dropdown lists the admin-added lab", b"Central QC Lab" in r.data)

r = c.get("/logout", follow_redirects=True)
check("logout redirects to login", b"Log in" in r.data or b"Logged out" in r.data)
r = c.get("/", follow_redirects=False)
check("after logout, / requires login again", r.status_code == 302)

# Log back in as Lab A's admin for the rest of the suite (seeding/CRUD checks
# below assume full access, same as a regular user would have for their own
# data).
r = c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)
check("Lab A admin re-login for the rest of the suite", r.status_code == 200)

# --- Landing page ---------------------------------------------------------
r = c.get("/")
check("GET / (landing page)", r.status_code == 200)
check("landing page has brand", b"Microbial Digital Library" in r.data)
check("landing page has feature tiles", b"Browse the catalog" in r.data)
check("nav shows the current lab's name", b"Riverside QC (Renamed)" in r.data)

# --- Catalog ---------------------------------------------------------------
r = c.get("/catalog")
check("GET /catalog", r.status_code == 200)
check("catalog shows seeded strain", b"MDL-SACCER-001" in r.data)

r = c.get("/catalog?type=validation")
check("GET /catalog?type=validation", r.status_code == 200)
check("validation filter shows Bacillus subtilis", b"Bacillus subtilis" in r.data)
check("validation filter hides library strain", b"MDL-SACCER-001" not in r.data)

r = c.get("/catalog?type=library")
check("GET /catalog?type=library", r.status_code == 200)
check("library filter shows Saccharomyces", b"MDL-SACCER-001" in r.data)
check("library filter hides validation strain", b"MDL-BACSUB-007" not in r.data)

# --- Canonical role values (no near-duplicate strings) ---------------------
conn = db()
roles = [row["role"] for row in conn.execute(
    "SELECT DISTINCT role FROM brewing_relevance WHERE role IS NOT NULL")]
conn.close()
check("brewing roles are canonical single words", all(" " not in r and "/" not in r for r in roles))

# --- Species detail page ----------------------------------------------------
r = c.get("/species/MDL-SACCER-001")
check("GET species page", r.status_code == 200)
check("species page has model-viewer", b"model-viewer" in r.data)

r = c.get("/species/MDL-BACSUB-007")
check("GET Bacillus subtilis (validation strain)", r.status_code == 200)
check("Bacillus subtilis page shows validation badge", b"validation" in r.data)

# --- Dark-theme calendar date-picker (static/calendar-picker.js) ------------
check("revival-date field uses the calendar date-picker", b'name="revival_date"' in r.data and b'class="date-input"' in r.data)
r = c.get("/species/MDL-SACCER-001/edit")
check("GET edit species page", r.status_code == 200)
check("storage date fields use the calendar date-picker",
     r.data.count(b'class="date-input"') >= 2)
r = c.get("/")
check("calendar-picker.js is linked from the base layout", b"calendar-picker.js" in r.data)
r = c.get("/static/calendar-picker.js")
check("GET calendar-picker.js", r.status_code == 200)

# --- Viability dashboard (home page) ----------------------------------------
r = c.get("/")
check("home page shows revival dashboard section", b"needing revival" in r.data or b"revival" in r.data.lower())
check("overdue Bacillus subtilis appears on dashboard", b"Bacillus subtilis" in r.data)
check("overdue strain shows a red badge", b'badge b-bad">overdue by' in r.data)

check("due-soon Brettanomyces appears on dashboard (amber band)", b"Brettanomyces" in r.data)
check("due-soon strain shows an amber badge", b'badge b-warn">due in' in r.data)

# --- Traffic-light revival badge (green/amber/red/muted) --------------------
from app import revival_badge, compute_revival_status
from datetime import date as _date

b_overdue = revival_badge(compute_revival_status("2026-01-01", 90, today=_date(2026, 4, 10)))
check("badge: overdue -> red", b_overdue["color"] == "red" and b_overdue["cls"] == "b-bad")

b_due_today = revival_badge(compute_revival_status("2026-01-01", 90, today=_date(2026, 4, 1)))
check("badge: due today -> red", b_due_today["color"] == "red")

b_due_soon = revival_badge(compute_revival_status("2026-01-01", 90, today=_date(2026, 3, 28)))
check("badge: due in 4 days -> amber", b_due_soon["color"] == "amber" and "4 day" in b_due_soon["label"])

b_not_due = revival_badge(compute_revival_status("2026-01-01", 90, today=_date(2026, 3, 1)))
check("badge: due in 31 days -> green", b_not_due["color"] == "green")

b_unscheduled = revival_badge(compute_revival_status(None, None, today=_date(2026, 3, 1)))
check("badge: unscheduled -> muted", b_unscheduled["color"] == "muted")

# --- Generate stylized model -------------------------------------------------
r = c.post("/species/MDL-SACCER-001/generate", follow_redirects=True)
check("POST generate (stylized model)", r.status_code == 200)
check("generate flash message ok", b"Generated" in r.data)

# --- Add a new species -------------------------------------------------------
r = c.post("/species/new", data=dict(storage_freezer="Freezer Z", storage_box="Box Z", 
    genus="Zymomonas", species="mobilis", tags="ethanol, spoilage",
    role="spoilage", risk="low", added_by="Test Suite"), follow_redirects=True)
check("POST add species", r.status_code == 200)
check("new species shown", b"Zymomonas mobilis" in r.data)

conn = db()
zymomonas_cid = conn.execute(
    "SELECT collection_id FROM organisms WHERE genus='Zymomonas'").fetchone()["collection_id"]
conn.close()
r = c.post(f"/species/{zymomonas_cid}/generate", follow_redirects=True)
check("POST generate on species with no builtin shape config", r.status_code == 200)
check("fallback shape config still generates a model", b"Generated" in r.data)

# --- Sample source: Source lab vs. Online reference database ---------------
r = c.post("/species/new", data=dict(storage_freezer="Freezer Z", storage_box="Box Z", 
    genus="Lactobacillus", species="labsourced", added_by="Test Suite",
    sample_source_type="lab", sample_source_lab_id=str(test_lab["lab_id"])),
    follow_redirects=True)
check("POST add species with a Source-lab sample source", r.status_code == 200)
conn = db()
lab_sourced = conn.execute(
    "SELECT * FROM organisms WHERE species='labsourced'").fetchone()
conn.close()
check("lab-sourced organism recorded sample_source_type='lab'", lab_sourced["sample_source_type"] == "lab")
check("lab-sourced organism recorded the source-lab id", lab_sourced["sample_source_lab_id"] == test_lab["lab_id"])

r = c.get(f"/species/{lab_sourced['collection_id']}")
check("species page shows the Source-lab source and its name",
     b"Sample source: Source lab" in r.data and b"Central QC Lab" in r.data)

r = c.get(f"/species/{lab_sourced['collection_id']}/edit")
check("edit page pre-selects the Source-lab source", b'value="lab" selected' in r.data)

r = c.post("/species/new", data=dict(storage_freezer="Freezer Z", storage_box="Box Z", 
    genus="Lactobacillus", species="onlinesourced", added_by="Test Suite",
    sample_source_type="online", sample_source_ref="NCBI Reference Sequence Database"),
    follow_redirects=True)
check("POST add species with an Online sample source", r.status_code == 200)
conn = db()
online_sourced = conn.execute(
    "SELECT * FROM organisms WHERE species='onlinesourced'").fetchone()
conn.close()
check("online-sourced organism recorded sample_source_type='online'", online_sourced["sample_source_type"] == "online")
check("online-sourced organism's reference text reused the sample_source column",
     online_sourced["sample_source"] == "NCBI Reference Sequence Database")

r = c.get(f"/species/{online_sourced['collection_id']}")
check("species page shows the Online source and its reference text",
     b"Sample source: Online reference database" in r.data and b"NCBI Reference Sequence Database" in r.data)

# --- Gram stain image at species creation/edit time -------------------------
r = c.get("/species/new")
check("add-species page offers a Gram stain micrograph upload", b'name="gram_image"' in r.data)

r = c.post("/species/new", data=dict(storage_freezer="Freezer Z", storage_box="Box Z", 
    genus="Pediococcus", species="testgramimg", added_by="Test Suite",
    gram_stain="positive", gram_stain_date="2026-02-01",
    gram_image=(io.BytesIO(PNG), "pedio_gram.png")),
    follow_redirects=True, content_type="multipart/form-data")
check("POST add species with a Gram stain image", r.status_code == 200)
conn = db()
pedio = conn.execute("SELECT * FROM organisms WHERE species='testgramimg'").fetchone()
pedio_gram = conn.execute(
    "SELECT * FROM gram_stain_images WHERE organism_id=?", (pedio["organism_id"],)).fetchone()
conn.close()
check("new species got its initial gram_stain", pedio["gram_stain"] == "positive")
check("attaching an image at creation time saved a dated history entry too", pedio_gram is not None)
check("that history entry recorded the image", pedio_gram is not None and pedio_gram["file_path"] is not None)
check("that history entry recorded the right tenant_id", pedio_gram is not None and pedio_gram["tenant_id"] == pedio["tenant_id"])

r = c.get(f"/species/{pedio['collection_id']}/edit")
check("edit-species page offers a Gram stain micrograph upload", b'name="gram_image"' in r.data)
r = c.post(f"/species/{pedio['collection_id']}/edit", data=dict(
    full_name="Pediococcus damnosus", gram_stain="positive",
    gram_image=(io.BytesIO(PNG), "pedio_gram2.png")),
    follow_redirects=True, content_type="multipart/form-data")
check("POST edit species with a new Gram stain image", r.status_code == 200)
conn = db()
pedio_gram_count = conn.execute(
    "SELECT COUNT(*) c FROM gram_stain_images WHERE organism_id=?", (pedio["organism_id"],)).fetchone()["c"]
conn.close()
check("attaching a new image via edit adds another dated history entry (doesn't overwrite)",
     pedio_gram_count == 2)

r = c.post(f"/species/{pedio['collection_id']}/edit", data=dict(
    full_name="Pediococcus damnosus", gram_stain="positive"),
    follow_redirects=True, content_type="multipart/form-data")
conn = db()
pedio_gram_count_after = conn.execute(
    "SELECT COUNT(*) c FROM gram_stain_images WHERE organism_id=?", (pedio["organism_id"],)).fetchone()["c"]
conn.close()
check("saving the edit form again with no new image doesn't spam another history entry",
     pedio_gram_count_after == 2)

# --- Upload plate photos -- multi-plate, per-plate media/conditions, no
# automatic 3D build (see CHANGELOG v2.0) -----------------------------------
from app import _cv_missing_reason

r = c.post("/species/MDL-SACCER-001/upload", data=dict(
    image=[(io.BytesIO(PNG), "plate1.png"), (io.BytesIO(PNG), "plate2.png")],
    media=["YPD agar", "WLN agar"], atmosphere=["aerobic", "anaerobic"],
    protocol="p1", lighting="top", scale_bar="on", uploaded_by="Test Suite",
    captured_date="2026-09-01", captured_time="14:30"), follow_redirects=True,
    content_type="multipart/form-data")
check("POST upload multiple plate images", r.status_code == 200)
check("upload flash reports the right count", b"Uploaded 2 plate image(s)" in r.data)
check("upload flash no longer promises an automatic 3D build",
     b"3D relief built" not in r.data and b"Couldn't build a 3D relief" not in r.data)

conn = db()
img_rows = conn.execute(
    "SELECT * FROM plate_images WHERE organism_id=(SELECT organism_id FROM organisms WHERE collection_id='MDL-SACCER-001') "
    "ORDER BY image_id ASC").fetchall()
conn.close()
check("both uploaded images recorded uploaded_by", all(r["uploaded_by"] == "Test Suite" for r in img_rows))
check("uploaded image recorded upload_timestamp", img_rows[-1]["upload_timestamp"] is not None)
check("each plate kept its own media value", [r["media"] for r in img_rows[-2:]] == ["YPD agar", "WLN agar"])
check("each plate kept its own atmosphere/conditions value",
     [r["atmosphere"] for r in img_rows[-2:]] == ["aerobic", "anaerobic"])
check("shared captured date/time applied to both plates",
     all(r["captured_date"] == "2026-09-01" and r["captured_time"] == "14:30" for r in img_rows[-2:]))
check("uploaded images recorded the right tenant_id", all(r["tenant_id"] == pedio["tenant_id"] for r in img_rows))

r = c.get("/species/MDL-SACCER-001")
check("species page shows per-plate media and conditions", b"YPD agar" in r.data and b"anaerobic" in r.data)
check("species page offers an optional, per-plate 3D build button",
     ('/species/MDL-SACCER-001/rebuild/%d' % img_rows[-1]["image_id"]).encode() in r.data)

last_image_id = img_rows[-1]["image_id"]
check("3D build refuses a plain GET (it writes, so it needs the CSRF token)",
      c.get(f"/species/MDL-SACCER-001/rebuild/{last_image_id}").status_code == 405)
r = c.post(f"/species/MDL-SACCER-001/rebuild/{last_image_id}", follow_redirects=True)
check("POST build_plate_replica", r.status_code == 200)
check("build-3D flash is honest about what happened",
     b"Built the 3D relief" in r.data or b"Couldn't build the 3D model" in r.data)
check("_cv_missing_reason reports None when cv2/scipy/Pillow are all available",
     _cv_missing_reason() is None)

r = c.get("/species/MDL-SACCER-001")
check("species page renders CV morphology fields from the build", b"Coverage" in r.data)

# --- Editing a plate's media/conditions/capture metadata after the fact -----
r = c.post(f"/species/MDL-SACCER-001/image/{last_image_id}/edit", data=dict(
    media="MRS agar", atmosphere="microaerophilic", captured_date="2026-09-02",
    captured_time="09:00"), follow_redirects=True)
check("POST edit_plate_image", r.status_code == 200)
check("plate-edit flash ok", b"Plate metadata updated" in r.data)
conn = db()
edited_img = conn.execute("SELECT * FROM plate_images WHERE image_id=?", (last_image_id,)).fetchone()
conn.close()
check("plate media was changed", edited_img["media"] == "MRS agar")
check("plate conditions were changed", edited_img["atmosphere"] == "microaerophilic")
check("plate captured date/time were changed",
     edited_img["captured_date"] == "2026-09-02" and edited_img["captured_time"] == "09:00")

# --- Regression: handwriting/printed text on a plate must not be counted as
# colonies (see plate_vision.detect_colonies(); not wired into the live
# upload flow, but still tested directly as a standalone CV utility). -------
import os as _os2
_fixture = _os2.path.join(_os2.path.dirname(__file__), "test_fixtures", "handwritten_label_plate.jpg")
if _os2.path.exists(_fixture):
    import plate_vision as _pv
    _rgb = _pv.load_square_rgb(_fixture)
    _circle = _pv.detect_dish_circle(_rgb)
    _result = _pv.detect_colonies(_rgb, diameter_mm=90.0, circle=_circle)
    check("handwriting/printed text plate: dish boundary detected", _circle is not None)
    check("handwriting/printed text plate: colony count stays in a sane range (not exploded by text)",
         _result is not None and _result["count"] <= 10)
    check("handwriting/printed text plate: low-confidence/unreliable flag set (touching colonies present)",
         _result is not None and _result["reliable"] is False)
else:
    print("(skipping handwriting-plate regression check -- fixture not found)")

# --- Gram stain (result + optional micrograph, with history) ---------------
r = c.get("/species/MDL-SACCER-001")
check("species page has a Gram stain section", b"Gram stain" in r.data)
check("seeded yeast strain shows 'not done' (Gram stain isn't routinely used for yeast ID)",
     b">not done<" in r.data)

r = c.post("/species/MDL-SACCER-001/gram-stain", data=dict(
    result="positive", stain_date="2026-01-15", stain_time="13:45", stained_by="Test Suite",
    magnification="1000x oil immersion", notes="Clusters, violet retained",
    image=(io.BytesIO(PNG), "gram1.png")), follow_redirects=True,
    content_type="multipart/form-data")
check("POST gram stain with image", r.status_code == 200)
check("gram stain flash confirms photo saved", b"Photo saved" in r.data)

conn = db()
org_row = conn.execute("SELECT * FROM organisms WHERE collection_id='MDL-SACCER-001'").fetchone()
gram_row = conn.execute(
    "SELECT * FROM gram_stain_images WHERE organism_id=? ORDER BY image_id DESC LIMIT 1",
    (org_row["organism_id"],)).fetchone()
conn.close()
check("organism's current gram_stain updated to latest result", org_row["gram_stain"] == "positive")
check("gram_stain_images row inserted", gram_row is not None)
check("gram_stain_images row records the image path", gram_row is not None and gram_row["file_path"] is not None)
check("gram_stain_images row records stained_by", gram_row is not None and gram_row["stained_by"] == "Test Suite")
check("gram_stain_images row records stain_time", gram_row is not None and gram_row["stain_time"] == "13:45")
check("gram_stain_images row has an automatic 'logged at' timestamp", gram_row is not None and gram_row["created_at"] is not None)

r = c.get("/species/MDL-SACCER-001")
check("species page now shows positive Gram stain badge", b">positive<" in r.data)
check("species page shows gram stain history entry", b"Test Suite" in r.data and b"oil immersion" in r.data)
check("species page shows the stain time", b"13:45" in r.data)
check("species page shows gram stain 'logged at' timestamp", b"Logged " in r.data)

r = c.get("/catalog")
check("catalog shows Gram column value for stained strain", b"badge b-good\">+<" in r.data)

r = c.post("/species/MDL-BACSUB-007/gram-stain", data=dict(
    result="negative", stained_by="Test Suite"), follow_redirects=True)
check("POST gram stain without image still works", r.status_code == 200)
conn = db()
bacsub = conn.execute("SELECT * FROM organisms WHERE collection_id='MDL-BACSUB-007'").fetchone()
conn.close()
check("gram stain without image still updates organism", bacsub["gram_stain"] == "negative")

conn = db()
gid = gram_row["image_id"]
conn.close()
r = c.post(f"/species/MDL-SACCER-001/gram-stain/{gid}/delete", follow_redirects=True)
conn = db()
row = conn.execute("SELECT voided_at FROM gram_stain_images WHERE image_id=?", (gid,)).fetchone()
conn.close()
check("voiding a gram stain needs a reason", row["voided_at"] is None and b"Give a reason" in r.data)
r = c.post(f"/species/MDL-SACCER-001/gram-stain/{gid}/delete", data=dict(reason="wrong strain"),
           follow_redirects=True)
check("POST void gram stain record", r.status_code == 200 and b"voided" in r.data)
conn = db()
row = conn.execute("SELECT * FROM gram_stain_images WHERE image_id=?", (gid,)).fetchone()
aud = conn.execute("SELECT * FROM audit_log WHERE entity='gram_stain' AND entity_id=? AND action='void'",
                   (gid,)).fetchone()
conn.close()
check("voided gram stain is kept, with who/when/why", row is not None and row["voided_at"]
      and row["void_reason"] == "wrong strain" and row["voided_by"] == "adminA")
check("voiding is written to the audit log", aud is not None)
check("species page lists the voided entry", b"Voided entries" in r.data and b"wrong strain" in r.data)

# --- nothing is sent outside the lab ----------------------------------------
# The catalogue is searched here, on the lab's own installation. Nothing in the
# app sends a strain, a photograph or a question anywhere else.
r = c.get("/catalog")
check("the catalogue has a plain search and filter box", b'name="q"' in r.data)
_outward = [str(rule) for rule in app.url_map.iter_rules()
            if any(w in str(rule) for w in ("/ai", "/chat", "assistant"))]
check("no route offers to send anything to an outside service", _outward == [])
check("no page offers to send anything to an outside service",
      b"outside" not in c.get("/").data and b"provider" not in c.get("/catalog").data)

# --- Revival tracking --------------------------------------------------------
r = c.post("/species/MDL-BACSUB-007/log-revival", data=dict(
    performed_by="Test Suite", growth_success="pass", contamination_notes="clean",
    colony_count="47", revival_time="11:15"), follow_redirects=True)
check("POST log revival check", r.status_code == 200)
check("revival log flash ok", b"logged" in r.data)

conn = db()
org = conn.execute("SELECT * FROM organisms WHERE collection_id='MDL-BACSUB-007'").fetchone()
log_count = conn.execute("SELECT COUNT(*) c FROM revival_log WHERE organism_id=?", (org["organism_id"],)).fetchone()["c"]
latest_log = conn.execute(
    "SELECT * FROM revival_log WHERE organism_id=? ORDER BY log_id DESC LIMIT 1",
    (org["organism_id"],)).fetchone()
conn.close()
check("revival_log row inserted", log_count >= 1)
check("organism last_revival_date updated", org["last_revival_date"] is not None)
check("manually entered colony count stored on the revival_log row", latest_log["colony_count"] == 47)
check("organism's current colony_count mirrors the latest manual entry", org["colony_count"] == 47)
check("revival time stored on the revival_log row", latest_log["revival_time"] == "11:15")
check("organism's current last_revival_time mirrors the latest entry", org["last_revival_time"] == "11:15")
check("revival_log row has an automatic 'logged at' timestamp", latest_log["created_at"] is not None)
check("revival_log row recorded the right tenant_id", latest_log["tenant_id"] == org["tenant_id"])

r = c.get("/species/MDL-BACSUB-007")
check("species page shows the manual colony count on the viability card", b"47" in r.data)
check("species page shows the revival time", b"11:15" in r.data)
check("revival history table shows a 'Logged' (system timestamp) column", b"Logged" in r.data)

r = c.get("/upload")
check("upload page no longer claims automatic colony counting", b"count individual colonies" not in r.data)
check("upload page no longer promises an automatic 3D build",
     b"isn't built automatically" in r.data)
check("upload page offers per-plate media field", b'name="media"' in r.data)
check("upload page offers per-plate conditions field", b'name="atmosphere"' in r.data)
check("upload page offers an 'add another plate' control", b"add-plate-row" in r.data)

r = c.get("/")
check("dashboard reflects fresh revival (no longer flagged overdue)",
     b'badge b-bad">overdue by' not in r.data)
status = compute_revival_status(org["last_revival_date"], org["revival_interval_days"])
check("compute_revival_status shows notDue right after logging", status["status"] == "notDue")

# --- Deterministic revival-status arithmetic (hand-checked cases) -----------
s1 = compute_revival_status("2026-01-01", 90, today=__import__("datetime").date(2026, 4, 1))
check("revival status: exactly on due date -> due", s1["status"] == "due")
s2 = compute_revival_status("2026-01-01", 90, today=__import__("datetime").date(2026, 4, 10))
check("revival status: past due date -> overdue with correct day count", s2["status"] == "overdue" and s2["days_overdue"] == 9)
s3 = compute_revival_status("2026-01-01", 90, today=__import__("datetime").date(2026, 3, 1))
check("revival status: well before due date -> notDue", s3["status"] == "notDue")
s4 = compute_revival_status(None, None, today=__import__("datetime").date(2026, 3, 1))
check("revival status: no interval set -> unscheduled", s4["status"] == "unscheduled")

# --- Export (scoped to the caller's own lab -- see the isolation section
# further down for the cross-tenant check) -----------------------------------
r = c.get("/export")
check("GET /export", r.status_code == 200)
check("export is a zip", r.data[:2] == b"PK")

# --- Connect / QR page --------------------------------------------------------
r = c.get("/connect")
check("GET /connect", r.status_code == 200)
r = c.get("/qr.png")
check_qr("GET /qr.png", r.status_code == 200)

# --- PWA assets ----------------------------------------------------------------
r = c.get("/manifest.json")
check("GET /manifest.json", r.status_code == 200)
r = c.get("/service-worker.js")
check("GET /service-worker.js", r.status_code == 200)

# --- Feedback questionnaire -----------------------------------------------------
r = c.get("/feedback")
check("GET /feedback", r.status_code == 200)
check("feedback form has rating field", b'name="rating"' in r.data)
check("feedback form has ease field", b'name="ease"' in r.data)
check("feedback form has optional improvement field", b'name="improvement"' in r.data)

r = c.post("/feedback", data={}, follow_redirects=True)
check("submitting feedback with no rating/ease is rejected", b"Please pick an overall rating" in r.data)
conn = db()
check("rejected submission wasn't stored", conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 0)
conn.close()

r = c.post("/feedback", data={
    "rating": "5", "ease": "easy", "most_used": "catalog",
    "improvement": "Would love a dark-mode toggle.", "name": "Jordan",
}, follow_redirects=True)
check("valid feedback submission accepted", r.status_code == 200)
check("valid feedback submission flashes thanks", b"Thanks" in r.data)

conn = db()
feedback_row = conn.execute("SELECT * FROM feedback WHERE name='Jordan'").fetchone()
conn.close()
check("feedback row was stored", feedback_row is not None)
check("feedback row has the right rating", feedback_row["rating"] == 5)
check("feedback row has the right ease value", feedback_row["ease"] == "easy")
check("feedback row recorded the right tenant_id", feedback_row is not None and feedback_row["tenant_id"] == tenant_a["tenant_id"])

r = c.post("/feedback", data={"rating": "3", "ease": "ok"}, follow_redirects=True)
check("feedback with only the two required fields is accepted", b"Thanks" in r.data)

r = c.get("/feedback/summary")
check("GET /feedback/summary", r.status_code == 200)
check("summary shows the written comment", b"Would love a dark-mode toggle" in r.data)
check("summary shows an average rating", b"average rating" in r.data)

r = c.get("/export")
import zipfile as _zipfile
zf = _zipfile.ZipFile(io.BytesIO(r.data))
check("export zip contains feedback.csv", "feedback.csv" in zf.namelist())


# =============================================================================
# Lab B: a second lab, added straight into the database. An installation holds
# one lab, but every query still filters by lab; these checks keep that honest.
# =============================================================================
c.get("/logout")
conn = db()
create_lab(conn, "Downtown Fermentation Lab", "adminB", "adminB-pass-123", "Admin B")
conn.close()
r = c.post("/login", data=dict(username="adminB", password="adminB-pass-123"), follow_redirects=True)
check("Lab B's admin can sign in", r.status_code == 200)

conn = db()
tenant_b = conn.execute("SELECT * FROM tenants WHERE name='Downtown Fermentation Lab'").fetchone()
conn.close()
check("Lab B's tenant row exists and differs from Lab A's", tenant_b is not None and tenant_b["tenant_id"] != tenant_a["tenant_id"])

r = c.get("/catalog")
check("Lab B's catalog starts empty (no shared seed data)", b"MDL-SACCER-001" not in r.data)

# Same external accession number as an existing Lab A record (Lab A's seeded
# Saccharomyces cerevisiae holds NCYC 505) -- this used to be blocked by
# accessions' bare UNIQUE(culture_collection, accession_number) before it was
# rebuilt as UNIQUE(tenant_id, culture_collection, accession_number) for
# multi-tenancy (see _migrate_accessions_table).
r = c.post("/species/new", data=dict(storage_freezer="Freezer Z", storage_box="Box Z", 
    genus="Saccharomyces", species="cerevisiae", collection="NCYC", accession="505",
    added_by="Test Suite"), follow_redirects=True)
check("Lab B can record the same external accession (NCYC 505) Lab A already has",
     r.status_code == 200 and b"Added" in r.data)

conn = db()
dup_accession = conn.execute(
    "SELECT COUNT(*) c FROM accessions WHERE culture_collection='NCYC' AND accession_number='505'"
).fetchone()["c"]
conn.close()
check("both labs' NCYC 505 accession rows coexist (composite UNIQUE, not a bare one)", dup_accession == 2)

r = c.post(f"/species/MDL-SACCER-001/edit", data=dict(full_name="Saccharomyces cerevisiae"),
          follow_redirects=True)
check("(sanity) Lab B's own MDL-SACCER-001 edit succeeds without touching Lab A's", r.status_code == 200)

r = c.post("/species/new", data=dict(storage_freezer="Freezer Z", storage_box="Box Z", 
    genus="Testus", species="betaspecies", collection="NCYC", accession="9999",
    added_by="Test Suite"), follow_redirects=True)
check("Lab B strain add with a fresh accession succeeds", r.status_code == 200)

conn = db()
org_b = conn.execute("SELECT * FROM organisms WHERE genus='Testus'").fetchone()
conn.close()
check("Lab B's strain recorded Lab B's tenant_id", org_b is not None and org_b["tenant_id"] == tenant_b["tenant_id"])

# --- Capstone: cross-tenant isolation ----------------------------------------
conn = db()
org_a_saccer = conn.execute("SELECT * FROM organisms WHERE collection_id='MDL-SACCER-001' AND tenant_id=?",
                           (tenant_a["tenant_id"],)).fetchone()
conn.close()
check("Lab A's MDL-SACCER-001 still exists under Lab A's tenant_id", org_a_saccer is not None)

r = c.get("/catalog")
check("Lab B catalog never shows Lab A's Zymomonas strain", b"Zymomonas mobilis" not in r.data)

r = c.get(f"/species/MDL-SACCER-001")
check("Lab B's own MDL-SACCER-001 page shows its own data, not Lab A's", b"Saccharomyces cerevisiae" in r.data)

r = c.get(f"/species/{zymomonas_cid}")
check("Lab B is blocked (404) from a cid that only exists in Lab A", r.status_code == 404)

r = c.post(f"/species/{zymomonas_cid}/delete")
check("Lab B cannot delete a strain by a cid belonging only to Lab A (404)", r.status_code == 404)

r = c.get(f"/species/{zymomonas_cid}/edit")
check("Lab B cannot open the edit page for a cid belonging only to Lab A (404)", r.status_code == 404)

r = c.get("/export")
exported = r.data
check("Lab B's export never contains Lab A's strain data", b"Zymomonas" not in exported and b"Riverside" not in exported)

r = c.get("/export/xlsx")
check("Lab B's xlsx export succeeds and is scoped (no crash from the shared accessions/tags tables)",
     r.status_code == 200)

# Admin boundary: Lab B's admin can't see Lab A's users or source labs
r = c.get("/admin")
check("Lab B's admin page shows only Lab B's own (empty) users/labs",
     b"adminA" not in r.data and b"labuser" not in r.data and b"Central QC Lab" not in r.data)

c.get("/logout")

# --- CSRF protection -------------------------------------------------------------
# Every other request in this suite ran with app.config["TESTING"] = True, which
# deliberately bypasses the check (see the note where it's set, near the top of
# this file) so the suite can post plain data dicts. This section turns that
# bypass off just long enough to confirm the protection is actually live: a POST
# with no token, and one with a wrong token, must both be rejected; a POST that
# echoes back the token a real page would have rendered must still succeed.
r = c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)
check("(setup) logged back in as Lab A's admin for the CSRF check", r.status_code == 200)

app.config["TESTING"] = False
try:
    r = c.post(f"/species/MDL-SACCER-001/generate", data={})
    check("POST with no CSRF token is rejected (400)", r.status_code == 400)

    r = c.post(f"/species/MDL-SACCER-001/generate", data=dict(csrf_token="not-the-right-token"))
    check("POST with a wrong CSRF token is rejected (400)", r.status_code == 400)

    page = c.get(f"/species/MDL-SACCER-001")
    token = page.data.split(b'name="csrf_token" value="')[1].split(b'"')[0].decode()
    r = c.post(f"/species/MDL-SACCER-001/generate", data=dict(csrf_token=token), follow_redirects=True)
    check("POST with the page's own CSRF token succeeds", r.status_code == 200)
finally:
    app.config["TESTING"] = True
c.get("/logout")

# --- Bulk import ------------------------------------------------------------------
import io as _io
import openpyxl as _openpyxl
import importer as _importer

IMPORT_CSV = (
    "Organism,Culture Collection,Tags,Gram,Last Subcultured,Interval,Supplier,Storage\n"
    "Lactobacillus brevis BR-1,DSM 99001; NCYC 9001,\"sour, spoilage\",pos,03/04/2025,90,Import Test Supplier,Freezer 1 Box 2\n"
    "Pediococcus damnosus,ATCC 29358,spoilage,negative,2025-13-45,,Import Test Supplier,\n"
    "Saccharomyces diastaticus,,,,,,,\n"
    "Brettanomyces,,,,,,,\n"
    "Zymomonas mobilis,NCYC 9001,,,,,,\n"
    ",,,,,,,\n"
).encode("cp1252")

# pure parsing / validation
hdrs, rows_ = _importer.parse_file("x.csv", IMPORT_CSV)
imap = _importer.auto_map(hdrs)
check("importer: blank lines dropped (5 data rows)", len(rows_) == 5)
check("importer: headers auto-mapped", imap == ["full_name", "accessions", "tags", "gram_stain",
      "last_revival_date", "revival_interval_days", "source_lab", "storage_location"])
recs_ = _importer.build_records(rows_, imap)
check("importer: full name split into genus/species/strain",
      recs_[0]["values"]["species"] == "brevis" and recs_[0]["values"]["strain"] == "BR-1")
check("importer: day-first date parsed", recs_[0]["values"]["last_revival_date"] == "2025-04-03")
check("importer: interval makes strain periodic", recs_[0]["values"]["viability_category"] == "periodic")
check("importer: bad date warned, not fatal",
      recs_[1]["values"]["last_revival_date"] is None and any("date" in w for w in recs_[1]["warnings"]))
check("importer: genus-only row is an error", bool(recs_[3]["errors"]))
try:
    _importer.parse_file("old.xls", b"x")
    old_xls_rejected = False
except _importer.ImportFileError:
    old_xls_rejected = True
check("importer: legacy .xls gets a helpful rejection", old_xls_rejected)

wb_ = _openpyxl.Workbook()
ws_ = wb_.active
ws_.append(["Genus", "Species", "Accession", "Last revival"])
ws_.append(["Acetobacter", "pasteurianus", "NCYC 1234", __import__("datetime").date(2025, 5, 6)])
xbuf = _io.BytesIO(); wb_.save(xbuf)
xh, xr = _importer.parse_file("s.xlsx", xbuf.getvalue())
check("importer: .xlsx read, dates become ISO text", xr[0][3] == "2025-05-06" and xr[0][0] == "Acetobacter")

# routes: admin only
r = c.post("/login", data=dict(username="labuser", password="labuser-pass-123"), follow_redirects=True)
check("(setup) regular user logged in", r.status_code == 200)
check("regular user can't open the import page", c.get("/admin/import").status_code == 403)
c.get("/logout")

r = c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)
check("(setup) Lab A admin logged in for import", r.status_code == 200)
check("admin page links to import", b"/admin/import" in c.get("/admin").data)
check("import page loads", c.get("/admin/import").status_code == 200)
r = c.get("/admin/import/template.csv")
check("template CSV downloads", r.status_code == 200 and b"Genus,Species" in r.data)

# Give Lab A an existing ATCC 29358 so row 3 must be flagged as already present
conn = db()
tid_a = conn.execute("SELECT tenant_id FROM users WHERE username='adminA'").fetchone()[0]
oid_a = conn.execute("SELECT organism_id FROM organisms WHERE tenant_id=? LIMIT 1", (tid_a,)).fetchone()[0]
conn.execute("INSERT OR IGNORE INTO accessions (organism_id, tenant_id, culture_collection, accession_number) "
             "VALUES (?,?,?,?)", (oid_a, tid_a, "ATCC", "29358"))
conn.commit()
before = conn.execute("SELECT COUNT(*) FROM organisms WHERE tenant_id=?", (tid_a,)).fetchone()[0]
lac_max = max((int(x[0].rsplit("-", 1)[1]) for x in conn.execute(
    "SELECT collection_id FROM organisms WHERE tenant_id=? AND collection_id LIKE 'MDL-LACBRE-%'", (tid_a,))),
    default=0)
conn.close()

r = c.post("/admin/import", data=dict(file=(_io.BytesIO(b"x"), "notes.pdf")),
           content_type="multipart/form-data", follow_redirects=True)
check("wrong file type rejected with a message", b"Upload a .csv or .xlsx" in r.data)

r = c.post("/admin/import", data=dict(file=(_io.BytesIO(IMPORT_CSV), "strains.csv")),
           content_type="multipart/form-data")
check("upload redirects to the preview", r.status_code == 302 and "/admin/import/" in r.headers["Location"])
preview_url = r.headers["Location"]
r = c.get(preview_url)
check("preview shows parsed rows", r.status_code == 200 and b"Lactobacillus brevis" in r.data)
check("preview flags an accession already in the catalog", b"ATCC 29358 is already in your catalog" in r.data)
check("preview flags an accession repeated within the file", b"NCYC 9001 already appears on row 2" in r.data)
check("preview flags a genus-only row", b"Species is missing" in r.data)

# another lab's admin can't see or commit this staged import
c.get("/logout")
c.post("/login", data=dict(username="adminB", password="adminB-pass-123"), follow_redirects=True)
check("another lab's admin gets 404 on the preview", c.get(preview_url).status_code == 404)
check("another lab's admin can't commit it",
      c.post(preview_url, data=dict(action="commit", row=["2"])).status_code == 404)
check("path-traversal token is a 404", c.get("/admin/import/..%2F..%2Fetc%2Fpasswd").status_code == 404)
c.get("/logout")
c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)

form_map = {f"map_{i}": k for i, k in enumerate(imap) if k}
r = c.post(preview_url, data=dict(action="commit", **form_map), follow_redirects=True)
check("commit with nothing ticked adds nothing", b"No rows were selected" in r.data)

# tick every row; only the two valid, non-duplicate ones (2 and 4) may be imported
r = c.post(preview_url, data={"action": "commit", "row": ["2", "3", "4", "5", "6"], **form_map},
           follow_redirects=True)
check("commit reports 2 strains imported", b"Imported 2 strains" in r.data)
check("commit reports the new source lab", b"Added 1 new source lab" in r.data)
conn = db()
after = conn.execute("SELECT COUNT(*) FROM organisms WHERE tenant_id=?", (tid_a,)).fetchone()[0]
check("only valid rows were inserted", after == before + 2)
o = conn.execute("SELECT * FROM organisms WHERE tenant_id=? AND strain='BR-1'", (tid_a,)).fetchone()
check("collection ID continues after the lab\'s existing numbering", o is not None and o["collection_id"] == f"MDL-LACBRE-{lac_max + 1:03d}")
check("imported strain fields stored",
      o["strain"] == "BR-1" and o["gram_stain"] == "positive" and o["revival_interval_days"] == 90
      and o["last_revival_date"] == "2025-04-03" and o["storage_location"] == "Freezer 1 Box 2"
      and o["viability_category"] == "periodic")
accs = {(a["culture_collection"], a["accession_number"]) for a in conn.execute(
    "SELECT * FROM accessions WHERE organism_id=?", (o["organism_id"],))}
check("imported accessions stored", accs == {("DSM", "99001"), ("NCYC", "9001")})
tags_ = {t["tag"] for t in conn.execute("SELECT tag FROM tags WHERE organism_id=?", (o["organism_id"],))}
check("imported tags stored", tags_ == {"sour", "spoilage"})
lab_row = conn.execute("SELECT * FROM labs WHERE tenant_id=? AND name='Import Test Supplier'", (tid_a,)).fetchone()
check("source lab created and linked", lab_row is not None and o["sample_source_lab_id"] == lab_row["lab_id"]
      and o["sample_source_type"] == "lab")
check("brewing_relevance row created", conn.execute(
    "SELECT COUNT(*) FROM brewing_relevance WHERE organism_id=?", (o["organism_id"],)).fetchone()[0] == 1)
check("imported strain page renders", c.get("/species/" + o["collection_id"]).status_code == 200)
conn.close()

# re-uploading the same file: everything valid is now a known duplicate
r = c.post("/admin/import", data=dict(file=(_io.BytesIO(IMPORT_CSV), "strains.csv")),
           content_type="multipart/form-data")
r = c.get(r.headers["Location"])
check("re-importing the same file flags the strains as already present",
      r.data.count(b"already in your catalog") >= 3 and b"0 ready" in r.data)

# a spreadsheet upload works end to end
r = c.post("/admin/import", data=dict(file=(_io.BytesIO(xbuf.getvalue()), "s.xlsx")),
           content_type="multipart/form-data")
xurl = r.headers["Location"]
xm = {f"map_{i}": k for i, k in enumerate(_importer.auto_map(xh)) if k}
r = c.post(xurl, data={"action": "commit", "row": ["2"], **xm}, follow_redirects=True)
check("xlsx import commits", b"Imported 1 strain." in r.data)
check("staged file removed after import", c.get(xurl).status_code == 404)
c.get("/logout")

# Lab B importing the same accession isn't blocked by Lab A's copy
c.post("/login", data=dict(username="adminB", password="adminB-pass-123"), follow_redirects=True)
r = c.post("/admin/import", data=dict(file=(_io.BytesIO(IMPORT_CSV), "strains.csv")),
           content_type="multipart/form-data")
r = c.get(r.headers["Location"])
check("Lab B's preview doesn't see Lab A's existing accession", b"is already in your catalog" not in r.data)
c.get("/logout")

# --- Custom fields, structured storage, labels -------------------------------------
r = c.post("/login", data=dict(username="labuser", password="labuser-pass-123"), follow_redirects=True)
check("regular user can't add a custom field",
      c.post("/admin/fields", data=dict(name="Nope", field_type="text")).status_code == 403)
c.get("/logout")

c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)
r = c.post("/admin/fields", data=dict(name="Biosafety level", field_type="choice", choices="BSL-1, BSL-2"),
           follow_redirects=True)
check("admin adds a choice custom field", b"Added the field" in r.data and b"Biosafety level" in r.data)
r = c.post("/admin/fields", data=dict(name="Batch no", field_type="number"), follow_redirects=True)
r = c.post("/admin/fields", data=dict(name="Biosafety level", field_type="text"), follow_redirects=True)
check("duplicate field name is refused", b"already have a field" in r.data)
r = c.post("/admin/fields", data=dict(name="Pick", field_type="choice", choices="only-one"), follow_redirects=True)
check("choice field with <2 options is refused", b"at least two options" in r.data)

conn = db()
fid_bsl = conn.execute("SELECT field_id FROM custom_fields WHERE name='Biosafety level' AND tenant_id=?", (tid_a,)).fetchone()[0]
fid_batch = conn.execute("SELECT field_id FROM custom_fields WHERE name='Batch no' AND tenant_id=?", (tid_a,)).fetchone()[0]
conn.close()

check("add-strain form shows custom fields", f'name="cf_{fid_bsl}"'.encode() in c.get("/species/new").data)
r = c.get("/species/MDL-SACCER-001/edit")
check("edit form shows custom fields and structured storage",
      f'name="cf_{fid_batch}"'.encode() in r.data and b'name="storage_freezer"' in r.data)

r = c.post("/species/MDL-SACCER-001/edit", data=dict(
    full_name="Saccharomyces cerevisiae", viability_category="stable", collection_type="library",
    storage_freezer="Freezer 9", storage_rack="Rack 1", storage_box="Box 3", storage_position="A2",
    **{f"cf_{fid_bsl}": "bsl-2", f"cf_{fid_batch}": "not a number"}), follow_redirects=True)
check("a value that doesn't fit its field type is reported, not saved", b"Not saved (wrong format): Batch no" in r.data)
check("choice value matched case-insensitively and shown on the strain page",
      b"Biosafety level" in r.data and b"BSL-2" in r.data)
check("structured storage shown on the strain page", b"Freezer 9 / Rack 1 / Box 3 / A2" in r.data)

r = c.post("/species/MDL-SACCER-001/edit", data=dict(
    full_name="Saccharomyces cerevisiae", viability_category="stable", collection_type="library",
    storage_freezer="Freezer 9", storage_rack="Rack 1", storage_box="Box 3", storage_position="A2",
    **{f"cf_{fid_batch}": "1,200"}), follow_redirects=True)
conn = db()
bv = conn.execute("SELECT value FROM custom_field_values WHERE field_id=? AND organism_id=(SELECT organism_id FROM organisms WHERE collection_id='MDL-SACCER-001' AND tenant_id=?)", (fid_batch, tid_a)).fetchone()
conn.close()
check("number field normalized (1,200 -> 1200)", bv is not None and bv["value"] == "1200")

# a second strain placed in the same box position -> storage map flags a clash
c.post("/species/MDL-LEVBRE-004/edit", data=dict(
    full_name="Levilactobacillus brevis", viability_category="stable", collection_type="library",
    storage_freezer="Freezer 9", storage_rack="Rack 1", storage_box="Box 3", storage_position="a2"))
r = c.get("/storage")
check("storage map lists the box and its strains", b"Freezer 9 / Rack 1 / Box 3" in r.data and b"MDL-SACCER-001" in r.data)
check("storage map flags two strains in one position", b"shared" in r.data and b"more than one strain" in r.data)
check("storage map filters by box", b"MDL-LACLAB-001" not in c.get("/storage?freezer=Freezer 9&box=Box 3").data)

# labels
r = c.get("/labels")
check("label picker lists strains", r.status_code == 200 and b"MDL-SACCER-001" in r.data)
# labels carry the lab's public address (localhost deliberately prints no QR)
os.environ["PUBLIC_BASE_URL"] = "http://192.168.1.20:5000"
r = c.get("/labels/print?cid=MDL-SACCER-001&size=small")
os.environ.pop("PUBLIC_BASE_URL")
check_qr("label sheet renders with a QR code", r.status_code == 200 and b"<svg" in r.data and b"MDL-SACCER-001" in r.data
      and b"Freezer 9 / Rack 1 / Box 3 / A2" in r.data)
from app import _qr_svg
check_qr("label QR encodes this strain's page URL",
      str(_qr_svg(f"http://192.168.1.20:5000/s/{tid_a}/MDL-SACCER-001")).encode() in r.data)
check("label sheet with no strain chosen redirects", c.get("/labels/print").status_code == 302)
check("label for an unknown strain is a 404", c.get("/labels/print?cid=NOPE-999").status_code == 404)
check("species page offers a label button", b"/labels/print?cid=MDL-SACCER-001" in c.get("/species/MDL-SACCER-001").data)

# scanning a printed label: the code points at /s/<lab>/<strain>, which opens that
# strain's page. The whole record is then on one screen, with no searching.
scanned = c.get(f"/s/{tid_a}/MDL-SACCER-001")
check("scanning a label goes straight to that strain",
      scanned.status_code == 302 and scanned.headers["Location"].endswith("/species/MDL-SACCER-001"))
page = c.get(f"/s/{tid_a}/MDL-SACCER-001", follow_redirects=True)
check("...and the strain's page opens with its details on it",
      page.status_code == 200 and b"Saccharomyces" in page.data and b"Freezer 9" in page.data
      and b"Box 3" in page.data and b"A2" in page.data)
# what the person at the freezer sees without searching any further
_scanned = page.data
check("...identity, accession and Gram result are on that page",
      b"NCYC 505" in _scanned and b"Gram" in _scanned)
_monitored = c.get(f"/s/{tid_a}/MDL-BACSUB-007", follow_redirects=True).data
check("...and for a strain under periodic monitoring, its revival status and last passing check",
      b"Viability monitoring" in _monitored and b"Last passing revival" in _monitored)
check("...and the plate photographs", b"Photo" in _scanned or b"photo" in _scanned)
elsewhere = app.test_client()
elsewhere.post("/login", data=dict(username="adminB", password="adminB-pass-123"))
check("a label printed by another lab shows that lab nothing",
      elsewhere.get(f"/s/{tid_a}/MDL-SACCER-001").status_code == 404)
signed_out = app.test_client().get(f"/s/{tid_a}/MDL-SACCER-001")
check("whoever picks up a vial must sign in before the label tells them anything",
      signed_out.status_code == 302 and "/login" in signed_out.headers["Location"])
check("a label for a strain that no longer exists does not error",
      c.get(f"/s/{tid_a}/MDL-NOPE-999").status_code in (302, 404))

# import: custom column + structured storage columns
CSV2 = ("Genus,Species,Biosafety level,Freezer,Box,Position\n"
        "Gluconobacter,oxydans,BSL-1,Freezer 4,Box 7,B2\n"
        "Komagataeibacter,xylinus,BSL-9,Freezer 4,Box 7,B3\n").encode()
r = c.post("/admin/import", data=dict(file=(_io.BytesIO(CSV2), "c2.csv")), content_type="multipart/form-data")
r = c.get(r.headers["Location"])
check("import preview offers the custom field and auto-maps it", b"Custom: Biosafety level" in r.data
      and b'selected>Custom: Biosafety level' in r.data.replace(b"\n", b" ").replace(b"  ", b" "))
check("import warns about an invalid custom value", b"Biosafety level:" in r.data and b"BSL-9" in r.data)
url2 = c.post("/admin/import", data=dict(file=(_io.BytesIO(CSV2), "c2.csv")), content_type="multipart/form-data").headers["Location"]
cm = {f"cf_{fid_bsl}": None}
m2 = _importer.auto_map(["Genus", "Species", "Biosafety level", "Freezer", "Box", "Position"], {f"cf_{fid_bsl}": "Biosafety level"})
r = c.post(url2, data={"action": "commit", "row": ["2", "3"], **{f"map_{i}": k for i, k in enumerate(m2) if k}},
           follow_redirects=True)
conn = db()
g = conn.execute("SELECT * FROM organisms WHERE tenant_id=? AND genus='Gluconobacter'", (tid_a,)).fetchone()
check("import stored structured storage", g is not None and (g["storage_freezer"], g["storage_box"], g["storage_position"]) == ("Freezer 4", "Box 7", "B2"))
gv = conn.execute("SELECT value FROM custom_field_values WHERE organism_id=? AND field_id=?", (g["organism_id"], fid_bsl)).fetchone()
check("import stored the custom field value", gv is not None and gv["value"] == "BSL-1")
k = conn.execute("SELECT organism_id FROM organisms WHERE tenant_id=? AND genus='Komagataeibacter'", (tid_a,)).fetchone()
check("invalid custom value dropped but the strain still imported",
      k is not None and conn.execute("SELECT COUNT(*) FROM custom_field_values WHERE organism_id=?", (k["organism_id"],)).fetchone()[0] == 0)
conn.close()
check("custom fields appear in the data export", b"custom_fields.csv" in c.get("/export").data)
c.get("/logout")

# Lab B can't see, use, or delete Lab A's custom fields
c.post("/login", data=dict(username="adminB", password="adminB-pass-123"), follow_redirects=True)
check("Lab B's admin page doesn't list Lab A's custom fields", b"Batch no" not in c.get("/admin").data)
check("Lab B's add-strain form has no Lab A custom fields", b"Biosafety level" not in c.get("/species/new").data)
c.post(f"/admin/fields/{fid_bsl}/delete")
conn = db()
check("Lab B can't delete Lab A's custom field",
      conn.execute("SELECT COUNT(*) FROM custom_fields WHERE field_id=?", (fid_bsl,)).fetchone()[0] == 1)
conn.close()
check("Lab B's storage map doesn't show Lab A's freezer", b"Freezer 9" not in c.get("/storage").data)
check("Lab B can't print Lab A's labels", c.get("/labels/print?cid=MDL-LEVBRE-004").status_code in (404,)
      or b"Freezer 9" not in c.get("/labels/print?cid=MDL-LEVBRE-004").data)
c.get("/logout")

c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)
c.post(f"/admin/fields/{fid_batch}/delete")
conn = db()
check("deleting a field removes its values",
      conn.execute("SELECT COUNT(*) FROM custom_field_values WHERE field_id=?", (fid_batch,)).fetchone()[0] == 0)
conn.close()
c.get("/logout")

# --- Files are private to the lab that owns them ----------------------------------
from pathlib import Path as _Path
conn = db()
tid_b = conn.execute("SELECT tenant_id FROM users WHERE username='adminB'").fetchone()[0]
up_row = conn.execute("SELECT file_path FROM plate_images WHERE tenant_id=? LIMIT 1", (tid_a,)).fetchone()
mdl_rows = conn.execute("SELECT file_path FROM models WHERE tenant_id=? AND file_path LIKE '%.stl'", (tid_a,)).fetchall()
conn.close()
assert up_row and mdl_rows, "seeded test data should include a plate photo and models for Lab A"
up_name = up_row["file_path"].split("/")[-1]
glb_name = mdl_rows[0]["file_path"].split("/")[-1].replace(".stl", ".glb")
stl_name = mdl_rows[0]["file_path"].split("/")[-1]

c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)
check("owner can fetch their own plate photo", c.get(f"/uploads/{up_name}").status_code == 200)
check("owner can fetch their own GLB model", c.get(f"/models/{glb_name}").status_code == 200)
check("owner can fetch their own STL model", c.get(f"/models/{stl_name}").status_code == 200)
check("path traversal out of uploads is refused", c.get("/uploads/..%2Fapp.py").status_code == 404)
check("unknown filename is a 404", c.get("/models/nothing.glb").status_code == 404)
check("a file type that isn't a model is refused", c.get("/models/../microbe_catalog.db").status_code == 404)
c.get("/logout")

check("anonymous visitor can't fetch a plate photo", c.get(f"/uploads/{up_name}").status_code == 302)
c.post("/login", data=dict(username="adminB", password="adminB-pass-123"), follow_redirects=True)
check("another lab can't fetch Lab A's plate photo", c.get(f"/uploads/{up_name}").status_code == 404)
check("another lab can't fetch Lab A's GLB model", c.get(f"/models/{glb_name}").status_code == 404)
check("another lab can't fetch Lab A's STL model", c.get(f"/models/{stl_name}").status_code == 404)

# Lab B generates models for its own strain with the same collection ID as Lab A's:
# the files must not collide with (or overwrite) Lab A's.
conn = db()
before_b = {r[0] for r in conn.execute("SELECT file_path FROM models WHERE tenant_id=?", (tid_b,))}
conn.close()
c.post("/species/MDL-SACCER-001/generate", follow_redirects=True)
conn = db()
b_models = {r[0] for r in conn.execute("SELECT file_path FROM models WHERE tenant_id=? AND file_path LIKE '%.stl'", (tid_b,))}
a_models = {r[0] for r in conn.execute("SELECT file_path FROM models WHERE tenant_id=? AND file_path LIKE '%.stl'", (tid_a,))}
conn.close()
check("Lab B's generated model files are separate from Lab A's", bool(b_models) and not (a_models & b_models))
check("Lab B's model filenames carry its lab id", all(x.split("/")[-1].startswith(f"t{tid_b}-") for x in b_models))
b_glb = sorted(b_models)[0].split("/")[-1].replace(".stl", ".glb")
check("Lab B can fetch its own model", c.get(f"/models/{b_glb}").status_code == 200)
c.get("/logout")
c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)
check("Lab A can't fetch Lab B's model", c.get(f"/models/{b_glb}").status_code == 404)

# Deleting a plate photo / a strain removes its files from disk
conn = db()
img = conn.execute("""SELECT i.image_id, i.file_path FROM plate_images i JOIN organisms o ON o.organism_id=i.organism_id
                      WHERE i.tenant_id=? AND o.collection_id='MDL-SACCER-001'""", (tid_a,)).fetchall()
conn.close()
from app import DATA_DIR as _DD
one = img[0]
check("(setup) the plate photo exists on disk before deletion", (_DD / one["file_path"]).exists())
c.post(f"/species/MDL-SACCER-001/image/{one['image_id']}/delete", data=dict(reason="wrong plate"),
       follow_redirects=True)
conn = db()
vrow = conn.execute("SELECT voided_at, void_reason FROM plate_images WHERE image_id=?", (one["image_id"],)).fetchone()
conn.close()
check("voiding a plate photo keeps the photo (it is history)", (_DD / one["file_path"]).exists())
check("voided plate photo is marked, with its reason", vrow["voided_at"] and vrow["void_reason"] == "wrong plate")
check("voided plate photo no longer shows on the strain page",
      one["file_path"].split("/")[-1].encode() not in c.get("/species/MDL-SACCER-001").data
      or b"Voided entries" in c.get("/species/MDL-SACCER-001").data)

conn = db()
rest = [r["file_path"] for r in conn.execute(
    "SELECT file_path FROM plate_images WHERE organism_id=(SELECT organism_id FROM organisms WHERE collection_id='MDL-SACCER-001' AND tenant_id=?)", (tid_a,))]
mods = [r["file_path"] for r in conn.execute(
    "SELECT file_path FROM models WHERE organism_id=(SELECT organism_id FROM organisms WHERE collection_id='MDL-SACCER-001' AND tenant_id=?)", (tid_a,))]
conn.close()
check("(setup) strain still has model files on disk", any((_DD / m).exists() for m in mods))
r = c.post("/species/MDL-SACCER-001/delete", follow_redirects=True)
check("deleting a strain needs a reason", b"Give a reason" in r.data
      and c.get("/species/MDL-SACCER-001").status_code == 200)
c.post("/species/MDL-SACCER-001/delete", data=dict(reason="duplicate record"), follow_redirects=True)
conn = db()
snap = conn.execute("SELECT detail FROM audit_log WHERE tenant_id=? AND entity='organism' AND action='delete' "
                    "ORDER BY audit_id DESC", (tid_a,)).fetchone()
conn.close()
check("deleting a strain writes a full snapshot to the audit log",
      snap is not None and "MDL-SACCER-001" in snap["detail"] and "duplicate record" in snap["detail"])
check("deleting a strain removes its photos", not any((_DD / p).exists() for p in rest))
check("deleting a strain removes its model files", not any((_DD / m).exists() for m in mods))
check("deleting Lab A's strain leaves Lab B's files alone",
      all((_DD / x).exists() for x in b_models))
c.get("/logout")

# --- Headers, error pages, safe exports, export -> import round trip ----------------
import zipfile as _zip, csv as _csv
c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)
r = c.get("/catalog")
check("security headers are sent", r.headers.get("X-Content-Type-Options") == "nosniff"
      and r.headers.get("X-Frame-Options") == "SAMEORIGIN" and "Referrer-Policy" in r.headers)
check("private pages are not cached", r.headers.get("Cache-Control") == "no-store")
r = c.get("/species/NOPE-000")
check("unknown strain shows a friendly 404 page", r.status_code == 404 and b"Not found" in r.data and b"Traceback" not in r.data)
r = c.post("/admin/import", data=dict(file=(_io.BytesIO(b"0" * (33 * 1024 * 1024)), "big.csv")),
           content_type="multipart/form-data")
check("an oversized upload gets a friendly 413 page", r.status_code == 413 and b"too large" in r.data)
c.get("/logout")
c.post("/login", data=dict(username="labuser", password="labuser-pass-123"), follow_redirects=True)
r = c.get("/admin")
check("a non-admin hitting /admin gets a friendly 403 page", r.status_code == 403 and b"Not allowed" in r.data)
c.get("/logout")

# spreadsheet formulas in exports are neutralized
c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)
evil = '=HYPERLINK("http://evil.example","click")'
c.post("/species/new", data=dict(genus="Formula", species="tester", notes=evil,
                                 storage_freezer="Freezer 4", storage_box="Box 7", storage_position="C1"))
c.post("/species/MDL-FORTES-001/edit", data=dict(full_name="Formula tester", notes=evil, storage_temp_c="-80",
       viability_category="stable", collection_type="library", storage_freezer="Freezer 4",
       storage_box="Box 7", storage_position="C1"))
zdata = c.get("/export").data
zf_ = _zip.ZipFile(_io.BytesIO(zdata))
flat_bytes = zf_.read("catalog_flat.csv")
flat_rows = list(_csv.DictReader(_io.StringIO(flat_bytes.decode())))
frow = next(r_ for r_ in flat_rows if r_["genus"] == "Formula")
check("CSV export neutralizes a leading '=' formula", frow["notes"] == "'" + evil)
check("CSV export leaves real negative numbers alone", frow["storage_temp_c"] == "-80")
check("CSV export includes storage, source and custom columns",
      {"storage_freezer", "storage_box", "source_lab", "Biosafety level"} <= set(flat_rows[0].keys()))
check("ZIP export includes the custom field tables", "custom_fields.csv" in zf_.namelist())
xl = _openpyxl.load_workbook(_io.BytesIO(c.get("/export/xlsx").data))
ws_ = xl["catalog_flat"]
hdr_ = [cell.value for cell in ws_[1]]
notes_col = hdr_.index("notes") + 1
gen_col = hdr_.index("genus") + 1
xcell = next(ws_.cell(row=i, column=notes_col) for i in range(2, ws_.max_row + 1)
             if ws_.cell(row=i, column=gen_col).value == "Formula")
check("XLSX export stores the formula text as text, not a formula",
      xcell.data_type != "f" and xcell.value == evil)
check("XLSX export has the custom field sheets", "custom_fields" in xl.sheetnames)
c.get("/logout")

# Round trip: Lab A's exported catalog re-imports cleanly into a brand-new lab
fh, fr = _importer.parse_file("catalog_flat.csv", flat_bytes)
check("the export's column names are all recognized by the importer",
      all(k for k, h in zip(_importer.auto_map(fh), fh) if h in
          ("genus", "species", "strain", "accessions", "tags", "storage_freezer", "storage_box",
           "storage_position", "source_lab", "gram_stain", "last_revival_date", "revival_interval_days")))
conn = db()
create_lab(conn, "Harbour Cultures", "adminC", "adminC-pass-123", "Admin C")
conn.close()
r = c.post("/login", data=dict(username="adminC", password="adminC-pass-123"), follow_redirects=True)
check("(setup) a third, empty lab is ready", r.status_code == 200)
c.post("/admin/fields", data=dict(name="Biosafety level", field_type="choice", choices="BSL-1, BSL-2"))
conn = db()
tid_c = conn.execute("SELECT tenant_id FROM users WHERE username='adminC'").fetchone()[0]
fid_c = conn.execute("SELECT field_id FROM custom_fields WHERE tenant_id=?", (tid_c,)).fetchone()[0]
conn.close()
cm = _importer.auto_map(fh, {f"cf_{fid_c}": "Biosafety level"})
cdef = {f"cf_{fid_c}": dict(name="Biosafety level", field_type="choice", choices=["BSL-1", "BSL-2"])}
recs = _importer.build_records(fr, cm, cdef)
_importer.mark_duplicates(recs, {}, {})
expected = sum(1 for r_ in recs if not r_["errors"] and not r_["duplicate_of"])
check("none of Lab A's exported rows is rejected as an error", not any(r_["errors"] for r_ in recs))
url_c = c.post("/admin/import", data=dict(file=(_io.BytesIO(flat_bytes), "catalog_flat.csv")),
               content_type="multipart/form-data").headers["Location"]
r = c.post(url_c, data={"action": "commit", "row": [str(r_["row_number"]) for r_ in recs],
                        **{f"map_{i}": k for i, k in enumerate(cm) if k}}, follow_redirects=True)
conn = db()
n_c = conn.execute("SELECT COUNT(*) FROM organisms WHERE tenant_id=?", (tid_c,)).fetchone()[0]
check(f"the new lab got every non-duplicate strain ({expected})", n_c == expected and expected > 5)

def _profile(tid, genus):
    o = conn.execute("SELECT * FROM organisms WHERE tenant_id=? AND genus=?", (tid, genus)).fetchone()
    accs = sorted((a["culture_collection"], a["accession_number"]) for a in
                  conn.execute("SELECT * FROM accessions WHERE organism_id=?", (o["organism_id"],)))
    tgs = sorted(t["tag"] for t in conn.execute("SELECT tag FROM tags WHERE organism_id=?", (o["organism_id"],)))
    cf = [x["value"] for x in conn.execute("SELECT value FROM custom_field_values WHERE organism_id=?", (o["organism_id"],))]
    lab = conn.execute("SELECT name FROM labs WHERE lab_id=?", (o["sample_source_lab_id"],)).fetchone()
    return (o["species"], o["strain"], o["gram_stain"], o["viability_category"], o["revival_interval_days"],
            o["last_revival_date"], o["storage_freezer"], o["storage_box"], o["storage_position"],
            accs, tgs, cf, lab["name"] if lab else None)

for genus in ("Gluconobacter", "Formula"):
    pa, pc = _profile(tid_a, genus), _profile(tid_c, genus)
    check(f"round trip preserves {genus} (storage, accessions, tags, custom field, source)", pa == pc)
g_a = _profile(tid_a, "Gluconobacter")
check("the round-tripped custom field value really arrived", g_a[11] == ["BSL-1"] and _profile(tid_c, "Gluconobacter")[11] == ["BSL-1"])
conn.close()
c.get("/logout")

# /connect uses the real address and a working QR
c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True,
       base_url="http://lab-server:8000")
r = c.get("/connect", base_url="http://lab-server:8000")
check("connect page shows the address you're actually using", b"http://lab-server:8000" in r.data)
r = c.get("/qr.png", base_url="http://lab-server:8000")
check_qr("connect QR is a PNG", r.status_code == 200 and r.data[:8] == b"\x89PNG\r\n\x1a\n")
try:
    import cv2 as _cv2, numpy as _np
    _img = _cv2.imdecode(_np.frombuffer(r.data, _np.uint8), _cv2.IMREAD_GRAYSCALE)
    if _img is not None:
        _txt, _, _ = _cv2.QRCodeDetector().detectAndDecode(_img)
        check("connect QR decodes to that same address", _txt == "http://lab-server:8000")
except ImportError:
    pass
c.get("/logout")

# --- Upgrading older installs ------------------------------------------------------
import sqlite3 as _sq
from app import ensure_schema, _migrate_legacy_single_tenant_data

def _mem_db():
    m_ = _sq.connect(":memory:")
    m_.row_factory = _sq.Row
    m_.execute("PRAGMA foreign_keys = ON")
    m_.executescript((_Path(app.root_path) / "schema.sql").read_text())  # same base tables a new install starts from
    return m_

# a pre-accounts install: strains exist but there are no users at all
mc = _mem_db()
ensure_schema(mc)
mc.execute("INSERT INTO organisms (collection_id, genus, species, full_name) VALUES ('MDL-OLD-001','Old','one','Old one')")
mc.commit()
creds = _migrate_legacy_single_tenant_data(mc)
org = mc.execute("SELECT tenant_id FROM organisms WHERE collection_id='MDL-OLD-001'").fetchone()
check("a pre-accounts install's strains are moved into a lab", org["tenant_id"] is not None)
check("...and a lab admin account is created so someone can sign in",
      creds is not None and mc.execute("SELECT role, tenant_id FROM users WHERE username=?", (creds[0],)).fetchone()["role"] == "admin")
check("running the migration again changes nothing", _migrate_legacy_single_tenant_data(mc) is None
      and mc.execute("SELECT COUNT(*) FROM tenants").fetchone()[0] == 1)
mc.close()

# a 4.x install with a platform superadmin: that account is removed on upgrade
mc = _mem_db()
ensure_schema(mc)
mc.execute("INSERT INTO tenants (name, join_code) VALUES ('Old Lab', 'AAAABBBB')")
mc.execute("INSERT INTO users (tenant_id, username, password_hash, role) VALUES (1,'oldadmin','x','admin')")
mc.execute("INSERT INTO users (tenant_id, username, password_hash, role) VALUES (NULL,'superadmin','x','superadmin')")
mc.commit()
ensure_schema(mc)
names = {r["username"]: r["role"] for r in mc.execute("SELECT username, role FROM users")}
check("upgrading removes the old platform superadmin account", names == {"oldadmin": "admin"})
check("...and the old activation codes table", mc.execute(
    "SELECT 1 FROM sqlite_master WHERE name='activation_codes'").fetchone() is None)
# a 4.x install holding several labs must be split before this version starts
mc.execute("INSERT INTO tenants (name, join_code) VALUES ('Other Lab', 'CCCCDDDD')")
mc.commit()
try:
    ensure_schema(mc)
    _refused = None
except SystemExit as e:
    _refused = str(e)
check("a database holding two labs is refused at start-up", _refused is not None and "split-lab" in _refused)
mc.close()

# manage.py split-lab copies one lab out of an old multi-lab database
import manage as _manage
_split_dir = _Path(_TEST_DATA) / "split-b"
import contextlib as _ctx
with _ctx.redirect_stdout(io.StringIO()):
    _manage.split_lab(str(tid_b), str(_split_dir))
_sb = _sq.connect(_split_dir / "microbe_catalog.db")
check("split-lab leaves exactly one lab in the copy",
      _sb.execute("SELECT COUNT(*) FROM tenants").fetchone()[0] == 1
      and _sb.execute("SELECT tenant_id FROM tenants").fetchone()[0] == tid_b)
check("...with only that lab's strains and accounts",
      _sb.execute("SELECT COUNT(*) FROM organisms WHERE tenant_id != ?", (tid_b,)).fetchone()[0] == 0
      and _sb.execute("SELECT COUNT(*) FROM organisms").fetchone()[0] > 0
      and _sb.execute("SELECT COUNT(*) FROM users WHERE tenant_id IS NOT ?", (tid_b,)).fetchone()[0] == 0)
check("...and its model files", any((_split_dir / "models").iterdir()))
_sb.close()
_live = db()
check("split-lab leaves the original database untouched",
      _live.execute("SELECT COUNT(*) FROM tenants").fetchone()[0] >= 3)
_live.close()

# --- Password rules, my-account page, richer catalog search -------------------------
r = c.post("/signup", data=dict(join_code="ZZZZ-ZZZZ", username="weakuser", password="short"), follow_redirects=True)
check("sign-up rejects a too-short password", b"at least 8 characters" in r.data)
r = c.post("/signup", data=dict(join_code="ZZZZ-ZZZZ", username="samesame1", password="samesame1"), follow_redirects=True)
check("sign-up rejects a password equal to the username", b"same as the username" in r.data)
r = c.post("/signup", data=dict(join_code="ZZZZ-ZZZZ", username="commonpw", password="password1"), follow_redirects=True)
check("sign-up rejects a well-known password", b"too common" in r.data)
conn = db()
check("none of those weak sign-ups created an account",
      conn.execute("SELECT COUNT(*) FROM users WHERE username IN ('weakuser','samesame1','commonpw')").fetchone()[0] == 0)
conn.close()

c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)
r = c.post("/admin/users", data=dict(username="newguy", password="abc", role="user"), follow_redirects=True)
check("admin can't create an account with a weak password", b"at least 8 characters" in r.data)
c.post("/admin/users", data=dict(username="newguy", password="newguy-pass-1", role="user"))
conn = db()
nid = conn.execute("SELECT user_id FROM users WHERE username='newguy'").fetchone()[0]
conn.close()
r = c.post(f"/admin/users/{nid}/reset-password", data=dict(password="abc"), follow_redirects=True)
check("admin can't reset a password to a weak one", b"at least 8 characters" in r.data)
c.get("/logout")

check("account page needs a sign-in", c.get("/account").status_code == 302)
c.post("/login", data=dict(username="newguy", password="newguy-pass-1"), follow_redirects=True)
check("a regular user can open their account page", c.get("/account").status_code == 200)
r = c.post("/account", data=dict(current_password="not-it", new_password="brand-new-pass-9",
                                 confirm_password="brand-new-pass-9"), follow_redirects=True)
check("changing password needs the current password", b"current password isn" in r.data)
r = c.post("/account", data=dict(current_password="newguy-pass-1", new_password="brand-new-pass-9",
                                 confirm_password="different-pass-9"), follow_redirects=True)
check("the two new passwords must match", b"don" in r.data and b"match" in r.data)
r = c.post("/account", data=dict(current_password="newguy-pass-1", new_password="short",
                                 confirm_password="short"), follow_redirects=True)
check("a weak new password is refused", b"at least 8 characters" in r.data)
r = c.post("/account", data=dict(current_password="newguy-pass-1", new_password="brand-new-pass-9",
                                 confirm_password="brand-new-pass-9"), follow_redirects=True)
check("password change succeeds", b"Password changed" in r.data)
c.get("/logout")
r = c.post("/login", data=dict(username="newguy", password="newguy-pass-1"), follow_redirects=True)
check("the old password no longer works", b"Incorrect username or password" in r.data)
r = c.post("/login", data=dict(username="newguy", password="brand-new-pass-9"), follow_redirects=True)
check("the new password works", b"Log out" in r.data)

# search covers accessions, tags, storage, custom values, and matches every word
def _hits(q_):
    page = c.get("/catalog", query_string=dict(q=q_)).data
    return {m.decode() for m in __import__("re").findall(rb"MDL-[A-Z]{6}-\d{3}", page)}
c.get("/logout")
c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)
check("search finds a strain by accession number", "MDL-LACBRE-001" in _hits("NCYC 9001"))
check("search finds strains by storage location", "MDL-FORTES-001" in _hits("Freezer 4"))
check("search needs every word to match", "MDL-FORTES-001" in _hits("freezer 4 formula")
      and "MDL-FORTES-001" not in _hits("freezer 4 lactobacillus"))
check("search finds a strain by tag", "MDL-LACBRE-001" in _hits("spoilage"))
check("search finds a strain by a custom field value", "MDL-GLUOXY-001" in _hits("BSL-1"))
check("search finds a strain by source lab", "MDL-LACBRE-001" in _hits("Import Test Supplier"))
check("search still ignores another lab's data", "MDL-FORTES-001" not in _hits("Harbour"))
c.get("/logout")

# --- A maximum-size import (regression: "database is locked" once the write got big) ---
import time as _time
big_rows = ["Genus,Species,Strain,Accessions,Tags"]
for i_ in range(5000):
    big_rows.append(f"Bulkgenus{i_ % 8},sp{i_ % 400},S{i_},NCYC {20000 + i_}; ATCC {70000 + i_},\"t{i_ % 20}, t{i_ % 7}\"")
big_csv = "\n".join(big_rows).encode()
c.post("/login", data=dict(username="adminC", password="adminC-pass-123"), follow_redirects=True)
conn = db()
before_big = conn.execute("SELECT COUNT(*) FROM organisms WHERE tenant_id=?", (tid_c,)).fetchone()[0]
conn.close()
_t0 = _time.time()
url_big = c.post("/admin/import", data=dict(file=(_io.BytesIO(big_csv), "big.csv")),
                 content_type="multipart/form-data").headers["Location"]
bh, br = _importer.parse_file("big.csv", big_csv)
bm = _importer.auto_map(bh)
r = c.post(url_big, data={"action": "commit", "row": [str(n) for n in range(2, 5002)],
                          **{f"map_{i}": k for i, k in enumerate(bm) if k}}, follow_redirects=True)
conn = db()
n_big = conn.execute("SELECT COUNT(*) FROM organisms WHERE tenant_id=?", (tid_c,)).fetchone()[0] - before_big
conn.close()
check("a 5,000-row import completes and adds every strain", n_big == 5000)
check("...in a reasonable time (under 30s)", _time.time() - _t0 < 30)
check("the catalog still renders with 5,000+ strains", c.get("/catalog").status_code == 200)
c.get("/logout")

# --- 3D models still display when file paths were saved the Windows way ---------------
from app import _normalize_stored_paths
import re as _re3
c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)
c.post("/species/MDL-LEVBRE-004/generate", follow_redirects=True)
conn = db()
oid4 = conn.execute("SELECT organism_id FROM organisms WHERE tenant_id=? AND collection_id='MDL-LEVBRE-004'", (tid_a,)).fetchone()[0]
posix = [r["file_path"] for r in conn.execute("SELECT file_path FROM models WHERE organism_id=?", (oid4,))]
check("(setup) new model paths are stored with forward slashes", posix and all("\\" not in x for x in posix))
# what a Windows install actually stored: models\name.stl
conn.execute("UPDATE models SET file_path=REPLACE(file_path,'/',char(92)) WHERE organism_id=?", (oid4,))
conn.commit()
conn.close()
page = c.get("/species/MDL-LEVBRE-004").data.decode()
srcs = _re3.findall(r'<model-viewer src="([^"]+)"', page)
check("the page builds clean model URLs even from backslash paths", srcs and all("%5C" not in s_ and "\\" not in s_ for s_ in srcs))
check("...and those models load", all(c.get(s_).status_code == 200 for s_ in srcs))
dl = _re3.findall(r'href="(/models/[^"]+\.stl)"', page)
check("...and the STL download links work", dl and all(c.get(d_).status_code == 200 for d_ in dl))
check("a path with a backslash in the URL is still refused", c.get("/models/..%5Capp.py").status_code == 404
      and c.get("/models/x%5Cy.glb").status_code == 404)
conn = db()
_normalize_stored_paths(conn)
fixed = [r["file_path"] for r in conn.execute("SELECT file_path FROM models WHERE organism_id=?", (oid4,))]
conn.close()
check("start-up repair turns stored backslash paths into forward-slash paths",
      fixed and all("\\" not in x and x.startswith("models/") for x in fixed))
check("the viewer script is served by the app itself (no CDN needed)",
      c.get("/static/vendor/model-viewer.min.js").status_code == 200 and "unpkg" not in page)
check("the page explains it if the viewer can't load", "viewer-msg" in page)
c.get("/logout")

# --- Joining an existing lab with a join code ---------------------------------
# A new colleague asks to join with the lab's join code and lands in the same lab.
cj = app.test_client()
cj.post("/login", data=dict(username="adminA", password="adminA-pass-123"))
adm = cj.get("/admin").data.decode()
m_code = _re3.search(r'letter-spacing:[^>]*>\s*([A-Z0-9]{4}-[A-Z0-9]{4})\s*<', adm)
check("the lab admin page shows the lab's join code", m_code is not None)
join_code = m_code.group(1) if m_code else "XXXX-XXXX"
conn = db()
tidA = conn.execute("SELECT tenant_id FROM users WHERE username='adminA'").fetchone()["tenant_id"]
labA_name = conn.execute("SELECT name FROM tenants WHERE tenant_id=?", (tidA,)).fetchone()["name"]
conn.close()
cj.get("/logout")

cp = app.test_client()                       # "the phone"
r = cp.post("/signup", data=dict(join_code="ZZZZ-ZZZZ", username="phoneuser", password="phone-pass-123"), follow_redirects=True)
check("a wrong join code is refused with a clear message", b"wasn&#39;t recognised" in r.data or b"wasn't recognised" in r.data)
conn = db()
check("...and nothing was created", conn.execute("SELECT COUNT(*) n FROM users WHERE username='phoneuser'").fetchone()["n"] == 0)
conn.close()
r = cp.post("/signup", data=dict(join_code=join_code.lower().replace("-", " "), username="phoneuser",
                                 password="phone-pass-123", full_name="Phone User"), follow_redirects=True)
check("joining with the lab's code (any case/spacing) sends a request", b"Request sent" in r.data)
conn = db()
u = conn.execute("SELECT * FROM users WHERE username='phoneuser'").fetchone()
check("...as a PENDING regular user of the SAME lab",
      u is not None and u["tenant_id"] == tidA and u["role"] == "user" and u["is_active"] == 0 and u["pending_approval"] == 1)
conn.close()
check("...who is not logged in by the request", cp.get("/", follow_redirects=False).status_code == 302)
r = cp.post("/login", data=dict(username="phoneuser", password="wrong-pass-999"), follow_redirects=True)
check("a pending person with a wrong password just sees the usual error", b"Incorrect username or password" in r.data)
r = cp.post("/login", data=dict(username="phoneuser", password="phone-pass-123"), follow_redirects=True)
check("a pending person is told they are waiting for approval", b"waiting for an admin" in r.data)
check("...and still can't see the catalog", cp.get("/", follow_redirects=False).status_code == 302)

cj2 = app.test_client()
cj2.post("/login", data=dict(username="adminA", password="adminA-pass-123"))
adm = cj2.get("/admin").data.decode()
check("the lab admin sees the join request", "Join requests" in adm and "phoneuser" in adm)
check("...and a count badge in the menu", 'class="badge b-warn">1<' in cj2.get("/").data.decode())
conn = db()
pend_id = conn.execute("SELECT user_id FROM users WHERE username='phoneuser'").fetchone()["user_id"]
conn.close()
# another lab's admin can't act on it
cB = app.test_client()
cB.post("/login", data=dict(username="adminB", password="adminB-pass-123"))
check("another lab's admin can't approve it", cB.post(f"/admin/join-requests/{pend_id}/approve").status_code == 404)
check("...and doesn't see it", "phoneuser" not in cB.get("/admin").data.decode())
cB.get("/logout")
r = cj2.post(f"/admin/join-requests/{pend_id}/approve", follow_redirects=True)
check("the admin can approve the request", b"Approved" in r.data)
r = cp.post("/login", data=dict(username="phoneuser", password="phone-pass-123"), follow_redirects=True)
page = cp.get("/").data.decode()
check("after approval the person logs in and sees the lab's existing catalog", "Saccharomyces" in page or "MDL-" in page)
r = cp.post("/species/new", data=dict(storage_freezer="Freezer Z", storage_box="Box Z", genus="Joinella", species="phonei", role="spoilage", risk="low",
                                      added_by="Phone User"), follow_redirects=True)
conn = db()
row = conn.execute("SELECT tenant_id FROM organisms WHERE genus='Joinella'").fetchone()
check("a strain added by the joined user lands in the shared lab", row is not None and row["tenant_id"] == tidA)
conn.close()
check("...and the admin sees it straight away", b"Joinella" in cj2.get("/").data)
check("a regular user can't open the admin page (so can't read the code or approve people)",
      cp.get("/admin").status_code in (302, 403)
      and cp.post(f"/admin/join-requests/{pend_id}/approve").status_code in (302, 403))
cp.get("/logout")

# declining removes the request
cd = app.test_client()
cd.post("/signup", data=dict(join_code=join_code, username="stranger1", password="stranger-pass-1"))
conn = db()
sid = conn.execute("SELECT user_id FROM users WHERE username='stranger1'").fetchone()["user_id"]
conn.close()
r = cj2.post(f"/admin/join-requests/{sid}/decline", follow_redirects=True)
conn = db()
check("declining a request removes it", b"Declined" in r.data
      and conn.execute("SELECT COUNT(*) n FROM users WHERE username='stranger1'").fetchone()["n"] == 0)
conn.close()
check("...and a declined person can't log in",
      b"Incorrect username or password" in cd.post("/login", data=dict(username="stranger1", password="stranger-pass-1"),
                                                  follow_redirects=True).data)

# a join code only opens its own lab, and regenerating it kills the old one
cj2.post("/admin/join-code/regenerate")
adm2 = cj2.get("/admin").data.decode()
new_code = _re3.search(r'letter-spacing:[^>]*>\s*([A-Z0-9]{4}-[A-Z0-9]{4})\s*<', adm2).group(1)
check("regenerating gives a different code", new_code != join_code)
cq = app.test_client()
r = cq.post("/signup", data=dict(join_code=join_code, username="lateuser", password="late-pass-123"), follow_redirects=True)
check("the old code no longer works", b"recognised" in r.data)
conn = db()
conn.close()
cj2.get("/logout")

# an install from before join codes gets codes for every lab on upgrade
conn = db()
conn.execute("UPDATE tenants SET join_code=NULL")
conn.commit()
_ensure_join_codes(conn)
codes = [r_["join_code"] for r_ in conn.execute("SELECT join_code FROM tenants")]
conn.close()
check("labs without a join code (older installs) are given one", codes and all(c_ and len(c_) == 8 for c_ in codes)
      and len(set(codes)) == len(codes))

# a request to join without any join code is refused
r = app.test_client().post("/signup", data=dict(username="nocode9", password="nocode-pass-1234"), follow_redirects=True)
check("asking to join without a join code is refused", b"join code, a username and a password" in r.data)
check("the sign-up page cannot create a lab", b"lab_name" not in app.test_client().get("/signup").data)

# --- About and Contact pages ---------------------------------------------------
import os as _os9
ca = app.test_client()                                   # nobody signed in
r = ca.get("/about")
check("the About page opens without signing in", r.status_code == 200)
pg = r.data.decode()
from app import APP_VERSION as _V
check("About describes the tool and shows the version", "About the Microbial Digital Library" in pg and _V in pg)
check("About is honest about limits (no automatic colony counting)", "does not count colonies automatically" in pg)
check("About explains the roles and the join code", "Lab admin" in pg and "join code" in pg
      and "activation" not in pg.lower() and "platform administrator" not in pg.lower())
import re as _re9
_visible = _re9.sub(r"<style.*?</style>|<!--.*?-->|<script.*?</script>", "", pg, flags=_re9.S)
check("About has no ' -- ' dashes in its visible text", " -- " not in _visible)
r = ca.get("/contact")
check("the Contact page opens without signing in", r.status_code == 200)
pg = r.data.decode()
check("Contact has the message form", 'name="message"' in pg and 'name="category"' in pg)
check("Contact shows no person's details unless configured", "Who runs this installation" not in pg and "mailto:" not in pg)
check("the menu and footer link to About and Contact", 'href="/about"' in pg and 'href="/contact"' in pg)
check("the login page's menu links to About too", b'href="/about"' in ca.get("/login").data)

_os9.environ["CONTACT_EMAIL"] = "lab@example.org"
_os9.environ["CONTACT_NAME"] = "Dr Example <b>"
_os9.environ["CONTACT_WEBSITE"] = "javascript:alert(1)"
pg = ca.get("/contact").data.decode()
check("configured contact details are shown (email as a link)", "mailto:lab@example.org" in pg and "Who runs this installation" in pg)
check("contact details are HTML-escaped", "Dr Example &lt;b&gt;" in pg and "Dr Example <b>" not in pg)
check("a non-http(s) website is never linked", "javascript:alert" not in pg)
for k in ("CONTACT_EMAIL", "CONTACT_NAME", "CONTACT_WEBSITE"):
    _os9.environ.pop(k, None)

def _msgs():
    conn = db()
    rows = conn.execute("SELECT * FROM contact_messages ORDER BY message_id").fetchall()
    conn.close()
    return rows
before = len(_msgs())
r = ca.post("/contact", data=dict(category="access", name="Ada", email="ada@example.org",
                                  message="Please could I have the join code for the lab?"), follow_redirects=True)
check("an anonymous visitor can send a message", b"your message was sent" in r.data and len(_msgs()) == before + 1)
m = _msgs()[-1]
check("...stored with its details and as not signed in", m["category"] == "access" and m["email"] == "ada@example.org"
      and m["tenant_id"] is None and m["username"] is None and m["is_read"] == 0)
r = ca.post("/contact", data=dict(category="question", message="hi"), follow_redirects=True)
check("a message that is too short is refused", b"Please write a message" in r.data and len(_msgs()) == before + 1)
r = ca.post("/contact", data=dict(category="question", message="x" * 4001), follow_redirects=True)
check("a message that is too long is refused", b"too long" in r.data and len(_msgs()) == before + 1)
r = ca.post("/contact", data=dict(category="question", email="not-an-email", message="A real question here"), follow_redirects=True)
check("a bad email address is refused", b"doesn&#39;t look right" in r.data or b"doesn't look right" in r.data)
r = ca.post("/contact", data=dict(category="question", message="Buy cheap pills now", website_url="http://spam"), follow_redirects=True)
check("a bot that fills the hidden field is silently dropped", len(_msgs()) == before + 1)
r = ca.post("/contact", data=dict(category="nonsense", message="Category should fall back safely"), follow_redirects=True)
check("an unknown category is stored as 'other'", _msgs()[-1]["category"] == "other")
r = ca.post("/contact", data=dict(category="bug", message="<script>alert(1)</script> breaks the page"), follow_redirects=True)

# a signed-in user's message is tied to their own account and lab, not to what the form says
cu = app.test_client()
cu.post("/login", data=dict(username="adminA", password="adminA-pass-123"))
check("the Contact page pre-fills a signed-in user's name", b'value="Admin A"' in cu.get("/contact").data)
cu.post("/contact", data=dict(category="feature", name="Somebody Else", message="Please add a dark mode toggle"), follow_redirects=True)
m = _msgs()[-1]
conn = db()
tidA9 = conn.execute("SELECT tenant_id FROM users WHERE username='adminA'").fetchone()["tenant_id"]
conn.close()
check("a signed-in message records the real account and lab", m["username"] == "adminA" and m["tenant_id"] == tidA9)
cu.get("/logout")
cr9 = app.test_client()
cr9.post("/login", data=dict(username="labuser", password="labuser-pass-123"))
check("a regular user can't read the messages", cr9.get("/admin").status_code == 403)
check("...or act on them", cr9.post(f"/admin/messages/{m['message_id']}/delete").status_code == 403)
cr9.get("/logout")

cs9 = app.test_client()
cs9.post("/login", data=dict(username="adminA", password="adminA-pass-123"))
pg = cs9.get("/admin").data.decode()
check("the lab admins see the messages on the Admin page", "join code for the lab" in pg and "Ada" in pg)
check("...with the sender's account for signed-in messages", "signed in as adminA" in pg)
check("...and message text is escaped (no script injection)", "<script>alert(1)</script>" not in pg and "&lt;script&gt;" in pg)
mid = _msgs()[0]["message_id"]
cs9.post(f"/admin/messages/{mid}/read")
check("a message can be marked as read", db().execute("SELECT is_read FROM contact_messages WHERE message_id=?", (mid,)).fetchone()["is_read"] == 1)
cs9.post(f"/admin/messages/{mid}/unread")
check("...and as new again", db().execute("SELECT is_read FROM contact_messages WHERE message_id=?", (mid,)).fetchone()["is_read"] == 0)
cs9.post(f"/admin/messages/{mid}/delete")
check("...and deleted", db().execute("SELECT COUNT(*) n FROM contact_messages WHERE message_id=?", (mid,)).fetchone()["n"] == 0)
check("an unknown action on a message is a 404", cs9.post("/admin/messages/1/explode").status_code == 404)
cs9.get("/logout")

# --- storage location for every new isolate ---------------------------------
cst = app.test_client()
cst.post("/login", data=dict(username="adminA", password="adminA-pass-123"))
_tid = db().execute("SELECT tenant_id FROM users WHERE username='adminA'").fetchone()["tenant_id"]
def _n_orgs(genus):
    return db().execute("SELECT COUNT(*) n FROM organisms WHERE tenant_id=? AND genus=?", (_tid, genus)).fetchone()["n"]
_form = cst.get("/species/new").data.decode()
check("add form marks freezer and box as required",
      'name="storage_freezer" required' in _form and 'name="storage_box" required' in _form)
check("add form also asks for format, temperature and date stored",
      all(('name="%s"' % f) in _form for f in ("storage_format", "storage_temp_c", "date_stored", "storage_location")))
r = cst.post("/species/new", data=dict(genus="Nolocus", species="none"), follow_redirects=True)
check("an isolate with no storage location is refused", _n_orgs("Nolocus") == 0 and b"Storage location is required" in r.data)
r = cst.post("/species/new", data=dict(genus="Nolocus", species="half", storage_freezer="Freezer Q"), follow_redirects=True)
check("freezer without a box is refused too", _n_orgs("Nolocus") == 0)
r = cst.post("/species/new", data=dict(genus="Placed", species="first", storage_freezer="Freezer Q", storage_rack="Rack 2",
                                       storage_box="Box 5", storage_position="B7", storage_format="Glycerol stock",
                                       storage_temp_c="-80", date_stored="2026-10-01"), follow_redirects=True)
_p1 = db().execute("SELECT * FROM organisms WHERE tenant_id=? AND genus='Placed'", (_tid,)).fetchone()
check("an isolate with a location is saved",
      _p1 is not None and _p1["storage_freezer"] == "Freezer Q" and _p1["storage_box"] == "Box 5" and _p1["storage_position"] == "B7")
check("format, temperature and date stored are saved from the add form",
      _p1["storage_format"] == "Glycerol stock" and _p1["storage_temp_c"] == "-80" and _p1["date_stored"] == "2026-10-01")
check("no warning when the position is free", b"already recorded" not in r.data)
r = cst.post("/species/new", data=dict(genus="Placed", species="second", storage_freezer="freezer q", storage_rack="rack 2",
                                       storage_box="box 5", storage_position="b7"), follow_redirects=True)
check("adding to an occupied position warns and names the other isolate",
      b"already recorded" in r.data and _p1["collection_id"].encode() in r.data)
check("...but the isolate is still saved", _n_orgs("Placed") == 2)
_p2 = db().execute("SELECT collection_id FROM organisms WHERE tenant_id=? AND species='second'", (_tid,)).fetchone()
r = cst.post(f"/species/{_p2['collection_id']}/edit", data=dict(storage_freezer="Freezer Q", storage_rack="Rack 2",
             storage_box="Box 5", storage_position="B7", collection_type="library", viability_category="unknown"), follow_redirects=True)
check("editing into an occupied position warns", b"already recorded" in r.data)
r = cst.post(f"/species/{_p2['collection_id']}/edit", data=dict(storage_freezer="Freezer Q", storage_rack="Rack 2",
             storage_box="Box 5", storage_position="B8", collection_type="library", viability_category="unknown"), follow_redirects=True)
check("moving to a free position shows no warning", b"already recorded" not in r.data)
check("the same strain saved again at its own position is not a clash",
      b"already recorded" not in cst.post(f"/species/{_p2['collection_id']}/edit", data=dict(storage_freezer="Freezer Q",
          storage_rack="Rack 2", storage_box="Box 5", storage_position="B8", collection_type="library",
          viability_category="unknown"), follow_redirects=True).data)
# another lab using the same freezer, box and position is not a clash
_other = db().execute("SELECT COUNT(*) n FROM organisms WHERE tenant_id<>? AND storage_freezer='Freezer Q'", (_tid,)).fetchone()["n"]
check("other labs' records never count as clashes", _other == 0)
# unplaced strains are called out at the top of the storage page
_conn = db()
_conn.execute("UPDATE organisms SET storage_freezer=NULL, storage_rack=NULL, storage_box=NULL, storage_position=NULL, storage_location=NULL WHERE organism_id=?",
              (_p1["organism_id"],))
_conn.commit(); _conn.close()
r = cst.get("/storage")
check("storage page warns at the top about strains with no location",
      b"no location recorded" in r.data and b'href="#unplaced"' in r.data and b'id="unplaced"' in r.data)
cst.get("/logout")

# --- ready to share -----------------------------------------------------------
import os as _os9
_root = _os9.path.dirname(_os9.path.abspath(__file__))
_readme = open(_os9.path.join(_root, "README.md"), encoding="utf-8").read()
check("README has no ' -- ' dash signs", " -- " not in _readme)
check("README does not claim 3D models come back automatically", "back automatically" not in _readme)
check("README explains how people open the app", "How people open it" in _readme and "127.0.0.1:5000" in _readme)
check("a LICENSE file is included", _os9.path.isfile(_os9.path.join(_root, "LICENSE")))
_gi = open(_os9.path.join(_root, ".gitignore"), encoding="utf-8").read().split()
check(".gitignore keeps secrets and data out of a public repository",
      all(x in _gi for x in (".env", ".secret_key", "*.db", "backups/", "imports/")))

# --- responsibility and liability notices ---------------------------------------
_sec = open(_os9.path.join(_root, "SECURITY.md"), encoding="utf-8").read()
check("README has a 'Before you rely on it' section with a no liability statement",
      "## Before you rely on it" in _readme and "not liable" in _readme and "has no access" in _readme)
check("SECURITY.md explains how to report a problem privately", "Report a vulnerability" in _sec and "not liable" in _sec)
check("README and SECURITY.md have no ' -- ' dash signs", " -- " not in _sec and " -- " not in _readme)
_about = app.test_client().get("/about").data.decode()
check("About page states who is responsible for the data and that the authors are not liable",
      "Responsibility for the data" in _about and "not liable" in _about and "no access to this installation" in _about)

# --- detailed evaluation questionnaire -----------------------------------------
import feedback_survey as _fs
# scoring: all 5s on odd items and all 1s on even items is the best possible, 100
_best = {("sus_%d" % i): (5 if i % 2 else 1) for i in range(1, 11)}
_worst = {("sus_%d" % i): (1 if i % 2 else 5) for i in range(1, 11)}
_mid = {("sus_%d" % i): 3 for i in range(1, 11)}
check("SUS score: best possible answers give 100", _fs.sus_score(_best) == 100.0)
check("SUS score: worst possible answers give 0", _fs.sus_score(_worst) == 0.0)
check("SUS score: all neutral answers give 50", _fs.sus_score(_mid) == 50.0)
_known = dict(sus_1=4, sus_2=2, sus_3=5, sus_4=1, sus_5=4, sus_6=2, sus_7=4, sus_8=1, sus_9=5, sus_10=2)
check("SUS score matches a hand worked example (85)", _fs.sus_score(_known) == 85.0)
check("SUS score is blank when an item is missing", _fs.sus_score({k: v for k, v in _best.items() if k != "sus_4"}) is None)
check("SUS bands: 68 is above average, 50 is poor", _fs.sus_band(68) == "above average" and _fs.sus_band(50) == "poor")
check("NPS groups: 10 promoter, 8 passive, 6 detractor",
      (_fs.nps_group("10"), _fs.nps_group("8"), _fs.nps_group("6")) == ("promoter", "passive", "detractor"))

cq = app.test_client()
cq.post("/login", data=dict(username="adminA", password="adminA-pass-123"))
# the form shows every section and keeps the two required questions
_pg = cq.get("/feedback").data.decode()
check("feedback form shows all six sections", all(("%s. " % l) in _pg for l in "ABCDEF"))
check("feedback form asks the ten usability statements", all(('name="sus_%d"' % i) in _pg for i in range(1, 11)))
check("feedback form has a research consent box", 'name="consent_research"' in _pg)
check("feedback form asks before and after times", 'name="prev_find_min"' in _pg and 'name="now_find_min"' in _pg)
check("feedback form rates every feature", all(('name="%s"' % k) in _pg for k, _ in _fs.FEATURE_RATING_ITEMS))

# a bad answer keeps the form filled in and saves nothing
conn = db(); _before = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]; conn.close()
r = cq.post("/feedback", data={"rating": "4", "ease": "easy", "prev_find_min": "abc", "best_thing": "Keeps its place"})
check("a non numeric time is refused with a clear message", b"please enter a number" in r.data)
check("...and the form keeps what was typed", b"Keeps its place" in r.data)
r2 = cq.post("/feedback", data={"rating": "4", "ease": "easy", "mixups_caught": "2.5"})
check("a fractional count is refused", b"whole number" in r2.data)
r3 = cq.post("/feedback", data={"rating": "4", "ease": "easy", "hours_saved_week": "9999"})
check("an out of range number is refused", b"between" in r3.data)
r4 = cq.post("/feedback", data={"rating": "4", "ease": "easy", "role": "wizard"})
check("an answer that is not one of the choices is refused", b"choose one of the listed answers" in r4.data)
r5 = cq.post("/feedback", data={"rating": "4", "ease": "easy", "contact_email": "not an email"})
check("a malformed email is refused", b"check it" in r5.data)
conn = db(); _after = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]; conn.close()
check("none of the refused submissions were stored", _after == _before)

# a full response is stored with its answers, score and measured usage
_full = {"rating": "5", "ease": "very_easy", "most_used": "catalog", "improvement": "Add batch labels",
         "consent_research": "1", "name": "Dr Test", "contact_email": "dr.test@example.org",
         "role": "lab_manager", "lab_type": "academic_research", "devices": ["phone", "desktop_or_laptop"],
         "previous_methods": ["a_spreadsheet"], "prev_find_min": "20", "now_find_min": "2",
         "prev_entry_min": "10", "now_entry_min": "5", "hours_saved_week": "3",
         "mixups_caught": "4", "overdue_caught": "7", "errors_seen": "never", "data_problem": "no",
         "f_catalog": "5", "f_storage": "5", "f_models": "not_used", "speed": "4",
         "nps": "10", "continue_use": "yes_definitely", "best_thing": "I can find any tube",
         "worst_thing": "Wants more label sizes"}
_full.update({k: str(v) for k, v in _known.items()})
r = cq.post("/feedback", data=_full, follow_redirects=True)
check("a complete response is accepted", b"Thanks" in r.data)
conn = db()
_row = conn.execute("SELECT * FROM feedback WHERE name='Dr Test'").fetchone()
conn.close()
_ans = _fs.loads(_row["answers"])
check("the SUS score is computed and stored", _row["sus_score"] == 85.0)
check("research consent is stored", _row["consent_research"] == 1)
check("answers are stored as structured data",
      _ans.get("prev_find_min") == 20 and _ans.get("mixups_caught") == 4 and _ans.get("devices") == ["phone", "desktop_or_laptop"])
check("usage figures were measured automatically",
      _row["app_version"] == __import__("app").APP_VERSION and _row["strains_count"] >= 1 and _row["days_in_use"] is not None
      and _row["strains_with_location"] is not None and _row["plates_count"] is not None)

# a second response that did not consent, to test the export filter
cq.post("/feedback", data={"rating": "3", "ease": "ok", "name": "Private Person", "contact_email": "p@example.org",
                          "best_thing": "Not for the paper", "prev_find_min": "10", "now_find_min": "5"})

# summary
r = cq.get("/feedback/summary")
check("summary shows the usability score", b"usability score of 100" in r.data and b"85" in r.data)
check("summary shows the recommend score", b"recommend score" in r.data)
check("summary shows before and after times", b"Find one strain" in r.data)
check("summary shows feature usefulness", b"How useful each feature was" in r.data and b"3D colony models" in r.data)
check("summary shows the measured usage figures", b"Usage when the latest response was sent" in r.data)
check("summary shows the written answers", b"I can find any tube" in r.data)
_summ = _fs.summarize([dict(rating=5, answers=_fs.dumps({"prev_find_min": 20, "now_find_min": 2, "nps": "10"}), sus_score=85.0, consent_research=1),
                       dict(rating=3, answers=_fs.dumps({"prev_find_min": 10, "now_find_min": 5, "nps": "6"}), sus_score=None, consent_research=0)])
check("summary maths: before and after averages and change",
      _summ["pairs"]["find"]["before"] == 15 and _summ["pairs"]["find"]["after"] == 3.5 and _summ["pairs"]["find"]["change_pct"] == -77)
check("summary maths: recommend score is promoters minus detractors", _summ["nps"]["score"] == 0)
check("summary copes with old rows that have no answers",
      _fs.summarize([dict(rating=4, answers=None, sus_score=None, consent_research=0)])["n"] == 1)

# export for the paper
import zipfile as _z9
r = cq.get("/feedback/export")
check("lab admin can download the research export", r.status_code == 200 and r.mimetype == "application/zip")
_zf = _z9.ZipFile(io.BytesIO(r.data))
check("export has responses and a data dictionary", set(_zf.namelist()) == {"responses.csv", "data_dictionary.csv"})
_resp = _zf.read("responses.csv").decode("utf-8-sig")
check("export includes only responses that agreed to research use", "I can find any tube" in _resp and "Not for the paper" not in _resp)
check("export never includes names or email addresses",
      "Dr Test" not in _resp and "dr.test@example.org" not in _resp and "p@example.org" not in _resp and "Private Person" not in _resp)
check("export shows readable answers, not codes", "Lab manager" in _resp and "A spreadsheet" in _resp and "Very easy" in _resp)
_all = _z9.ZipFile(io.BytesIO(cq.get("/feedback/export?all=1").data)).read("responses.csv").decode("utf-8-sig")
check("the include all option adds the responses without consent, still anonymous",
      "Not for the paper" in _all and "Private Person" not in _all and "p@example.org" not in _all)
_dd = _zf.read("data_dictionary.csv").decode("utf-8-sig")
check("data dictionary explains the columns", "sus_score" in _dd and "prev_find_min" in _dd and "strains_count" in _dd)
_hdr = _resp.splitlines()[0].split(",")
check("every question has a column in the export", all(q["key"] in _hdr for q in _fs.ALL_QUESTIONS))
# spreadsheet safety: a comment that starts with a formula sign is neutralised
cq.post("/feedback", data={"rating": "4", "ease": "ok", "consent_research": "1", "best_thing": "=HYPERLINK(\"http://x.example\",\"a\")"})
_f = _z9.ZipFile(io.BytesIO(cq.get("/feedback/export").data)).read("responses.csv").decode("utf-8-sig")
check("export neutralises text that starts like a spreadsheet formula", "'=HYPERLINK" in _f and ",=HYPERLINK" not in _f)

# only admins can download; another lab never sees these responses
_cu = app.test_client()
_cu.post("/login", data=dict(username="labuser", password="labuser-pass-123"))
check("a regular user cannot download the research export", _cu.get("/feedback/export").status_code == 403)
check("a regular user can still open the summary", _cu.get("/feedback/summary").status_code == 200)
_cb = app.test_client()
_cb.post("/login", data=dict(username="adminB", password="adminB-pass-123"))
check("another lab's summary shows none of these responses", b"I can find any tube" not in _cb.get("/feedback/summary").data)
check("another lab's export contains none of these responses",
      "I can find any tube" not in _z9.ZipFile(io.BytesIO(_cb.get("/feedback/export?all=1").data)).read("responses.csv").decode("utf-8-sig"))
check("old style two field feedback still works", b"Thanks" in cq.post("/feedback", data={"rating": "2", "ease": "difficult"}, follow_redirects=True).data)

# --- plain statements about where data lives and who can see it ------------------
_ab = app.test_client().get("/about").data.decode()
check("About says where data is stored, including the cloud case",
      "Where it is stored" in _ab and "rented or cloud server" in _ab and "in the care of its provider" in _ab)
check("About says the authors never receive the data", "never receive your data" in _ab)
check("About says the person managing the server can open the files", "can also open the files directly" in _ab)
check("About says contact messages go to the lab's admins, not the authors", "not to the authors of the software" in _ab)
_su = (_Path(app.root_path) / "templates" / "setup.html").read_text()
check("the setup page says where the data is stored", "stored on the computer or server" in _su)
_ct = app.test_client().get("/contact").data.decode()
check("Contact page says messages go to this installation's administrator", "not to the authors of the software" in _ct)
check("the footer says the data is stored on the computer or server running the installation",
      "stored on the computer or server that runs this installation" in _ab)
check("README explains where the data lives", "**Where the data lives.**" in _readme and "rented or cloud server" in _readme)

# no stray double hyphens or em dashes anywhere in the visible text of the main pages
cw = app.test_client()
cw.post("/login", data=dict(username="adminA", password="adminA-pass-123"))
_bad = []
_own = db().execute("SELECT collection_id FROM organisms WHERE tenant_id=(SELECT tenant_id FROM users WHERE username='adminA') LIMIT 1").fetchone()
_cid = _own["collection_id"] if _own else "MDL-SACCER-001"
for _u in ["/", "/catalog", "/species/" + _cid, "/species/new", "/species/" + _cid + "/edit", "/storage", "/upload", "/connect",
           "/admin", "/admin/import", "/feedback", "/feedback/summary", "/account", "/about", "/contact", "/labels"]:
    _r = cw.get(_u)
    if _r.status_code != 200:
        _bad.append(_u + " (status " + str(_r.status_code) + ")")
        continue
    _vis = _re9.sub(r"<style.*?</style>|<script.*?</script>|<!--.*?-->", "", _r.data.decode(), flags=_re9.S)
    if _re9.search(r"(^|\s)--(\s|$)", _vis) or "\u2014" in _vis or "\u2013" in _vis:
        _bad.append(_u)
check("no ' -- ' or dash characters in the visible text of the main pages " + str(_bad), not _bad)
cw.get("/logout")

# --- DB sanity -----------------------------------------------------------------
conn = db()
print("plate_images rows:", conn.execute("SELECT COUNT(*) FROM plate_images").fetchone()[0])
# ---- 3D viewer scene (scene3d.py) ----
import json as _json, struct as _struct, tempfile as _tmp, pathlib as _pl
import scene3d as _sc3
import shape_config as _shp
_d = _pl.Path(_tmp.mkdtemp())
def _read_glb(path):
    raw = path.read_bytes()
    magic, ver, total = _struct.unpack("<4sII", raw[:12])
    jl, _ = _struct.unpack("<II", raw[12:20])
    return magic, ver, total, len(raw), _json.loads(raw[20:20 + jl])
_sizes = {}
for _name, _key in [("rhizoid", ("Bacillus", "subtilis")), ("dome", ("Saccharomyces", "cerevisiae")),
                    ("rosette", ("Lactobacillus", "brevis"))]:
    _cfg = dict(_shp._COLONY_DEFAULTS, **_shp.BUILTIN_SHAPES[_key]["colony"])
    _p = _d / (_name + ".glb")
    _sc3.write_colony_scene(_p, _cfg, 7)
    _m, _v, _t, _n, _g = _read_glb(_p)
    check(f"3D scene ({_name}): valid GLB header and length", _m == b"glTF" and _v == 2 and _t == _n)
    check(f"3D scene ({_name}): dish, agar and colonies are separate parts",
          [x["name"] for x in _g["materials"]] == ["agar", "colonies", "glass"])
    _ys = _g["accessors"][0]["max"][1] - _g["accessors"][0]["min"][1]
    check(f"3D scene ({_name}): plate lies flat (taller sideways than up)", _ys < 20)
_p1 = _d / "a.glb"; _p2 = _d / "b.glb"
_cfg = dict(_shp._COLONY_DEFAULTS)
_sc3.write_colony_scene(_p1, _cfg, 5); _sc3.write_colony_scene(_p2, _cfg, 5)
check("3D scene: same seed gives the same model", _p1.read_bytes() == _p2.read_bytes())
_sc3.write_colony_scene(_p2, _cfg, 6)
check("3D scene: different seed gives a different layout", _p1.read_bytes() != _p2.read_bytes())

# =============================================================================
# v4.7 fixes (see CHANGELOG): revival logic, history, attribution, uploads,
# image analysis, 3D models, labels, chat, exports
# =============================================================================
import csv, tempfile
from datetime import date as _d47, timedelta as _td47
from app import (org_revival_status, revival_badge as _rb, compute_revival_status as _crs,
                 tenant_id as _tid_fn)
c.get("/logout")
c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)


def _org(cid):
    cn = db()
    row = cn.execute("SELECT * FROM organisms WHERE tenant_id=? AND collection_id=?", (tid_a, cid)).fetchone()
    cn.close()
    return row


# -- revival: a strain added with no dates counts from the date stored --------------
stored = (_d47.today() - _td47(days=100)).isoformat()
c.post("/species/new", data=dict(genus="Zygosaccharomyces", species="bailii", viability_category="periodic",
                                 revival_interval_days="30", storage_freezer="F1", storage_box="B9",
                                 date_stored=stored), follow_redirects=True)
zb = _org("MDL-ZYGBAI-001")
st = org_revival_status(zb)
check("revival: no revival yet counts from the date stored (overdue)",
      st["status"] == "overdue" and st["baseline_source"] == "date_stored")
c.post("/species/new", data=dict(genus="Zygosaccharomyces", species="rouxii", viability_category="periodic",
                                 revival_interval_days="30", storage_freezer="F1", storage_box="B9"),
       follow_redirects=True)
zr = _org("MDL-ZYGROU-001")
check("revival: no revival and no storage date is flagged, never 'not due'",
      org_revival_status(zr)["status"] == "nobaseline" and _rb(org_revival_status(zr))["color"] == "red")
check("revival: unreadable date is not treated as today",
      _crs("not-a-date", 30)["status"] == "nobaseline")

# -- revival: a failed check does not reset the clock --------------------------------
old = (_d47.today() - _td47(days=40)).isoformat()
c.post("/species/MDL-ZYGBAI-001/log-revival", data=dict(revival_date=old, growth_success="pass", performed_by="A"))
check("revival: a passing check 40 days ago with a 30-day interval is overdue",
      org_revival_status(_org("MDL-ZYGBAI-001"))["status"] == "overdue")
r = c.post("/species/MDL-ZYGBAI-001/log-revival", data=dict(revival_date=_d47.today().isoformat(),
                                                           growth_success="fail", performed_by="A"),
           follow_redirects=True)
zb = _org("MDL-ZYGBAI-001")
check("revival: a failed check keeps the strain red",
      org_revival_status(zb)["status"] == "failed" and _rb(org_revival_status(zb))["color"] == "red")
check("revival: a failed check does not move the last passing date", zb["last_revival_date"] == old)
check("revival: the flash says the check failed", b"FAILED" in r.data)
home = c.get("/").data
check("revival: dashboard lists the failed strain first-tier red", b"last revival failed" in home)
c.post("/species/MDL-ZYGBAI-001/log-revival", data=dict(revival_date=_d47.today().isoformat(),
                                                       revival_time="23:59", growth_success="pass"))
zb = _org("MDL-ZYGBAI-001")
check("revival: a later passing check clears the failure",
      zb["growth_success"] == "pass" and org_revival_status(zb)["status"] == "notDue")

# -- revival: back-dated and future entries -------------------------------------------
c.post("/species/MDL-ZYGBAI-001/log-revival", data=dict(revival_date="2020-01-01", growth_success="pass"))
check("revival: a back-dated entry never moves the due date backwards",
      _org("MDL-ZYGBAI-001")["last_revival_date"] == _d47.today().isoformat())
future = (_d47.today() + _td47(days=400)).isoformat()
before_n = db().execute("SELECT COUNT(*) FROM revival_log WHERE organism_id=?", (zb["organism_id"],)).fetchone()[0]
r = c.post("/species/MDL-ZYGBAI-001/log-revival", data=dict(revival_date=future, growth_success="pass"),
           follow_redirects=True)
after_n = db().execute("SELECT COUNT(*) FROM revival_log WHERE organism_id=?", (zb["organism_id"],)).fetchone()[0]
check("revival: a future date is refused", before_n == after_n and b"in the future" in r.data)
r = c.post("/species/MDL-ZYGBAI-001/log-revival", data=dict(revival_date=old, growth_success="maybe"),
           follow_redirects=True)
check("revival: an invalid result is a form message, not a server error",
      r.status_code == 200 and b"passed or failed" in r.data)

# -- revival: voiding the latest passing check recomputes ------------------------------
cn = db()
latest = cn.execute("SELECT log_id FROM revival_log WHERE organism_id=? AND revival_date=? AND growth_success='pass'",
                    (zb["organism_id"], _d47.today().isoformat())).fetchone()["log_id"]
cn.close()
c.post(f"/species/MDL-ZYGBAI-001/revival/{latest}/void", data=dict(reason="logged on wrong strain"))
zb = _org("MDL-ZYGBAI-001")
check("revival: voiding the latest pass brings back the failure it covered",
      zb["growth_success"] == "fail" and org_revival_status(zb)["status"] == "failed")
check("revival: voided entry is kept with its reason",
      db().execute("SELECT void_reason FROM revival_log WHERE log_id=?", (latest,)).fetchone()[0]
      == "logged on wrong strain")
check("revival: each entry records the signed-in account",
      db().execute("SELECT entered_by_user FROM revival_log WHERE organism_id=? LIMIT 1",
                   (zb["organism_id"],)).fetchone()[0] == "adminA")

# -- periodic with no interval shows on the dashboard ----------------------------------
c.post("/species/new", data=dict(genus="Pichia", species="membranifaciens", viability_category="periodic",
                                 storage_freezer="F1", storage_box="B9"), follow_redirects=True)
check("dashboard flags a periodic strain with no interval", b"periodic, but no interval set" in c.get("/").data)

# -- baseline date via the form, audited edits ------------------------------------------
c.post("/species/MDL-ZYGROU-001/edit", data=dict(viability_category="periodic", revival_interval_days="30",
                                                revival_baseline_date=_d47.today().isoformat(),
                                                storage_freezer="F1", storage_box="B9"),
       follow_redirects=True)
zr = _org("MDL-ZYGROU-001")
check("edit: a baseline revival date gives a due date", org_revival_status(zr)["status"] == "notDue")
aud = db().execute("SELECT detail FROM audit_log WHERE entity='organism' AND entity_id=? AND action='update'",
                   (zr["organism_id"],)).fetchone()
check("edit: changes are written to the audit log with old and new values",
      aud is not None and "revival_baseline_date" in aud["detail"])
check("edit: species page shows the change history", b"Change history for this record" in
      c.get("/species/MDL-ZYGROU-001").data)

# -- only an admin deletes a strain -----------------------------------------------------
c.get("/logout")
c.post("/login", data=dict(username="labuser", password="labuser-pass-123"), follow_redirects=True)
r = c.post("/species/MDL-ZYGROU-001/delete", data=dict(reason="x"))
check("a lab user cannot delete a strain", r.status_code == 403 and _org("MDL-ZYGROU-001") is not None)
c.get("/logout")
c.post("/login", data=dict(username="adminA", password="adminA-pass-123"), follow_redirects=True)

# -- uploads are checked by content ------------------------------------------------------
r = c.post("/species/MDL-ZYGROU-001/upload", data=dict(image=(io.BytesIO(b"not really a png"), "fake.png")),
           content_type="multipart/form-data", follow_redirects=True)
check("upload: a non-image with an image extension is refused", b"not readable images" in r.data)
from PIL import Image as _PILImage
_tb = io.BytesIO(); _PILImage.new("RGB", (40, 40), (200, 180, 120)).save(_tb, format="TIFF"); _tb.seek(0)
c.post("/species/MDL-ZYGROU-001/upload", data=dict(image=(_tb, "plate.tif")),
       content_type="multipart/form-data", follow_redirects=True)
fp = db().execute("SELECT file_path FROM plate_images WHERE organism_id=? ORDER BY image_id DESC",
                  (zr["organism_id"],)).fetchone()
check("upload: a TIFF is stored as PNG so browsers can show it", fp is not None and fp[0].endswith(".png"))
r = c.post("/species/MDL-ZYGROU-001/upload", data=dict(image=(io.BytesIO(PNG), "p.png"), captured_date=future),
           content_type="multipart/form-data", follow_redirects=True)
check("upload: a future capture date is refused", b"in the future" in r.data)

# -- image analysis on fixed scales ----------------------------------------------------
import plate_vision as _pv
import numpy as _np47
from pathlib import Path as _P47
_tmp = _P47(tempfile.mkdtemp())
yy, xx = _np47.mgrid[0:600, 0:800]
for sig in (0, 3, 6):
    b = _np47.full((600, 800, 3), (200, 190, 150), _np47.float64)
    b[((xx - 400) ** 2 + (yy - 300) ** 2) > 260 ** 2] = (60, 60, 60)
    b = _np47.clip(b + _np47.random.default_rng(0).normal(0, sig, b.shape), 0, 255).astype(_np47.uint8)
    _PILImage.fromarray(b).save(_tmp / "blank.png")
    m = _pv.analyse_plate(_tmp / "blank.png")["morphology"]
    check(f"image: a blank plate (noise sd {sig}) reads as no growth", m["coverage_pct"] < 0.5)
fx = _pv.analyse_plate("test_fixtures/handwritten_label_plate.jpg")
mf = fx["morphology"]
check("image: dish found and the analysis is centred on it",
      mf["dish_detected"] and abs(fx["circle"][0] - (fx["rgb"].shape[0] - 1) / 2) < 1
      and abs(fx["circle"][1] - (fx["rgb"].shape[0] - 1) / 2) < 1)
check("image: handwriting and print are excluded (coverage close to the 6 colonies alone)",
      mf["coverage_pct"] < 10 and mf["excluded_marks_pct"] > 5)
check("image: round colonies give 'entire' margins", mf["margin_desc"].startswith("entire"))
check("image: 'density' replaces the misleading 'elevation' wording", "density_desc" in mf)
for (x, y) in [(350, 250), (450, 330), (400, 400), (300, 320), (470, 220)]:
    th = _np47.arctan2(yy - y, xx - x)
lob = _np47.full((600, 800, 3), (200, 190, 150), _np47.float64)
lob[((xx - 400) ** 2 + (yy - 300) ** 2) > 260 ** 2] = (60, 60, 60)
for (x, y) in [(350, 250), (450, 330), (400, 400), (300, 320), (470, 220)]:
    th = _np47.arctan2(yy - y, xx - x)
    lob[((xx - x) ** 2 + (yy - y) ** 2) < (20 * (1 + 0.35 * _np47.sin(6 * th))) ** 2] = (240, 235, 220)
_PILImage.fromarray(lob.astype(_np47.uint8)).save(_tmp / "lobed.png")
check("image: lobed colonies still read as irregular",
      _pv.analyse_plate(_tmp / "lobed.png")["morphology"]["margin_desc"].startswith("irregular"))

# -- real laboratory photographs (v4.7.0 additions). Expected values are visual
# estimates from the photographs, used as guard rails, not as a benchmark. --------
_real = {k: _pv.analyse_plate(f"test_fixtures/{k}.jpg")["morphology"] for k in
         ("grid_small_dark_colonies", "translucent_colonies_marker_print",
          "translucent_colonies_held_in_glove", "wrinkled_confluent_growth")}
g = _real["grid_small_dark_colonies"]
check("photo: small dark colonies on a printed grid are found, the grid is not growth",
      5 <= g["coverage_pct"] <= 16 and g["excluded_marks_pct"] > 10)
check("photo: grid plate colonies read as entire (round)", g["margin_desc"].startswith("entire"))
w = _real["wrinkled_confluent_growth"]
check("photo: wrinkled growth covering most of the plate reads as dense or confluent",
      w["coverage_pct"] > 45 and ("dense" in w["density_desc"] or "confluent" in w["density_desc"]))
check("photo: wrinkled growth reads as irregular", w["margin_desc"].startswith("irregular"))
t = _real["translucent_colonies_marker_print"]
check("photo: marker and dot-matrix print are not counted as growth",
      t["coverage_pct"] < 16 and t["excluded_marks_pct"] > 8)
check("photo: a plain bench background is not flagged as uneven", not t["uneven_background"]
      and not g["uneven_background"])
check("photo: a plate held over a glove is flagged as an uneven background",
      _real["translucent_colonies_held_in_glove"]["uneven_background"])

# -- 3D: STL and viewer share one layout; realistic sizes; cells ----------------------
import scene3d as _s47, model_builder as _mb47, microbe3d as _m47, shape_config as _sh47
_cfgp = _sh47.BUILTIN_SHAPES[("Pediococcus", "damnosus")]["colony"]
lay, _ = _s47.colony_layout(_cfgp, 11)
hm = _s47.colony_height_map(_cfgp, 11, n=512)
px = 90.0 / 512
ok = True
for col in lay:
    j = int(round(col["x"] / px + 255.5)); i = int(round(-col["y"] / px + 255.5))
    ok &= hm[i, j] > 0
check("3D: every colony in the viewer layout is raised in the STL height map", ok and len(lay) > 20)
sizes = [2 * col["r"] for col in lay if col["kind"] == "dome"]
check("3D: P. damnosus colonies are pinpoint (under 2 mm across)", sizes and max(sizes) <= 1.7)
w, note = _mb47.build_colony_files(_cfgp, 11, _tmp / "c.stl", _tmp / "c.glb")
check("3D: colony STL is watertight (computed, not assumed)", w is True)
mesh, scale = _m47.cell_assembly(_sh47.BUILTIN_SHAPES[("Pediococcus", "damnosus")]["cell"])
check("3D: Pediococcus is drawn as a tetrad (4 cells)", len(mesh.vertices) == 4 * len(_m47.sphere(1, 20, 40).vertices))
check("3D: cell model is watertight", mesh.is_watertight())
yeast, _ = _m47.cell_assembly(_sh47.BUILTIN_SHAPES[("Saccharomyces", "cerevisiae")]["cell"])
check("3D: yeast cell has a bud", len(yeast.vertices) > len(_m47.ellipsoid(1, 1, 1, 28, 56).vertices))
lb = dict(genus="Levilactobacillus", species="brevis", gram_stain="negative", shape_config=None)
check("3D: cell colour follows the strain's recorded Gram result",
      tuple(_sh47.resolve_shape_config(lb)["cell"]["color"]) == _sh47.GRAM_NEGATIVE_COLOR)
c.post("/species/MDL-LEVBRE-004/generate", follow_redirects=True)
mrows = db().execute("SELECT is_watertight, notes FROM models WHERE organism_id=? AND source='procedural'",
                     (_org("MDL-LEVBRE-004")["organism_id"],)).fetchall()
check("3D: generated models record a computed watertight flag and size note",
      len(mrows) == 2 and all(r["is_watertight"] == 1 for r in mrows) and any("µm" in (r["notes"] or "") for r in mrows))

# -- 3D: photo relief built on the dish, not the frame ----------------------------------
with open("test_fixtures/handwritten_label_plate.jpg", "rb") as fh:
    c.post("/species/MDL-ZYGROU-001/upload", data=dict(image=(io.BytesIO(fh.read()), "plate.jpg")),
           content_type="multipart/form-data", follow_redirects=True)
pid = db().execute("SELECT image_id FROM plate_images WHERE organism_id=? AND file_path LIKE '%plate.jpg' "
                   "ORDER BY image_id DESC", (zr["organism_id"],)).fetchone()[0]
r = c.post(f"/species/MDL-ZYGROU-001/rebuild/{pid}", follow_redirects=True)
check("3D: photo relief builds from a real photo", b"Built the 3D relief" in r.data)
morph = json.loads(db().execute("SELECT cv_morphology_json FROM plate_images WHERE image_id=?", (pid,)).fetchone()[0])
check("3D: the stored plate description uses the new analysis", "density_desc" in morph and morph["coverage_pct"] < 10)
check("3D: the page says relief height is drawn, not measured", b"not a measured profile" in r.data)

# -- QR labels carry the lab -------------------------------------------------------------
cid0 = "MDL-ZYGROU-001"
check("label link opens the strain for its own lab",
      c.get(f"/s/{tid_a}/{cid0}").status_code == 302)
check("label link from another lab is not found", c.get(f"/s/{tid_b}/{cid0}").status_code == 404)
import app as _appmod
_real_lan = _appmod.get_lan_ip
_appmod.get_lan_ip = lambda: "192.168.1.50"
r = c.get(f"/labels/print?cid={cid0}")
check("labels printed from localhost are not left without QR codes", b"QR codes are left off" not in r.data)
check_qr("...the QR points at this computer's network address", b"http://192.168.1.50" in r.data and b"<svg" in r.data)
_appmod.get_lan_ip = lambda: "127.0.0.1"
r = c.get(f"/labels/print?cid={cid0}")
check("with no network at all, labels leave the QR off and say why", b"QR codes are left off" in r.data)
_appmod.get_lan_ip = _real_lan

# -- CSP header ---------------------------------------------------------------------------
check("pages send a same-origin Content-Security-Policy",
      "default-src 'self'" in c.get("/catalog").headers.get("Content-Security-Policy", ""))

# -- counts and the command-line export ---------------------------------------------------
rows = {r["collection_id"]: r for r in db().execute(
    """SELECT o.collection_id,
              (SELECT COUNT(*) FROM models m WHERE m.organism_id=o.organism_id AND m.is_print_ready=1) AS pr
       FROM organisms o WHERE o.tenant_id=?""", (tid_a,))}
import app as _app47
with app.test_request_context():
    from flask import session as _fs
    _row = db().execute("SELECT user_id, session_stamp FROM users WHERE username='adminA'").fetchone()
    _fs["user_id"], _fs["stamp"] = _row[0], _row[1] or ""      # as a real sign-in stores them
    listed = {r["collection_id"]: r["print_ready"] for r in _app47.get_organisms()}
check("catalog print-ready counts are not inflated by plate photos",
      all((listed[k] or 0) == rows[k]["pr"] for k in listed))
import export_catalog as _ec, sqlite3 as _sq
_cn = _sq.connect(_app47.DB_PATH); _cn.row_factory = _sq.Row
n = _ec.export_lab(_cn, tid_a, _tmp / "exp")
_cn.close()
with open(_tmp / "exp" / "organisms.csv", encoding="utf-8-sig") as fh:
    tids = {row["tenant_id"] for row in csv.DictReader(fh)}
with open(_tmp / "exp" / "feedback.csv", encoding="utf-8-sig") as fh:
    fb_cols = fh.readline()
check("CLI export holds one lab only", tids == {str(tid_a)} and n > 0)
check("CLI export leaves out feedback names and emails", "contact_email" not in fb_cols and ",name," not in f",{fb_cols.strip()},")
check("seed panel uses the current name Levilactobacillus brevis", _org("MDL-LEVBRE-004") is not None)

print("organisms rows   :", conn.execute("SELECT COUNT(*) FROM organisms").fetchone()[0])
print("tenants rows     :", conn.execute("SELECT COUNT(*) FROM tenants").fetchone()[0])
conn.close()

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(" -", f)
    raise SystemExit(1)
else:
    print("All checks passed." + (f" ({len(skipped)} skipped: optional segno package not installed)" if skipped else ""))
