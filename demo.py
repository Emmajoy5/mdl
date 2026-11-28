"""Public demo mode for the Microbial Digital Library.

Turn it on with MDL_DEMO=1. The installation then:
  - holds one lab, "Demo Brewery Lab", with the example strain panel, storage
    positions, a Gram stain entry, three real plate photographs with their 3D
    reliefs, and procedural models, and one shared demo account whose username
    and password are shown on the sign-in page;
  - wipes everything and rebuilds it at start-up and every MDL_DEMO_RESET_HOURS
    hours (default 6), so nothing anyone enters survives;
  - turns off what a shared public account must not do: sign-up, account and
    password changes and reset links, managing users and join codes, renaming the lab,
    and the contact form and messages;
  - limits uploads to 8 MB per request and shows a banner on every page.

It is meant for a free container host (see deploy/huggingface/README.md). Never
turn it on for an installation that holds real data: the reset deletes it.
"""
import os
import shutil
import threading
import time
from datetime import date, timedelta
from pathlib import Path

DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo-lab-2026"
DEMO_LAB = "Demo Brewery Lab"

BLOCKED_ENDPOINTS = {
    "signup", "account", "contact",
    "admin_add_user", "admin_toggle_user", "admin_reset_password",
    "admin_regenerate_join_code", "admin_join_request", "admin_rename_tenant",
    "admin_photo_storage",
    "admin_message_action", "setup",
    "pwreset.forgot", "pwreset.reset", "pwreset.admin_link", "pwreset.admin_dismiss", "pwreset.account_email",
}

# example plates shipped in test_fixtures/, attached to strains of the panel
DEMO_PLATES = [
    ("MDL-BACSUB-007", "wrinkled_confluent_growth.jpg", "test plate (wrinkled growth)", "aerobic"),
    ("MDL-SACCER-001", "translucent_colonies_marker_print.jpg", "test plate (translucent colonies)", "aerobic"),
    ("MDL-LEVBRE-004", "grid_small_dark_colonies.jpg", "test plate on a counting grid", "anaerobic"),
]

_lock = threading.Lock()


def enabled():
    return os.environ.get("MDL_DEMO", "").strip().lower() in ("1", "true", "yes", "on")


def reset_hours():
    try:
        return max(0.25, float(os.environ.get("MDL_DEMO_RESET_HOURS", "6")))
    except ValueError:
        return 6.0


def _wipe(app_module):
    """Delete the database and every stored photo and model."""
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(app_module.DB_PATH) + suffix)
        if p.exists():
            p.unlink()
    for folder in (app_module.UPLOAD_DIR, app_module.MODEL_DIR):
        for p in folder.iterdir():
            if p.name == ".gitkeep":
                continue
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()


def build(app_module):
    """Wipe and rebuild the demo lab. Safe to call while the app is serving:
    requests wait on the lock only during the rebuild itself."""
    a = app_module
    with _lock:
        _wipe(a)
        for k in ("ADMIN_USERNAME", "ADMIN_PASSWORD"):   # the demo makes its own account
            os.environ.pop(k, None)
        a.init_db_if_needed()
        conn = a.db()
        a.create_lab(conn, DEMO_LAB, DEMO_USERNAME, DEMO_PASSWORD, "Demo user", include_examples=True)
        tid = conn.execute("SELECT tenant_id FROM tenants").fetchone()[0]
        positions = {
            "MDL-SACCER-001": ("Freezer 1", "Rack A", "Box 1", "A1"),
            "MDL-SACPAS-002": ("Freezer 1", "Rack A", "Box 1", "A2"),
            "MDL-BRESPP-003": ("Freezer 1", "Rack A", "Box 1", "A3"),
            "MDL-LEVBRE-004": ("Freezer 1", "Rack B", "Box 2", "B1"),
            "MDL-PEDDAM-005": ("Freezer 1", "Rack B", "Box 2", "B2"),
            "MDL-ACESPP-006": ("Freezer 1", "Rack B", "Box 2", "B3"),
            "MDL-BACSUB-007": ("Freezer 1", "Rack B", "Box 2", "C1"),
        }
        stored = (date.today() - timedelta(days=50)).isoformat()
        for cid, (f, rk, b, p) in positions.items():
            conn.execute("""UPDATE organisms SET storage_freezer=?, storage_rack=?, storage_box=?,
                            storage_position=?, storage_format='Glycerol stock', storage_temp_c='-80',
                            date_stored=? WHERE tenant_id=? AND collection_id=?""",
                         (f, rk, b, p, stored, tid, cid))
        ped = conn.execute("SELECT * FROM organisms WHERE tenant_id=? AND collection_id='MDL-PEDDAM-005'",
                           (tid,)).fetchone()
        if ped is not None:
            conn.execute("""INSERT INTO gram_stain_images (organism_id, tenant_id, result, magnification,
                            stained_by, stain_date, stain_time, entered_by_user)
                            VALUES (?, ?, 'positive', '1000x oil immersion', 'Operator A', ?, '10:20', ?)""",
                         (ped["organism_id"], tid, date.today().isoformat(), DEMO_USERNAME))
        conn.commit()
        fixtures = a.BASE / "test_fixtures"
        for cid, fname, media, atmosphere in DEMO_PLATES:
            src = fixtures / fname
            org = conn.execute("SELECT * FROM organisms WHERE tenant_id=? AND collection_id=?",
                               (tid, cid)).fetchone()
            if org is None or not src.exists():
                continue
            name = f"{cid}-demo-{fname}"
            shutil.copyfile(src, a.UPLOAD_DIR / name)
            cur = conn.execute("""INSERT INTO plate_images (organism_id, tenant_id, file_path, media, atmosphere,
                                  captured_date, uploaded_by, upload_timestamp, entered_by_user)
                                  VALUES (?, ?, ?, ?, ?, ?, 'Demo', datetime('now'), ?)""",
                               (org["organism_id"], tid, f"uploads/{name}", media, atmosphere,
                                date.today().isoformat(), DEMO_USERNAME))
            conn.commit()
            img = conn.execute("SELECT * FROM plate_images WHERE image_id=?", (cur.lastrowid,)).fetchone()
            try:
                a.build_photo_relief(img, org)
            except Exception:              # a demo without one relief is still a demo
                a.app.logger.exception("demo relief failed")
        for org in conn.execute("SELECT * FROM organisms WHERE tenant_id=? AND collection_id IN "
                                "('MDL-PEDDAM-005','MDL-BACSUB-007','MDL-SACCER-001')", (tid,)).fetchall():
            a.generate_models_for(org)
        conn.close()


def install(app_module):
    """Hook demo behaviour into the Flask app (called from app.py when enabled)."""
    from flask import flash, redirect, request, url_for

    flask_app = app_module.app
    flask_app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    flask_app.jinja_env.globals["demo_mode"] = True
    flask_app.jinja_env.globals["demo_credentials"] = (DEMO_USERNAME, DEMO_PASSWORD)
    flask_app.jinja_env.globals["demo_reset_hours"] = reset_hours()
    flask_app.jinja_env.globals["demo_source_url"] = os.environ.get(
        "MDL_SOURCE_URL", "https://github.com/Emmajoy5/mdl")

    @flask_app.before_request
    def _demo_guard():
        if request.endpoint in BLOCKED_ENDPOINTS:
            if request.endpoint == "signup" and request.method == "GET":
                flash("Sign-up is off in the public demo. Sign in with the demo account below.")
                return redirect(url_for("login"))
            flash("That isn't available in the public demo.")
            return redirect(url_for("home") if request.endpoint != "contact" else url_for("about"))
        if _lock.locked():
            return ("The demo is being reset. Try again in a minute.", 503)
        return None

    def _loop():
        while True:
            time.sleep(reset_hours() * 3600)
            try:
                build(app_module)
            except Exception:
                flask_app.logger.exception("demo reset failed")

    threading.Thread(target=_loop, name="demo-reset", daemon=True).start()
