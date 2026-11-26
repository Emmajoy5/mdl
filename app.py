"""Microbial Digital Library -- self-hosted lab web app.

Run locally in the lab:

    pip install -r requirements.txt
    python app.py            # then open http://localhost:5000

For other lab machines on the same network, open http://<this-machine-ip>:5000
(the app binds 0.0.0.0 by default). Scan the QR code at /connect to open it
on a phone with no install needed.

No SharePoint, no Power BI, no cloud -- everything stays in this folder
(SQLite DB + uploads/ + models/). Nothing is sent anywhere else.
"""
import csv
import hashlib
import io
import json
import os
import re
import secrets
import socket
import sys
import sqlite3
import threading
import zipfile
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from functools import wraps
from pathlib import Path

from flask import (Flask, Response, abort, flash, g, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import feedback_survey as fs
import importer
import microbe3d as m3
import scene3d
import model_builder
try:
    import segno  # QR codes for printed labels
except ImportError:  # labels still print, just without a QR
    segno = None
import plate_vision as pv
import shape_config as shapes
import photo_store
from shape_config import resolve_shape_config

BASE = Path(__file__).resolve().parent

# Load settings from a .env file next to this script, if one exists, which is
# easier than exporting shell variables every time the app starts. Entirely
# optional: without python-dotenv, or without a .env file, the app uses
# whatever is already in the environment.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
except ImportError:
    pass
# Where the app keeps everything it writes. Defaults to the app folder (the
# original behavior); set APP_DATA_DIR to put the database, uploaded photos,
# 3D models, and the session key under one separate directory instead -- what
# the Dockerfile does, so a single mounted volume is the whole backup surface.
DATA_DIR = Path(os.environ.get("APP_DATA_DIR") or BASE)
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "microbe_catalog.db"
UPLOAD_DIR = DATA_DIR / "uploads"
MODEL_DIR = DATA_DIR / "models"
UPLOAD_DIR.mkdir(exist_ok=True)
MODEL_DIR.mkdir(exist_ok=True)

ALLOWED_IMG = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff"}

ROLE_CHOICES = ["fermentation", "souring", "spoilage", "conditioning", "flavor", "other"]
VIABILITY_CHOICES = ["periodic", "stable", "longTerm", "unknown"]
COLLECTION_TYPE_CHOICES = ["library", "validation", "reference", "other"]
GRAM_CHOICES = ["positive", "negative", "variable", "not_done"]

# Evaluation questionnaire (see /feedback). The questions, checking and scoring
# live in feedback_survey.py.
EASE_CHOICES = fs.EASE_CHOICES
FEEDBACK_FEATURE_CHOICES = fs.FEATURE_CHOICES

# Login / accounts (see README "Accounts & access"). Two roles: 'admin' can
# do everything a regular 'user' can, plus the things that affect every
# user's data or the tool's own configuration -- bulk export and managing
# the Labs/Users lists. Regular users can fully add, edit, and delete the
# strain/sample records themselves; that's not an admin-only action.
USER_ROLE_CHOICES = ["admin", "user"]
SAMPLE_SOURCE_CHOICES = [("lab", "Source lab"), ("online", "Online reference database")]

app = Flask(__name__)


def _get_or_create_secret_key():
    """Persist the Flask session-signing key across restarts in a local
    dotfile, so logging in isn't undone every time the app is restarted.
    Generated once, on first run; never committed (see .gitignore-style
    advice in the README) since it's effectively a credential."""
    key_path = DATA_DIR / ".secret_key"
    if key_path.exists():
        return key_path.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    key_path.write_text(key, encoding="utf-8")
    return key


app.config["SECRET_KEY"] = _get_or_create_secret_key()
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB upload cap
app.jinja_env.filters["fromjson"] = lambda s: json.loads(s) if s else {}
app.jinja_env.globals["revival_badge"] = lambda status: revival_badge(status)

# Session cookie hardening. SESSION_COOKIE_SECURE is opt-in via an env var
# rather than always-on: a lab trying this out over plain http://<lan-ip>:5000
# (see the module docstring) would otherwise get silently logged out, since a
# Secure cookie is never sent back over a non-HTTPS connection. Turn this on
# once the app sits behind HTTPS (see README "Deploying for real use").
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FORCE_HTTPS_COOKIES", "").lower() in ("1", "true", "yes")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=14)


# --------------------------------------------------------------------------- #
# CSRF protection
# --------------------------------------------------------------------------- #
# Every POST route in this app is a plain HTML form submission (no JSON/AJAX
# API calls), so one site-wide check covers all of them: a hidden csrf_token
# field (emitted by the csrf_field() template helper) must match the token
# stashed in the signed session cookie when the form was rendered. Without
# this, a malicious page could silently submit any of this app's forms (add a
# user, delete a strain, suspend a lab...) using a logged-in visitor's own
# session, since cookies are sent automatically by the browser regardless of
# which site the request came from.
def _csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_hex(32)
        session["csrf_token"] = token
    return token


def pending_join_count():
    """Number of people waiting for the signed-in admin to approve them (nav badge)."""
    u = current_user()
    if u is None or u["role"] != "admin" or u["tenant_id"] is None:
        return 0
    conn = db()
    n = conn.execute("SELECT COUNT(*) n FROM users WHERE tenant_id=? AND pending_approval=1",
                     (u["tenant_id"],)).fetchone()["n"]
    conn.close()
    return n


def csrf_field():
    from markupsafe import Markup, escape
    return Markup(f'<input type="hidden" name="csrf_token" value="{escape(_csrf_token())}">')


app.jinja_env.globals["csrf_token"] = _csrf_token
app.jinja_env.globals["csrf_field"] = csrf_field
app.jinja_env.globals["pending_join_count"] = pending_join_count


@app.before_request
def _enforce_csrf():
    if request.method != "POST":
        return
    if app.config.get("TESTING"):
        # test_app.py exercises routes directly via Flask's test client without
        # a browser rendering+resubmitting each form; CSRF protection itself is
        # covered by a dedicated check there instead (see "CSRF" section).
        return
    expected = session.get("csrf_token")
    sent = request.form.get("csrf_token")
    if not expected or not sent or not secrets.compare_digest(expected, sent):
        abort(400, description="This form's session token was missing or stale. "
                               "Go back, reload the page, and try again.")


def _error_page(code, heading, message):
    try:
        return render_template("error.html", heading=heading, message=message), code
    except Exception:  # the error page itself must never take the app down
        return f"{heading}: {message}", code


@app.errorhandler(400)
def _handle_bad_request(err):
    return _error_page(400, "Request not accepted", getattr(err, "description", None) or "Bad request.")


@app.errorhandler(403)
def _handle_forbidden(err):
    return _error_page(403, "Not allowed", "You don't have permission to do that. "
                       "If you think you should, ask your lab's admin.")


@app.errorhandler(404)
def _handle_not_found(err):
    # Deliberately the same message whether a record doesn't exist or belongs
    # to another lab, so nobody can probe for other labs' strain IDs.
    return _error_page(404, "Not found", "That page or record wasn't found.")


@app.errorhandler(413)
def _handle_too_large(err):
    mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return _error_page(413, "That upload is too large",
                       f"Uploads are limited to {mb} MB in total. Try fewer or smaller photos at a time.")


@app.errorhandler(500)
def _handle_server_error(err):
    app.logger.exception("Unhandled error")
    return _error_page(500, "Something went wrong", "That didn't work, and it's not your fault. "
                       "If you were saving something, it was not saved, so check the record before typing it "
                       "again. If it keeps happening tell your admin.")


CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; connect-src 'self' data: blob:; worker-src 'self' blob:; "
       "font-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'self'; "
       "form-action 'self'")


@app.after_request
def _security_headers(resp):
    """Cheap, broadly-safe browser protections, including a Content-Security-Policy
    that keeps every page to this server's own origin. Inline scripts and styles
    are still allowed ('unsafe-inline'), because the templates use them; moving
    them to files would let that be dropped too."""
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if app.config.get("SESSION_COOKIE_SECURE"):
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    # Pages show one lab's private data: keep proxies and shared browsers from caching them.
    if request.endpoint not in ("static", "manifest", "service_worker") and "Cache-Control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store" if resp.mimetype == "text/html" else "private, max-age=300"
    return resp


# --------------------------------------------------------------------------- #
# Login rate limiting / account lockout
# --------------------------------------------------------------------------- #
# Two layers, deliberately simple rather than pulling in a dependency (like
# Flask-Limiter) or an external store (like Redis) for a lab-scale tool:
#   1. Per-account lockout, persisted on the users row (failed_login_count /
#      locked_until) -- survives a restart, the main defense against a
#      focused brute-force on one username.
#   2. A per-IP sliding-window limiter, in-process memory only, as a second
#      layer against one source hammering many different usernames. This
#      resets on restart and isn't shared across multiple app processes --
#      fine for the single-process deployment this app targets (see README
#      "Deploying for real use"); a multi-instance deployment would need a
#      shared store instead.
LOGIN_LOCKOUT_THRESHOLD = 6
LOGIN_LOCKOUT_MINUTES = 15
IP_RATE_LIMIT_MAX = 20
IP_RATE_LIMIT_WINDOW_SECONDS = 300

_rate_limit_lock = threading.Lock()
_rate_limit_buckets = {}  # key -> list[datetime] of recent attempts


def _ip_rate_limited(key):
    """True if `key` (e.g. "login:1.2.3.4") has made too many attempts in
    the current window; otherwise records this attempt and returns False."""
    now = datetime.now()
    cutoff = now - timedelta(seconds=IP_RATE_LIMIT_WINDOW_SECONDS)
    with _rate_limit_lock:
        attempts = [t for t in _rate_limit_buckets.get(key, []) if t > cutoff]
        if len(attempts) >= IP_RATE_LIMIT_MAX:
            _rate_limit_buckets[key] = attempts
            return True
        attempts.append(now)
        _rate_limit_buckets[key] = attempts
        return False


def _client_ip():
    # Trusts X-Forwarded-For only when the app is actually behind a reverse
    # proxy that sets it (see README) -- otherwise this is spoofable, since
    # the client controls any header they send directly.
    if os.environ.get("TRUST_PROXY_HEADERS", "").lower() in ("1", "true", "yes"):
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _account_locked(user):
    if not user["locked_until"]:
        return False
    try:
        return datetime.fromisoformat(user["locked_until"]) > datetime.now()
    except ValueError:
        return False


def _register_failed_login(conn, user):
    count = (user["failed_login_count"] or 0) + 1
    locked_until = None
    if count >= LOGIN_LOCKOUT_THRESHOLD:
        locked_until = (datetime.now() + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)).isoformat(timespec="seconds")
    conn.execute("UPDATE users SET failed_login_count=?, locked_until=? WHERE user_id=?",
                (count, locked_until, user["user_id"]))
    conn.commit()
    return locked_until is not None


def _register_successful_login(conn, user):
    conn.execute("UPDATE users SET failed_login_count=0, locked_until=NULL WHERE user_id=?",
                (user["user_id"],))
    conn.commit()


# --------------------------------------------------------------------------- #
# Database helpers + in-place schema migration
# --------------------------------------------------------------------------- #
BUSY_WAIT_MS = int(os.environ.get("DB_BUSY_WAIT_MS", "30000"))


def db():
    # SQLite allows one writer at a time. Without a wait, a save that lands
    # while someone else is writing (a spreadsheet import, say) fails at once
    # and the person loses what they typed, so we wait our turn instead.
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_WAIT_MS / 1000.0)
    conn.execute(f"PRAGMA busy_timeout={BUSY_WAIT_MS}")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def begin_write(conn):
    """Take the database's write lock now, before reading the numbers a new
    collection ID is worked out from.

    Two people saving at the same moment used to be given the same ID, and
    whichever saved second lost the isolate. Starting the transaction before
    the read means the second save waits, then sees the first one's ID and
    takes the next. Harmless if the connection is already in a transaction.
    """
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def set_write_mode():
    """Put the database in write-ahead logging mode, once per install.

    In the older rollback mode a page being read had to wait for a save to
    finish and the other way round. With write-ahead logging, reading and
    writing go on at the same time, which roughly doubled the work done per
    second in a test with several people saving and reading at once. The
    setting is kept in the database file, so this is a no-op after the first
    run. A database on a network share can't use it, hence the quiet failure.
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=BUSY_WAIT_MS / 1000.0)
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        conn.close()
        return mode
    except sqlite3.Error as e:
        app.logger.warning("Could not turn on write-ahead logging: %s", e)
        return None


# Multi-lab accounts (see CHANGELOG v4.0): each signed-up lab is a tenant
# with its own walled-off data. Every data table below carries a tenant_id
# so a query that forgets to filter by it fails safe (returns nothing)
# rather than leaking another lab's records -- but every route is still
# expected to filter explicitly; this column is the last line of defense,
# not a substitute for that. Nullable at the column level (SQLite can't
# add a NOT NULL column with no default to a populated table), and backfilled
# for any pre-existing single-tenant data by _migrate_legacy_single_tenant_data().
NEW_ORGANISM_COLUMNS = {
    "collection_type": "TEXT NOT NULL DEFAULT 'library'",
    "gram_stain": "TEXT", "media": "TEXT", "sample_source": "TEXT",
    "oxygen_requirement": "TEXT", "optimal_temp_c": "TEXT",
    "morphology_notes": "TEXT", "storage_location": "TEXT",
    "storage_format": "TEXT", "storage_temp_c": "TEXT",
    "storage_freezer": "TEXT", "storage_rack": "TEXT",
    "storage_box": "TEXT", "storage_position": "TEXT",
    "date_stored": "TEXT", "last_verified_date": "TEXT",
    "shape_config": "TEXT",
    "cell_shape": "TEXT", "cell_arrangement": "TEXT",
    "added_by": "TEXT",
    "viability_category": "TEXT NOT NULL DEFAULT 'unknown'",
    "revival_interval_days": "INTEGER",
    "last_revival_date": "TEXT",
    "growth_success": "TEXT",
    "contamination_notes": "TEXT",
    "colony_count": "INTEGER",
    "last_revival_time": "TEXT",
    # Structured sample provenance (see CHANGELOG v3.0): sample_source_type
    # is 'lab' or 'online'; sample_source_lab_id points at a row in the
    # admin-managed source-labs table when type='lab'. The existing free-text
    # sample_source column is reused to hold the online reference's name
    # when type='online', instead of adding a redundant new column.
    "sample_source_type": "TEXT", "sample_source_lab_id": "INTEGER",
    "tenant_id": "INTEGER REFERENCES tenants(tenant_id)",
}

# schema.sql's original organisms table has collection_id TEXT NOT NULL
# UNIQUE -- a platform-wide uniqueness constraint. Collection IDs (see
# next_collection_id()) are only meant to be unique within one lab, so two
# different labs landing on the same code (both registering the same
# genus/species as their first strain, say) would otherwise collide on
# INSERT. Rebuilt under UNIQUE(tenant_id, collection_id) the same way
# accessions/labs/users were (see _migrate_organisms_table).
ORGANISMS_TABLE = """
CREATE TABLE IF NOT EXISTS organisms (
    organism_id        INTEGER PRIMARY KEY,
    collection_id      TEXT    NOT NULL,
    tenant_id           INTEGER REFERENCES tenants(tenant_id),
    genus              TEXT    NOT NULL,
    species            TEXT    NOT NULL,
    strain             TEXT,
    full_name          TEXT    NOT NULL,
    ncbi_taxonomy_id   INTEGER,
    target_status      TEXT    NOT NULL DEFAULT 'initial'
                       CHECK (target_status IN ('initial','candidate','curated','retired')),
    collection_type    TEXT    NOT NULL DEFAULT 'library'
                       CHECK (collection_type IN ('library','validation','reference','other')),
    notes              TEXT,
    gram_stain         TEXT,
    media              TEXT,
    sample_source      TEXT,
    oxygen_requirement TEXT,
    optimal_temp_c     TEXT,
    morphology_notes   TEXT,
    storage_location   TEXT,
    storage_format     TEXT,
    storage_temp_c     TEXT,
    storage_freezer    TEXT,
    storage_rack       TEXT,
    storage_box        TEXT,
    storage_position   TEXT,
    date_stored        TEXT,
    last_verified_date TEXT,
    shape_config       TEXT,
    cell_shape         TEXT,
    cell_arrangement   TEXT,
    added_by           TEXT,
    viability_category  TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (viability_category IN ('periodic','stable','longTerm','unknown')),
    revival_interval_days INTEGER,
    last_revival_date     TEXT,
    last_revival_time     TEXT,
    revival_baseline_date TEXT,
    growth_success         TEXT CHECK (growth_success IN ('pass','fail')),
    contamination_notes    TEXT,
    colony_count            INTEGER,
    sample_source_type    TEXT,
    sample_source_lab_id  INTEGER,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (tenant_id, collection_id)
);
"""


def _migrate_organisms_table(conn):
    """Upgrading from v3.0 (or an earlier v4.0 build): the organisms table
    had a bare UNIQUE on collection_id, which would stop two different
    tenants from both landing on the same code (e.g. both registering
    Saccharomyces cerevisiae as their first strain). Rebuilds under the
    current UNIQUE(tenant_id, collection_id) definition. A no-op once
    already in that shape."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='organisms'").fetchone()
    sql = row["sql"] or "" if row else ""
    if row and "UNIQUE(tenant_id, collection_id)" not in sql and "UNIQUE (tenant_id, collection_id)" not in sql:
        _rebuild_table(conn, "organisms", ORGANISMS_TABLE)

NEW_MODEL_COLUMNS = {"tenant_id": "INTEGER REFERENCES tenants(tenant_id)"}

NEW_PLATE_IMAGE_COLUMNS = {
    "morphology_json": "TEXT", "cv_morphology_json": "TEXT",
    "bg_marker_x": "REAL", "bg_marker_y": "REAL",
    "qc_status": "TEXT", "qc_notes": "TEXT",
    "detected_artifacts": "TEXT", "ai_analyzed_at": "TEXT",
    "uploaded_by": "TEXT", "upload_timestamp": "TEXT",
    "colony_count": "INTEGER", "colony_coordinates": "TEXT",
    "segmentation_confidence": "REAL",
    # Per-plate media + incubation conditions -- different plates in the same
    # upload batch can use different media/atmosphere, so these live on each
    # plate_images row rather than the strain as a whole (see CHANGELOG v2.0).
    "media": "TEXT", "atmosphere": "TEXT", "captured_time": "TEXT",
    "tenant_id": "INTEGER REFERENCES tenants(tenant_id)",
    # Dish size (mm) for the colony sizes and the 3D model, and areas painted
    # over by hand that the analysis ignores (JSON list of [x, y, r] fractions).
    "plate_diameter_mm": "REAL", "ignore_areas": "TEXT",
}

PLATE_SIZES_MM = (35, 60, 90, 100, 150)
DEFAULT_PLATE_MM = 90
MAX_IGNORE_CIRCLES = 600


def _plate_size(value):
    """A dish size from a form field, or None if it is not one of PLATE_SIZES_MM."""
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return None
    return v if v in PLATE_SIZES_MM else None


def _ignore_areas(text):
    """Painted circles from the page, checked: a list of [x, y, r] with x and y
    between 0 and 1 and a sensible radius. Returns the cleaned list."""
    try:
        data = json.loads(text or "[]")
    except ValueError:
        return []
    out = []
    if not isinstance(data, list):
        return out
    for c in data[:MAX_IGNORE_CIRCLES]:
        if not isinstance(c, (list, tuple)) or len(c) < 3:
            continue
        try:
            x, y, r = (float(v) for v in c[:3])
        except (TypeError, ValueError):
            continue
        if 0 <= x <= 1 and 0 <= y <= 1 and 0 < r <= 0.25:
            out.append([round(x, 4), round(y, 4), round(r, 4)])
    return out


def _lab_plate_size(conn, tid, organism_id=None):
    """The dish size to assume: this strain's latest plate photo, else the lab's
    latest, else 90 mm. Most labs use one size, so this is usually right."""
    base = ("SELECT plate_diameter_mm FROM plate_images WHERE tenant_id=? "
            "AND plate_diameter_mm IS NOT NULL AND voided_at IS NULL")
    tries = [(base + " AND organism_id=?", (tid, organism_id))] if organism_id else []
    tries.append((base, (tid,)))
    for sql, args in tries:
        row = conn.execute(sql + " ORDER BY image_id DESC LIMIT 1", args).fetchone()
        if row and row[0]:
            return int(row[0])
    return DEFAULT_PLATE_MM

NEW_BREWING_RELEVANCE_COLUMNS = {
    "tenant_id": "INTEGER REFERENCES tenants(tenant_id)",
}

NEW_ACCESSION_COLUMNS = {"tenant_id": "INTEGER REFERENCES tenants(tenant_id)"}
NEW_TAG_COLUMNS = {"tenant_id": "INTEGER REFERENCES tenants(tenant_id)"}

# Culture-collection cross-references (NCYC/DSM/ATCC/CBS, yeast labs...). The
# original schema.sql had a bare UNIQUE(culture_collection, accession_number)
# -- fine for one lab, but two different labs legitimately referencing the
# same external accession (both importing "NCYC 1245", say) would collide
# under it. Rebuilt under UNIQUE(tenant_id, culture_collection,
# accession_number) the same way labs.name was (see _migrate_accessions_table).
ACCESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS accessions (
    accession_id       INTEGER PRIMARY KEY,
    organism_id        INTEGER NOT NULL REFERENCES organisms(organism_id) ON DELETE CASCADE,
    tenant_id          INTEGER REFERENCES tenants(tenant_id),
    culture_collection TEXT    NOT NULL,
    accession_number   TEXT    NOT NULL,
    date_acquired      TEXT,
    UNIQUE (tenant_id, culture_collection, accession_number)
);
"""


def _migrate_accessions_table(conn):
    """Upgrading from v3.0: the accessions table had a bare UNIQUE
    (culture_collection, accession_number), which would stop two different
    tenants from each holding a record for the same external accession.
    Rebuilds under the current UNIQUE(tenant_id, culture_collection,
    accession_number) definition. A no-op once already in that shape."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='accessions'").fetchone()
    if row and "UNIQUE (tenant_id" not in (row["sql"] or "") and "UNIQUE(tenant_id" not in (row["sql"] or ""):
        _rebuild_table(conn, "accessions", ACCESSIONS_TABLE)

# The lab that owns this installation. Since v4.7 an installation holds one lab;
# the tenant_id columns remain so that older databases upgrade without a rebuild.
APP_VERSION = "4.7.2"

# Messages sent from the Contact page. The lab's admins read them on the Admin
# page. username is filled from the signed-in session, never from the form.
CONTACT_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS contact_messages (
    message_id  INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    category    TEXT NOT NULL,
    name        TEXT,
    email       TEXT,
    message     TEXT NOT NULL,
    tenant_id   INTEGER,
    username    TEXT,
    is_read     INTEGER NOT NULL DEFAULT 0
);
"""

TENANTS_TABLE = """
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id  INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    is_active  INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    join_code  TEXT
);
"""

REVIVAL_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS revival_log (
    log_id              INTEGER PRIMARY KEY,
    organism_id         INTEGER NOT NULL REFERENCES organisms(organism_id) ON DELETE CASCADE,
    revival_date        TEXT    NOT NULL,
    revival_time        TEXT,
    performed_by        TEXT,
    growth_success      TEXT CHECK (growth_success IN ('pass','fail')),
    contamination_notes TEXT,
    colony_count        INTEGER,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_revival_org ON revival_log(organism_id);
"""

# Manually entered by the person doing the revival check (a hand count of
# colonies/CFUs on the viability plate) -- added after we removed automatic
# colony counting from plate-photo uploads (see CHANGELOG v1.7). Migrated in
# for any revival_log table created before this column existed.
NEW_REVIVAL_LOG_COLUMNS = {"colony_count": "INTEGER", "revival_time": "TEXT",
                          "tenant_id": "INTEGER REFERENCES tenants(tenant_id)"}

GRAM_STAIN_IMAGES_TABLE = """
CREATE TABLE IF NOT EXISTS gram_stain_images (
    image_id         INTEGER PRIMARY KEY,
    organism_id      INTEGER NOT NULL REFERENCES organisms(organism_id) ON DELETE CASCADE,
    file_path        TEXT,
    result           TEXT CHECK (result IN ('positive','negative','variable','not_done')),
    magnification    TEXT,
    stained_by       TEXT,
    stain_date       TEXT,
    stain_time       TEXT,
    notes            TEXT,
    uploaded_by      TEXT,
    upload_timestamp TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gram_org ON gram_stain_images(organism_id);
"""

# Migrated in for any gram_stain_images table created before this column
# existed (same pattern as NEW_REVIVAL_LOG_COLUMNS above).
NEW_GRAM_STAIN_IMAGE_COLUMNS = {"stain_time": "TEXT",
                               "tenant_id": "INTEGER REFERENCES tenants(tenant_id)",
                               # what the cells looked like: shape_config kinds
                               # and arrangements, or NULL when not recorded
                               "cell_shape": "TEXT", "cell_arrangement": "TEXT"}

FEEDBACK_TABLE = """
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id  INTEGER PRIMARY KEY,
    tenant_id    INTEGER REFERENCES tenants(tenant_id),
    rating       INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    ease         TEXT    NOT NULL,
    most_used    TEXT,
    improvement  TEXT,
    name         TEXT,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""
NEW_FEEDBACK_COLUMNS = {
    "tenant_id": "INTEGER REFERENCES tenants(tenant_id)",
    "answers": "TEXT",                      # JSON of the longer questionnaire
    "sus_score": "REAL",                    # System Usability Scale, 0 to 100
    "consent_research": "INTEGER DEFAULT 0",
    "contact_email": "TEXT",
    "app_version": "TEXT",
    "strains_count": "INTEGER",             # measured when the response is sent
    "plates_count": "INTEGER",
    "revival_checks_count": "INTEGER",
    "strains_with_location": "INTEGER",
    "days_in_use": "INTEGER",
}

# Named individual accounts (see README "Accounts & access"). Passwords are
# never stored in plain text -- only a salted hash (werkzeug's
# generate_password_hash). is_active lets admin revoke a departed
# colleague's access without deleting their name from history everywhere
# else (added_by/performed_by fields are plain text, not a foreign key, so
# a deactivated or renamed account never breaks old records). Every account
# belongs to the lab and is either 'admin' or 'user' (see "Accounts & access"
# in the README). Roles are checked in code (USER_ROLE_CHOICES), not by a
# CHECK constraint, so a role can be added later without a table rebuild.
USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    user_id             INTEGER PRIMARY KEY,
    tenant_id           INTEGER REFERENCES tenants(tenant_id),
    username            TEXT    NOT NULL UNIQUE,
    password_hash       TEXT    NOT NULL,
    role                TEXT    NOT NULL,
    full_name           TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1,
    failed_login_count  INTEGER NOT NULL DEFAULT 0,
    locked_until        TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""
NEW_USERS_COLUMNS = {
    "tenant_id": "INTEGER REFERENCES tenants(tenant_id)",
    # Login lockout (see _check_login_rate_limit / the login route): a count
    # of consecutive failed password attempts and, once it crosses the
    # threshold, how long the account stays locked. Both reset to 0/NULL on
    # any successful login.
    "failed_login_count": "INTEGER NOT NULL DEFAULT 0",
    "locked_until": "TEXT",
    # Someone who joined a lab with its join code waits here (is_active=0,
    # pending_approval=1) until one of the lab's admins approves them.
    "pending_approval": "INTEGER NOT NULL DEFAULT 0",
}

# Admin-managed list of source labs, offered as a dropdown when a sample's
# source is 'lab' (see organisms.sample_source_lab_id) -- where a physical
# sample came from, not to be confused with the tenants table above (the
# lab account that owns this data). Kept as a real table rather than a
# free-text suggestion (unlike media/atmosphere) because the whole point
# here is a controlled, per-lab-account-curated list. Scoped per tenant, so
# two different lab accounts can each have their own "Central QC Lab"
# entry without colliding (hence UNIQUE(tenant_id, name), not a bare
# UNIQUE(name)).
LABS_TABLE = """
CREATE TABLE IF NOT EXISTS labs (
    lab_id     INTEGER PRIMARY KEY,
    tenant_id  INTEGER REFERENCES tenants(tenant_id),
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(tenant_id, name)
);
"""
NEW_LABS_COLUMNS = {"tenant_id": "INTEGER REFERENCES tenants(tenant_id)"}


# Per-lab custom fields (see CHANGELOG v4.3): a lab defines extra fields for
# its own strains (e.g. "Isolation source", "Biosafety level", "Batch") and
# every strain can carry a value for each. Values are stored as text and
# validated against the field's type when saved.
CUSTOM_FIELDS_TABLE = """
CREATE TABLE IF NOT EXISTS custom_fields (
    field_id   INTEGER PRIMARY KEY,
    tenant_id  INTEGER NOT NULL REFERENCES tenants(tenant_id),
    name       TEXT NOT NULL,
    field_type TEXT NOT NULL DEFAULT 'text' CHECK (field_type IN ('text','number','date','choice')),
    choices    TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (tenant_id, name)
);
CREATE TABLE IF NOT EXISTS custom_field_values (
    organism_id INTEGER NOT NULL REFERENCES organisms(organism_id) ON DELETE CASCADE,
    field_id    INTEGER NOT NULL REFERENCES custom_fields(field_id) ON DELETE CASCADE,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(tenant_id),
    value       TEXT NOT NULL,
    PRIMARY KEY (organism_id, field_id)
);
"""
MAX_CUSTOM_FIELDS = 30


def _ensure_columns(conn, table, columns):
    """Add any missing columns; returns the names that were added."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    added = []
    for col, decl in columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            added.append(col)
    return added


# v4.7: history rows are voided (kept, hidden from current views, with who/when/why)
# rather than deleted, and each row records the signed-in account that entered it
# alongside the typed operator name.
HISTORY_AUDIT_COLUMNS = {"voided_at": "TEXT", "voided_by": "TEXT", "void_reason": "TEXT",
                         "entered_by_user": "TEXT"}

AUDIT_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id     INTEGER PRIMARY KEY,
    tenant_id    INTEGER REFERENCES tenants(tenant_id),
    username     TEXT,
    action       TEXT NOT NULL,
    entity       TEXT NOT NULL,
    entity_id    INTEGER,
    detail       TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_log(tenant_id, entity, entity_id);
"""


def audit(conn, action, entity, entity_id, detail=None, tenant=None):
    """Append one row to the audit log (never updated or deleted by the app)."""
    u = current_user()
    conn.execute("INSERT INTO audit_log (tenant_id, username, action, entity, entity_id, detail) "
                 "VALUES (?,?,?,?,?,?)",
                 (tenant if tenant is not None else (u["tenant_id"] if u else None),
                  u["username"] if u else None, action, entity, entity_id,
                  json.dumps(detail, default=str) if detail is not None else None))


def _signed_in_username():
    u = current_user()
    return u["username"] if u else None


def _rebuild_table(conn, table, create_sql, column_map=None):
    """Rebuild `table` under the new `create_sql` definition, copying every
    row across. Used only for the handful of constraints SQLite won't let
    ALTER TABLE change in place (a CHECK, or a UNIQUE that needs to become a
    composite one) -- see _migrate_users_table() and _migrate_labs_table().
    `column_map` renames/computes a destination column from the old row
    when the two schemas don't share a name 1:1; columns present in the new
    table but absent from the old one (and not in column_map) are left at
    their default/NULL. Safe to run more than once: if `table` is already
    the new shape this is a no-op copy.

    Follows SQLite's own documented procedure for this
    (https://www.sqlite.org/lang_altertable.html, "Making other kinds of
    table schema changes"): foreign keys are turned off around the
    drop-and-rename. Without that, dropping the old table with FK
    enforcement on doesn't just fail or block -- SQLite treats it as an
    implicit delete of every row and cascades to every child table with
    ON DELETE CASCADE, silently wiping out everything that referenced it
    (this bit organisms specifically: accessions/plate_images/models/
    brewing_relevance/tags/revival_log/gram_stain_images all cascade off
    organism_id). PRAGMA foreign_keys can't be changed mid-transaction, so
    this commits first and the caller's own transaction resumes after."""
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        tmp = f"{table}__migrating"
        conn.execute(f"DROP TABLE IF EXISTS {tmp}")
        conn.executescript(create_sql.replace(f"EXISTS {table} ", f"EXISTS {tmp} ", 1)
                           .replace(f"TABLE {table} ", f"TABLE {tmp} ", 1))
        old_cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
        new_cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({tmp})")]
        column_map = column_map or {}
        select_cols = []
        insert_cols = []
        for col in new_cols:
            if col in old_cols:
                select_cols.append(col)
                insert_cols.append(col)
            elif col in column_map:
                select_cols.append(column_map[col])
                insert_cols.append(col)
        conn.execute(
            f"INSERT INTO {tmp} ({', '.join(insert_cols)}) "
            f"SELECT {', '.join(select_cols)} FROM {table}")
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {tmp} RENAME TO {table}")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _migrate_users_table(conn):
    """Upgrading from v3.0: the users table was created with
    CHECK (role IN ('admin','user')). SQLite can't drop a CHECK constraint with ALTER
    TABLE, so this rebuilds the table under the current USERS_TABLE
    definition (no CHECK; roles are validated in code instead) the first
    time it's needed. A no-op once the table is already in that shape."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
    if row and "CHECK" in (row["sql"] or ""):
        _rebuild_table(conn, "users", USERS_TABLE)


def _migrate_labs_table(conn):
    """Upgrading from v3.0: the labs table had a bare UNIQUE(name), which
    would stop two different tenants from each having their own
    identically-named source lab. Rebuilds under the current UNIQUE
    (tenant_id, name) definition. A no-op once already in that shape."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='labs'").fetchone()
    if row and "UNIQUE(tenant_id" not in (row["sql"] or ""):
        _rebuild_table(conn, "labs", LABS_TABLE)


# Every tenant-owned table, used by the legacy-data migration and by the
# per-tenant export/admin helpers below. organisms is listed first since
# everything else hangs off organism_id.
TENANT_SCOPED_TABLES = [
    "organisms", "accessions", "plate_images", "models", "brewing_relevance",
    "tags", "revival_log", "gram_stain_images", "feedback", "labs",
]


def _normalize_stored_paths(conn):
    """Saved file paths always use forward slashes. Versions that ran on Windows
    stored model paths like "models\\x.stl" (Python's native separator there),
    which made every 3D model and STL download a "not found". Repairs them in
    place; a no-op once they're all clean."""
    for table in ("models", "plate_images", "gram_stain_images"):
        conn.execute(f"UPDATE {table} SET file_path=REPLACE(file_path, char(92), '/') "
                     f"WHERE file_path LIKE '%' || char(92) || '%'")
    conn.commit()


def _free_username(conn, base):
    """base, or base-2, base-3... -- whichever isn't taken yet."""
    name, n = base, 1
    while conn.execute("SELECT 1 FROM users WHERE username=?", (name,)).fetchone():
        n += 1
        name = f"{base}-{n}"
    return name


def _migrate_legacy_single_tenant_data(conn):
    """Upgrading from a pre-v4.0, single-tenant install: every data table
    already has rows, all with tenant_id NULL, and no tenants table entry
    exists for them yet. Creates one "Default Lab" tenant and backfills
    every NULL tenant_id (including any pre-existing admin/user accounts,
    who keep their role, just now scoped to that lab) so the upgrade
    doesn't lose or orphan any data. Does nothing on a brand-new install
    (no users yet at all) or one that's already been migrated (no NULL
    tenant_id rows left anywhere)."""
    legacy_user = conn.execute(
        "SELECT 1 FROM users WHERE tenant_id IS NULL AND role IN ('admin','user') LIMIT 1"
    ).fetchone()
    # An install from before accounts existed (v2.x) has strains but no users at
    # all. Its data still has to land in a lab someone can sign in to.
    legacy_data = conn.execute("SELECT 1 FROM organisms WHERE tenant_id IS NULL LIMIT 1").fetchone()
    if not legacy_user and not legacy_data:
        return None
    cur = conn.execute("INSERT INTO tenants (name) VALUES ('Default Lab')")
    tenant_id = cur.lastrowid
    for table in TENANT_SCOPED_TABLES:
        conn.execute(f"UPDATE {table} SET tenant_id=? WHERE tenant_id IS NULL", (tenant_id,))
    conn.execute(
        "UPDATE users SET tenant_id=? WHERE tenant_id IS NULL AND role IN ('admin','user')",
        (tenant_id,))
    conn.commit()
    print("Upgrading from a single-lab install: existing data and accounts "
         "were moved into a new \"Default Lab\". Rename it any time from "
         "the Admin page.")
    if legacy_user:
        return None
    # No accounts existed before, so create the lab's first admin; nobody could
    # otherwise open the data that was just moved in.
    username = _free_username(conn, "labadmin")
    password = secrets.token_urlsafe(9)
    conn.execute("INSERT INTO users (tenant_id, username, password_hash, role, full_name) "
                 "VALUES (?, ?, ?, 'admin', 'Lab admin')",
                 (tenant_id, username, generate_password_hash(password)))
    conn.commit()
    return username, password


def _remove_platform_accounts(conn):
    """Versions 4.0 to 4.6 had a platform 'superadmin' account above the labs.
    An installation now holds one lab, run by its own admins, so that account
    is removed on upgrade."""
    gone = conn.execute("DELETE FROM users WHERE role='superadmin'").rowcount
    conn.commit()
    if gone:
        print("The platform administrator account of earlier versions was removed. "
              "The lab's admins now manage everything; see manage.py to reset a password.")


def _check_single_lab(conn):
    n = conn.execute("SELECT COUNT(*) FROM tenants").fetchone()[0]
    if n > 1:
        raise SystemExit(
            f"This database holds {n} labs. From version 4.7 each lab has its own "
            "installation.\nList them with:   python manage.py list-labs\n"
            "Then make one copy per lab with:   python manage.py split-lab LAB_ID NEW_FOLDER\n"
            "and point each installation at its own folder (APP_DATA_DIR).")


def create_lab(conn, lab_name, username, password, full_name=None, include_examples=False):
    """Set up the installation's lab and its first admin. Returns the user_id."""
    cur = conn.execute("INSERT INTO tenants (name, join_code) VALUES (?, ?)",
                       (lab_name, _new_join_code()))
    tid = cur.lastrowid
    cur = conn.execute("INSERT INTO users (tenant_id, username, password_hash, role, full_name) "
                       "VALUES (?, ?, ?, 'admin', ?)",
                       (tid, username, generate_password_hash(password), full_name))
    uid = cur.lastrowid
    conn.commit()
    if include_examples:
        seed_example_data(conn, tid)
    return uid


def lab_exists(conn=None):
    own = conn is None
    conn = conn or db()
    row = conn.execute("SELECT 1 FROM tenants LIMIT 1").fetchone()
    if own:
        conn.close()
    return row is not None


SETUP_CODE_FILE = DATA_DIR / "setup-code.txt"


def setup_code():
    """A one-time code for the first-run setup page, shown in the terminal and
    kept in the data folder, so only someone with access to the server can set
    up the lab."""
    try:
        code = SETUP_CODE_FILE.read_text(encoding="utf-8").strip()
        if code:
            return code
    except OSError:
        pass
    code = _show_join_code(_new_join_code())
    SETUP_CODE_FILE.write_text(code + "\n", encoding="utf-8")
    return code


def _bootstrap_lab(conn):
    """Without a browser (Docker, a server), ADMIN_USERNAME and ADMIN_PASSWORD,
    plus LAB_NAME, set up the lab on first start. Otherwise the setup page does."""
    if lab_exists(conn):
        return
    username = os.environ.get("ADMIN_USERNAME", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not (username and password):
        return
    problem = _password_problem(password, username)
    if problem:
        print(f"ADMIN_PASSWORD was not used: {problem} Use the setup page instead.")
        return
    create_lab(conn, os.environ.get("LAB_NAME", "").strip() or "My lab", username, password,
               full_name="Lab admin")
    try:
        SETUP_CODE_FILE.unlink()
    except OSError:
        pass
    print(f"First run: set up the lab with the admin account \"{username}\".")


# --- Lab join codes -------------------------------------------------------
# Sign-up with a lab name always creates a NEW lab. To let a colleague join an
# EXISTING lab (and see the same data), each lab has a join code that its admin
# shares; entering it at sign-up creates a normal 'user' account in that lab.
# 8 characters from an alphabet without look-alikes (no 0/O, 1/I/L) = 32^8
# (~10^12) possibilities, and sign-up attempts are rate-limited per address.
JOIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _new_join_code():
    return "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(8))


def _normalize_join_code(raw):
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


def _show_join_code(code):
    return f"{code[:4]}-{code[4:]}" if code and len(code) == 8 else (code or "")


def _ensure_join_codes(conn):
    """Give every lab that has no join code one (existing installs, and any lab
    created by an older path)."""
    for row in conn.execute("SELECT tenant_id FROM tenants WHERE join_code IS NULL OR join_code=''").fetchall():
        conn.execute("UPDATE tenants SET join_code=? WHERE tenant_id=?", (_new_join_code(), row["tenant_id"]))
    conn.commit()


def reset_password(conn, username, new_password):
    """Set a new password for one account and clear any sign-in lockout.
    Used by manage.py, run on the machine that hosts the app."""
    row = conn.execute("SELECT user_id FROM users WHERE username=?", (username,)).fetchone()
    if row is None:
        return False
    conn.execute("UPDATE users SET password_hash=?, failed_login_count=0, locked_until=NULL "
                 "WHERE user_id=?", (generate_password_hash(new_password), row["user_id"]))
    import password_reset
    password_reset.new_stamp(conn, row["user_id"])          # ends the account's other sign-ins
    user = conn.execute("SELECT * FROM users WHERE user_id=?", (row["user_id"],)).fetchone()
    password_reset.record_set(conn, user, None, via="server-set")
    password_reset._audit(conn, "password_set_on_server", user, {})
    conn.commit()
    return True


def ensure_schema(conn):
    conn.executescript(TENANTS_TABLE)
    conn.execute("DROP TABLE IF EXISTS activation_codes")
    conn.commit()
    _ensure_columns(conn, "tenants", {"join_code": "TEXT",
                                      # 1 = store photos exactly as uploaded (see photo_store.py)
                                      "keep_original_photos": "INTEGER NOT NULL DEFAULT 0"})
    _ensure_columns(conn, "organisms", NEW_ORGANISM_COLUMNS)
    _ensure_columns(conn, "models", NEW_MODEL_COLUMNS)
    _ensure_columns(conn, "plate_images", NEW_PLATE_IMAGE_COLUMNS)
    _ensure_columns(conn, "brewing_relevance", NEW_BREWING_RELEVANCE_COLUMNS)
    _ensure_columns(conn, "accessions", NEW_ACCESSION_COLUMNS)
    _ensure_columns(conn, "tags", NEW_TAG_COLUMNS)
    conn.executescript(REVIVAL_LOG_TABLE)
    conn.executescript(GRAM_STAIN_IMAGES_TABLE)
    conn.executescript(FEEDBACK_TABLE)
    conn.executescript(CONTACT_MESSAGES_TABLE)
    conn.executescript(USERS_TABLE)
    conn.executescript(LABS_TABLE)
    conn.executescript(CUSTOM_FIELDS_TABLE)
    _ensure_columns(conn, "revival_log", NEW_REVIVAL_LOG_COLUMNS)
    _ensure_columns(conn, "gram_stain_images", NEW_GRAM_STAIN_IMAGE_COLUMNS)
    _ensure_columns(conn, "feedback", NEW_FEEDBACK_COLUMNS)
    for t in ("revival_log", "gram_stain_images", "plate_images"):
        _ensure_columns(conn, t, HISTORY_AUDIT_COLUMNS)
    conn.executescript(AUDIT_LOG_TABLE)
    if _ensure_columns(conn, "organisms", {"revival_baseline_date": "TEXT"}):
        # One-time: whatever date a strain already showed becomes its baseline, so an
        # upgrade never moves a due date. From now on the log is the source of truth.
        conn.execute("UPDATE organisms SET revival_baseline_date=last_revival_date "
                     "WHERE last_revival_date IS NOT NULL")
    _ensure_columns(conn, "users", NEW_USERS_COLUMNS)
    _ensure_columns(conn, "labs", NEW_LABS_COLUMNS)
    conn.commit()
    _migrate_users_table(conn)
    _migrate_labs_table(conn)
    _migrate_accessions_table(conn)
    _migrate_organisms_table(conn)
    conn.commit()
    _normalize_stored_paths(conn)
    legacy_admin = _migrate_legacy_single_tenant_data(conn)
    if legacy_admin:
        print("=" * 60)
        print("Your existing strains were moved into a lab called \"Default Lab\".")
        print("This version needs an account to sign in, so one was created for it:")
        print(f"  Username: {legacy_admin[0]}")
        print(f"  Password: {legacy_admin[1]}")
        print("  Sign in with it, then change the password from the Admin page.")
        print("=" * 60)
    import password_reset
    password_reset.ensure_schema(conn)
    _ensure_join_codes(conn)
    _remove_platform_accounts(conn)
    _check_single_lab(conn)
    _bootstrap_lab(conn)

def init_db_if_needed():
    if not DB_PATH.exists():
        _build_db()
        set_write_mode()
        return
    set_write_mode()
    conn = db()
    has = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='organisms'"
    ).fetchone()
    if has is None:
        conn.close()
        _build_db()
    else:
        ensure_schema(conn)
        conn.close()


def _build_db():
    conn = db()
    conn.executescript((BASE / "schema.sql").read_text(encoding="utf-8"))
    ensure_schema(conn)
    conn.close()


def seed_example_data(conn, tenant_id):
    """Insert the illustrative example strains (seed_data.py) into one
    lab's catalog -- offered as a checkbox at sign-up so a newly created lab
    isn't staring at an empty catalog. Safe to call when the organisms table
    already holds other tenants' rows: each seed organism's real,
    DB-assigned organism_id is tracked and used to link its accessions/
    tags/brewing_relevance rows, rather than assuming (as the old
    single-tenant _build_db() could) that the table was empty beforehand."""
    from seed_data import build_seed_rows
    orgs, accs, tgs, rels = build_seed_rows()
    logical_to_real = {}
    for i, o in enumerate(orgs, start=1):
        cur = conn.execute(
            """INSERT INTO organisms
               (tenant_id, collection_id, genus, species, strain, full_name, ncbi_taxonomy_id, notes,
                collection_type, gram_stain, added_by, viability_category, revival_interval_days, last_revival_date)
               VALUES (:tenant_id, :collection_id, :genus, :species, :strain, :full_name, :ncbi_taxonomy_id, :notes,
                       :collection_type, :gram_stain, :added_by, :viability_category, :revival_interval_days, :last_revival_date)""",
            {**o, "tenant_id": tenant_id})
        logical_to_real[i] = cur.lastrowid
        conn.execute("UPDATE organisms SET revival_baseline_date=last_revival_date WHERE organism_id=?",
                     (cur.lastrowid,))
    for a in accs:
        conn.execute(
            "INSERT INTO accessions (tenant_id, organism_id, culture_collection, accession_number) "
            "VALUES (?, ?, ?, ?)",
            (tenant_id, logical_to_real[a["organism_id"]], a["culture_collection"], a["accession_number"]))
    for t in tgs:
        conn.execute("INSERT INTO tags (tenant_id, organism_id, tag) VALUES (?, ?, ?)",
                     (tenant_id, logical_to_real[t["organism_id"]], t["tag"]))
    for r in rels:
        conn.execute("""INSERT INTO brewing_relevance
            (tenant_id, organism_id, role, attenuation_min, attenuation_max, temp_range_c,
             flavor_compounds, off_flavor_potential, spoilage_risk, application_notes)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (tenant_id, logical_to_real[r["organism_id"]], r["role"], r["attenuation_min"],
             r["attenuation_max"], r["temp_range_c"], r["flavor_compounds"],
             r["off_flavor_potential"], r["spoilage_risk"], r["application_notes"]))
    conn.commit()


# --------------------------------------------------------------------------- #
# Accounts & access control
# --------------------------------------------------------------------------- #
# One lab per installation, with two roles. 'user' can fully manage strain and
# sample records: add, edit, void, and log revivals, Gram stains and plates.
# 'admin' can do all of that, plus what affects everyone in the lab: exports,
# users and join requests, the source-labs list, custom fields and messages.
# The whole app needs a sign-in; the pages below are the only exceptions.
PUBLIC_ENDPOINTS = {"login", "signup", "setup", "about", "contact", "static", "manifest", "service_worker"}


def current_user():
    if "user_id" not in session:
        return None
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE user_id=? AND is_active=1",
                       (session["user_id"],)).fetchone()
    conn.close()
    if row is not None and not _pwreset.session_matches(row):
        return None             # the password was reset or changed after this browser signed in
    return row


def current_tenant():
    """The signed-in user's lab row, or None when nobody is signed in."""
    user = current_user()
    if user is None or user["tenant_id"] is None:
        return None
    conn = db()
    row = conn.execute("SELECT * FROM tenants WHERE tenant_id=?", (user["tenant_id"],)).fetchone()
    conn.close()
    return row


def tenant_id():
    """The scoping value every tenant-owned query must filter by. Aborts
    rather than returning None/0, because a query that silently dropped its
    tenant filter on a falsy value would return every lab's data instead of
    none of it -- the opposite of fail-safe."""
    user = current_user()
    if user is None or user["tenant_id"] is None:
        abort(403)
    return user["tenant_id"]


app.jinja_env.globals["current_user"] = current_user
app.jinja_env.globals["current_tenant"] = current_tenant


@app.before_request
def _require_login():
    if request.endpoint in ("static", "manifest", "service_worker", "setup") or request.endpoint is None:
        return None
    if not lab_exists():
        return redirect(url_for("setup"))
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    user = current_user()
    if user is None:
        session.clear()
        if request.method == "GET":
            session["login_next"] = request.full_path
        return redirect(url_for("login"))
    return None


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None or user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def get_labs():
    conn = db()
    rows = conn.execute("SELECT * FROM labs WHERE tenant_id=? ORDER BY name",
                       (tenant_id(),)).fetchall()
    conn.close()
    return rows


def get_custom_fields():
    """This lab's custom field definitions, in display order, with the
    comma-separated choices column parsed into a list."""
    conn = db()
    rows = conn.execute("SELECT * FROM custom_fields WHERE tenant_id=? ORDER BY sort_order, name",
                        (tenant_id(),)).fetchall()
    conn.close()
    return [dict(field_id=r["field_id"], name=r["name"], field_type=r["field_type"],
                 choices=[c for c in (r["choices"] or "").split("\n") if c]) for r in rows]


def custom_inputs(oid=None):
    """Each custom field with this strain's current value (None for a new
    strain), shaped for the shared edit/add/view templates."""
    fields = get_custom_fields()
    values = {}
    if oid is not None and fields:
        conn = db()
        values = {r["field_id"]: r["value"] for r in conn.execute(
            "SELECT field_id, value FROM custom_field_values WHERE organism_id=? AND tenant_id=?",
            (oid, tenant_id()))}
        conn.close()
    return [dict(f, value=values.get(f["field_id"])) for f in fields]


def _save_custom_values(conn, oid, tid, form):
    """Store the cf_<field_id> inputs of a submitted form. A blank input clears
    that field; a value that doesn't fit the field's type is skipped and its
    field name returned so the caller can tell the user."""
    rejected = []
    for f in conn.execute("SELECT * FROM custom_fields WHERE tenant_id=?", (tid,)).fetchall():
        key = f"cf_{f['field_id']}"
        if key not in form:
            continue
        choices = [c for c in (f["choices"] or "").split("\n") if c]
        val, problem = importer.normalize_custom(f["field_type"], choices, form.get(key))
        if problem:
            rejected.append(f["name"])
            continue
        if val is None:
            conn.execute("DELETE FROM custom_field_values WHERE organism_id=? AND field_id=?",
                         (oid, f["field_id"]))
        else:
            conn.execute("""INSERT INTO custom_field_values (organism_id, field_id, tenant_id, value)
                            VALUES (?,?,?,?)
                            ON CONFLICT(organism_id, field_id) DO UPDATE SET value=excluded.value""",
                         (oid, f["field_id"], tid, val))
    return rejected


def storage_summary(org):
    """"Freezer 2 / Rack 1 / Box 4 / A3" from the structured fields, falling
    back to the free-text location older records (and imports) use."""
    parts = [org[k] for k in ("storage_freezer", "storage_rack", "storage_box", "storage_position")
             if org[k]]
    return " / ".join(parts) if parts else (org["storage_location"] or "")


app.jinja_env.globals["storage_summary"] = storage_summary


def _position_clash(conn, tid, form, exclude_oid=None):
    """IDs of other strains in this lab recorded at the same freezer, rack, box
    and position as the submitted form. Empty when no position was given."""
    pos = (form.get("storage_position") or "").strip().lower()
    if not pos:
        return []
    key = [(form.get(k) or "").strip().lower() for k in ("storage_freezer", "storage_rack", "storage_box")]
    rows = conn.execute("""SELECT organism_id, collection_id FROM organisms
                           WHERE tenant_id=? AND lower(trim(COALESCE(storage_position,'')))=?
                             AND lower(trim(COALESCE(storage_freezer,'')))=?
                             AND lower(trim(COALESCE(storage_rack,'')))=?
                             AND lower(trim(COALESCE(storage_box,'')))=?""",
                        (tid, pos, key[0], key[1], key[2])).fetchall()
    return [r["collection_id"] for r in rows if r["organism_id"] != exclude_oid]


def _flash_position_clash(conn, tid, form, exclude_oid=None):
    ids = _position_clash(conn, tid, form, exclude_oid)
    if ids:
        flash("Saved, but that position is already recorded for " + ", ".join(ids[:5])
              + ". Check which tube is really there and fix one of the records.")
app.jinja_env.filters["basename"] = lambda p: re.split(r"[\\/]", p or "")[-1]


def _code(genus, species):
    g = (genus[:3] or "XXX").upper()
    s = "SPP" if species.lower() in ("spp.", "spp", "sp.", "sp") else species[:3].upper()
    return f"MDL-{g}{s}"


def next_collection_id(genus, species, conn=None):
    """Collection IDs (MDL-XXXXXX-NNN) are only unique within a lab, not
    platform-wide -- two different labs can register the same genus/species
    and land on the same code, since every lookup by collection_id is also
    scoped by tenant_id (see get_organism)."""
    prefix = _code(genus, species)
    tid = tenant_id()
    own = conn is None                 # the caller's connection when there is one, so the
    if own:                            # read happens inside the transaction that saves
        conn = db()
    rows = conn.execute(
        "SELECT collection_id FROM organisms WHERE tenant_id=? AND collection_id LIKE ?",
        (tid, prefix + "-%")
    ).fetchall()
    if own:
        conn.close()
    nums = [int(r["collection_id"].rsplit("-", 1)[1]) for r in rows if r["collection_id"].rsplit("-", 1)[1].isdigit()]
    return f"{prefix}-{max(nums, default=0) + 1:03d}"


# --------------------------------------------------------------------------- #
# Viability / revival-due logic (deterministic: dates matter too much to be
# anything else)
# --------------------------------------------------------------------------- #
def compute_revival_status(last_revival_date, revival_interval_days, today=None,
                           last_result=None, baseline_source="revival"):
    """Returns dict(status, due_date, days_overdue, days_until_due, baseline_source).

    status is one of:
      'failed'      -- the most recent revival check failed (red until a passing check)
      'nobaseline'  -- an interval is set but there is no date to count from (red)
      'overdue', 'due', 'notDue' -- date arithmetic from the last passing revival
      'unscheduled' -- no interval set

    A missing or unreadable date is never treated as "today": that used to make
    a strain that had never been revived roll forward and stay "not due" forever.
    """
    today = today or date.today()
    if not revival_interval_days:
        return dict(status="unscheduled", due_date=None, days_overdue=0, days_until_due=0,
                    baseline_source=None)
    last = None
    if last_revival_date:
        try:
            last = datetime.strptime(str(last_revival_date)[:10], "%Y-%m-%d").date()
        except ValueError:
            last = None
    if last_result == "fail":
        due = (last + timedelta(days=int(revival_interval_days))).isoformat() if last else None
        return dict(status="failed", due_date=due, days_overdue=0, days_until_due=0,
                    baseline_source=baseline_source)
    if last is None:
        return dict(status="nobaseline", due_date=None, days_overdue=0, days_until_due=0,
                    baseline_source=None)
    due = last + timedelta(days=int(revival_interval_days))
    delta = (today - due).days
    base = dict(due_date=due.isoformat(), baseline_source=baseline_source)
    if delta > 0:
        return dict(base, status="overdue", days_overdue=delta, days_until_due=0)
    if delta == 0:
        return dict(base, status="due", days_overdue=0, days_until_due=0)
    return dict(base, status="notDue", days_overdue=0, days_until_due=-delta)


def org_revival_status(org, today=None):
    """Revival status for an organisms row. Counts from the last passing revival;
    if none is recorded, from the date the strain was stored; a failed latest
    check overrides everything until a passing check is logged."""
    keys = org.keys()
    last = org["last_revival_date"]
    source = "revival"
    if not last and "date_stored" in keys and org["date_stored"]:
        last, source = org["date_stored"], "date_stored"
    result = org["growth_success"] if "growth_success" in keys else None
    return compute_revival_status(last, org["revival_interval_days"], today=today,
                                  last_result=result, baseline_source=source)


def recompute_revival_summary(conn, organism_id):
    """Rebuild the strain's revival summary from its (non-voided) revival log.

    last_revival_date = the latest PASSING check (or the baseline date entered
    on the strain form / by import, whichever is later); growth_success = the
    result of the most recent check by date and time. A failed check therefore
    never resets the schedule, and back-dated entries never move it backwards."""
    org = conn.execute("SELECT revival_baseline_date FROM organisms WHERE organism_id=?",
                       (organism_id,)).fetchone()
    baseline = org["revival_baseline_date"] if org else None
    rows = conn.execute("""SELECT * FROM revival_log WHERE organism_id=? AND voided_at IS NULL
                           ORDER BY revival_date, COALESCE(revival_time,''), log_id""",
                        (organism_id,)).fetchall()
    passes = [r for r in rows if r["growth_success"] == "pass"]
    last_pass = passes[-1] if passes else None
    latest = rows[-1] if rows else None
    candidates = [d for d in (baseline, last_pass["revival_date"] if last_pass else None) if d]
    last_date = max(candidates) if candidates else None
    last_time = last_pass["revival_time"] if last_pass and last_pass["revival_date"] == last_date else None
    result = None
    if latest is not None:
        if latest["growth_success"] == "fail" and (last_date is None or latest["revival_date"] >= last_date):
            result = "fail"
        elif latest["growth_success"] == "pass":
            result = "pass"
    conn.execute("""UPDATE organisms SET last_revival_date=?, last_revival_time=?, growth_success=?,
                    contamination_notes=?, colony_count=?, updated_at=datetime('now')
                    WHERE organism_id=?""",
                 (last_date, last_time, result,
                  latest["contamination_notes"] if latest else None,
                  latest["colony_count"] if latest else None, organism_id))


# Days-until-due at or below this counts as the "amber" early-warning band;
# above it is "green" (comfortably not due yet).
DUE_SOON_WINDOW_DAYS = 5


def revival_badge(status):
    """Maps a compute_revival_status() dict to a traffic-light display:
    red (due today or overdue), amber (due within the next few days -- a
    heads-up window), green (comfortably not due yet), or muted (no
    schedule). Returns dict(cls, color, label) -- `cls` is the CSS badge
    class already used elsewhere in the app, `color` is the plain semantic
    name, `label` is ready-to-display text."""
    s = status["status"]
    if s == "failed":
        return dict(cls="b-bad", color="red", label="last revival failed: re-check now")
    if s == "nobaseline":
        return dict(cls="b-bad", color="red", label="no revival date on record: check now")
    if s == "overdue":
        d = status["days_overdue"]
        return dict(cls="b-bad", color="red",
                   label=f"overdue by {d} day{'s' if d != 1 else ''}")
    if s == "due":
        return dict(cls="b-bad", color="red", label="due today")
    if s == "notDue":
        days = status["days_until_due"]
        if days <= DUE_SOON_WINDOW_DAYS:
            return dict(cls="b-warn", color="amber",
                       label=f"due in {days} day{'s' if days != 1 else ''}")
        return dict(cls="b-good", color="green", label=f"not due ({status['due_date']})")
    return dict(cls="b-muted", color="muted", label="unscheduled")


def revival_notification_message(strain_name, status, due_date, days_overdue):
    if status == "overdue":
        plural = "day" if days_overdue == 1 else "days"
        return f"{strain_name} is overdue for revival by {days_overdue} {plural} (was due {due_date})."
    if status == "due":
        return f"{strain_name} is due for revival today ({due_date})."
    if status == "notDue":
        return f"{strain_name} isn't due for revival until {due_date}."
    if status == "failed":
        return f"{strain_name}: the last revival check failed. Re-check it before relying on this stock."
    if status == "nobaseline":
        return f"{strain_name} has a revival interval but no revival or storage date to count from."
    return f"{strain_name} has no revival schedule set."


# --------------------------------------------------------------------------- #
# Organism queries
# --------------------------------------------------------------------------- #
def get_organisms():
    conn = db()
    rows = conn.execute("""
        SELECT o.*,
               (SELECT COUNT(*) FROM models m WHERE m.organism_id = o.organism_id) AS model_count,
               (SELECT COUNT(*) FROM models m WHERE m.organism_id = o.organism_id
                   AND m.is_print_ready = 1) AS print_ready,
               (SELECT COUNT(*) FROM plate_images i WHERE i.organism_id = o.organism_id
                   AND i.voided_at IS NULL) AS image_count
        FROM organisms o
        WHERE o.tenant_id = ?
        ORDER BY o.collection_id
    """, (tenant_id(),)).fetchall()
    conn.close()
    return rows


def get_organism(cid):
    """Looks up one strain by its collection_id, scoped to the current
    lab -- this is the one place that decides whether a cid in a URL
    belongs to the caller's own lab. Every /species/<cid>/... route either
    calls this first, or (for a child row like a plate image) verifies the
    organism_id it's handed belongs to an organism this returns, so a lab
    can never reach another lab's record just by guessing or incrementing
    an id in the URL."""
    conn = db()
    org = conn.execute("SELECT * FROM organisms WHERE collection_id = ? AND tenant_id = ?",
                      (cid, tenant_id())).fetchone()
    if org is None:
        conn.close()
        return None
    accs = conn.execute("SELECT * FROM accessions WHERE organism_id=? ORDER BY culture_collection",
                        (org["organism_id"],)).fetchall()
    tags = conn.execute("SELECT * FROM tags WHERE organism_id=? ORDER BY tag",
                        (org["organism_id"],)).fetchall()
    rel = conn.execute("SELECT * FROM brewing_relevance WHERE organism_id=?",
                       (org["organism_id"],)).fetchone()
    imgs = conn.execute("SELECT * FROM plate_images WHERE organism_id=? AND voided_at IS NULL "
                        "ORDER BY image_id DESC", (org["organism_id"],)).fetchall()
    models = conn.execute("SELECT * FROM models WHERE organism_id=? ORDER BY model_type",
                          (org["organism_id"],)).fetchall()
    revivals = conn.execute("SELECT * FROM revival_log WHERE organism_id=? AND voided_at IS NULL "
                            "ORDER BY revival_date DESC, COALESCE(revival_time,'') DESC, log_id DESC",
                            (org["organism_id"],)).fetchall()
    gram_images = conn.execute(
        "SELECT * FROM gram_stain_images WHERE organism_id=? AND voided_at IS NULL ORDER BY image_id DESC",
        (org["organism_id"],)).fetchall()
    voided = []
    for kind, sql in (("Revival check", "SELECT log_id AS id, revival_date AS dated, growth_success AS what, voided_at, voided_by, void_reason FROM revival_log"),
                      ("Gram stain", "SELECT image_id AS id, stain_date AS dated, result AS what, voided_at, voided_by, void_reason FROM gram_stain_images"),
                      ("Plate photo", "SELECT image_id AS id, captured_date AS dated, media AS what, voided_at, voided_by, void_reason FROM plate_images")):
        for r in conn.execute(sql + " WHERE organism_id=? AND voided_at IS NOT NULL", (org["organism_id"],)):
            voided.append(dict(kind=kind, **dict(r)))
    voided.sort(key=lambda v: v["voided_at"] or "", reverse=True)
    history = conn.execute("SELECT * FROM audit_log WHERE tenant_id=? AND entity='organism' AND entity_id=? "
                           "ORDER BY audit_id DESC LIMIT 50", (org["tenant_id"], org["organism_id"])).fetchall()
    lab_name = None
    if org["sample_source_type"] == "lab" and org["sample_source_lab_id"]:
        lab_row = conn.execute("SELECT name FROM labs WHERE lab_id=?",
                               (org["sample_source_lab_id"],)).fetchone()
        lab_name = lab_row["name"] if lab_row else None
    conn.close()
    revival = org_revival_status(org)
    return dict(org=org, accs=accs, tags=tags, rel=rel, imgs=imgs, models=models,
               revivals=revivals, revival=revival, gram_images=gram_images,
               voided=voided, history=history,
               sample_source_lab_name=lab_name, custom=custom_inputs(org["organism_id"]))


def _parsed_int(form, key):
    v = (form.get(key) or "").strip()
    return int(v) if v.isdigit() else None


def _sample_source_fields(form):
    """Parse the Lab / Online-reference-database source choice into the
    three DB fields that represent it: a type, a lab_id (when type='lab'),
    and the existing free-text sample_source column repurposed to hold the
    online reference's name (when type='online'). See CHANGELOG v3.0."""
    source_type = form.get("sample_source_type", "").strip()
    if source_type not in ("lab", "online"):
        source_type = None
    lab_id = _parsed_int(form, "sample_source_lab_id") if source_type == "lab" else None
    online_ref = form.get("sample_source_ref", "").strip() or None if source_type == "online" else None
    return source_type, lab_id, online_ref


def _save_organism_form(conn, oid, form):
    source_type, lab_id, online_ref = _sample_source_fields(form)
    conn.execute("""
        UPDATE organisms SET
            strain=?, full_name=COALESCE(?, full_name), ncbi_taxonomy_id=?, notes=?, target_status=?,
            collection_type=?, gram_stain=?, media=?, sample_source=?,
            sample_source_type=?, sample_source_lab_id=?,
            oxygen_requirement=?, optimal_temp_c=?, morphology_notes=?,
            storage_location=?, storage_format=?, storage_temp_c=?,
            storage_freezer=?, storage_rack=?, storage_box=?, storage_position=?,
            date_stored=?, last_verified_date=?,
            added_by=?, viability_category=?, revival_interval_days=?,
            revival_baseline_date=?,
            updated_at=datetime('now')
        WHERE organism_id=?
    """, (
        form.get("strain", "").strip() or None,
        form.get("full_name", "").strip() or None,
        form.get("ncbi", "").strip() or None,
        form.get("notes", "").strip() or None,
        form.get("target_status", "initial").strip() or "initial",
        form.get("collection_type", "library").strip() or "library",
        form.get("gram_stain", "").strip() or None,
        form.get("media", "").strip() or None,
        online_ref,
        source_type, lab_id,
        form.get("oxygen_requirement", "").strip() or None,
        form.get("optimal_temp_c", "").strip() or None,
        form.get("morphology_notes", "").strip() or None,
        form.get("storage_location", "").strip() or None,
        form.get("storage_format", "").strip() or None,
        form.get("storage_temp_c", "").strip() or None,
        form.get("storage_freezer", "").strip() or None,
        form.get("storage_rack", "").strip() or None,
        form.get("storage_box", "").strip() or None,
        form.get("storage_position", "").strip() or None,
        form.get("date_stored", "").strip() or None,
        form.get("last_verified_date", "").strip() or None,
        form.get("added_by", "").strip() or None,
        form.get("viability_category", "unknown").strip() or "unknown",
        _parsed_int(form, "revival_interval_days"),
        form.get("revival_baseline_date", "").strip() or None,
        oid,
    ))
    recompute_revival_summary(conn, oid)
    conn.execute("""
        UPDATE brewing_relevance SET
            role=?, attenuation_min=?, attenuation_max=?, temp_range_c=?,
            flavor_compounds=?, off_flavor_potential=?, spoilage_risk=?, application_notes=?
        WHERE organism_id=?
    """, (
        form.get("role", "").strip() or None,
        form.get("att_min", "").strip() or None,
        form.get("att_max", "").strip() or None,
        form.get("temp", "").strip() or None,
        form.get("flavor", "").strip() or None,
        form.get("off_flavor", "").strip() or None,
        form.get("risk", "").strip() or None,
        form.get("application", "").strip() or None,
        oid,
    ))
    conn.commit()


# --------------------------------------------------------------------------- #
# Model generation (stylized procedural + photo-driven relief)
# --------------------------------------------------------------------------- #
def _stable_seed(text):
    """Deterministic seed from a collection ID, so regenerating a species's
    model reproduces the same colony layout rather than reshuffling it on
    every click (Python's built-in hash() is randomized per process)."""
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)


def generate_models_for(org):
    """Stylized procedural models -- always available, for any species,
    via the shape_config fallback chain (custom override -> built-in table ->
    generic default), so this never silently produces nothing.

    The colony model is a plate of individually shaped colonies at typical sizes;
    its STL and its viewer scene share one layout (model_builder.py). The cell
    model shows the typical arrangement, coloured by the recorded Gram result."""
    conn = db()
    cfg = resolve_shape_config(_shape_input(conn, org))
    conn.close()
    cid = org["collection_id"]
    # Collection IDs are only unique within a lab, so model files carry the
    # lab's id too -- otherwise two labs with the same ID would overwrite
    # each other's files (and serve each other's models).
    prefix = f"t{org['tenant_id']}-{cid}"
    conn = db()
    old_files = [r["file_path"] for r in conn.execute(
        "SELECT file_path FROM models WHERE organism_id=? AND source='procedural'", (org["organism_id"],))]
    conn.execute("DELETE FROM models WHERE organism_id=? AND source='procedural'",
                 (org["organism_id"],))
    _remove_files(old_files)
    made = 0
    seed = _stable_seed(cid)
    dish_mm = _lab_plate_size(conn, org["tenant_id"], org["organism_id"])
    for mtype, shape in [("colony", cfg["colony"]), ("cell", cfg["cell"])]:
        base = f"{prefix}-{mtype}-STL-v2"
        stl = MODEL_DIR / f"{base}.stl"
        glb = MODEL_DIR / f"{base}.glb"
        if mtype == "colony":
            watertight, note = model_builder.build_colony_files(shape, seed, stl, glb,
                                                                diameter_mm=dish_mm)
        else:
            watertight, note = model_builder.build_cell_files(shape, stl, glb)
        conn.execute("""INSERT INTO models
            (organism_id, tenant_id, model_type, source, file_format, file_path, scale_factor,
             level_of_detail, is_print_ready, is_watertight, qa_status, version, created_date, notes)
            VALUES (?, ?, ?, 'procedural', 'STL', ?, 1.0, 'medium', ?, ?, 'review', 2, ?, ?)""",
            (org["organism_id"], org["tenant_id"], mtype, stl.relative_to(DATA_DIR).as_posix(),
             1 if watertight else 0, 1 if watertight else 0, date.today().isoformat(),
             f"{note} GLB companion: {glb.name}"))
        made += 1
    conn.commit()
    conn.close()
    return made


# 128 rings x 256 spokes: about 0.35 mm radial and 1.1 mm at the rim, so colonies
# of 1-3 mm keep their shape (40 x 96 turned them into terraced "mountains").
PHOTO_RELIEF_RINGS = 128
PHOTO_RELIEF_SPOKES = 256
PHOTO_RELIEF_MM = 2.0     # full dome height; about 4x a typical colony, for visibility


def build_photo_relief(image_row, org):
    """Real photo -> height map -> watertight textured dish mesh.

    This used to also run automatic colony counting (scale-space blob
    detection) and store a count/confidence on the image. That's been
    removed as a user-facing feature (see CHANGELOG v1.7): it could
    misread handwriting/labels on the plate as colonies, and more
    generally can't reliably split colonies that are touching in the
    photo. Colony counts for viability/validation isolates are now a
    plain manual number entered when logging a revival check (see
    log_revival()) rather than a CV estimate.

    Returns dict(made, error=...) -- made is False if the relief couldn't
    be built (missing file, or Pillow/opencv/scipy not available)."""
    img_path = DATA_DIR / image_row["file_path"]
    if not img_path.exists():
        return dict(made=False, error="image file missing on disk")

    bg_marker = None
    if image_row["bg_marker_x"] is not None and image_row["bg_marker_y"] is not None:
        bg_marker = (image_row["bg_marker_x"], image_row["bg_marker_y"])
    if pv.Image is None:
        return dict(made=False, error="Pillow not available")
    # Pad (never crop) the photo, find the dish, crop and centre on it, then
    # analyse: the relief and its texture both cover the dish, not the frame.
    keys = image_row.keys()
    dish_mm = (image_row["plate_diameter_mm"] if "plate_diameter_mm" in keys else None) or DEFAULT_PLATE_MM
    ignore = _ignore_areas(image_row["ignore_areas"] if "ignore_areas" in keys else None)
    result = pv.analyse_plate(img_path, bg_marker=bg_marker, diameter_mm=dish_mm, ignore=ignore)
    intensity, morph = result["intensity"], result["morphology"]

    tex_path = MODEL_DIR / f"img{image_row['image_id']}-texture.png"
    try:
        result["texture"].save(tex_path)
    except Exception:
        tex_path = None
    agar_rgb = tuple(float(c) / 255.0 for c in pv._dominant_color(
        result["rgb"], pv._dish_mask(result["rgb"].shape, result["circle"], margin=0.05)))

    mesh = m3.photo_colony_plate(result["height"], diameter_mm=dish_mm, relief_mm=PHOTO_RELIEF_MM,
                                 n_r=PHOTO_RELIEF_RINGS, n_theta=PHOTO_RELIEF_SPOKES)
    cid = org["collection_id"]
    base = f"t{org['tenant_id']}-{cid}-img{image_row['image_id']}-photorelief-v1"
    stl_path = MODEL_DIR / f"{base}.stl"
    glb_path = MODEL_DIR / f"{base}.glb"
    m3.write_stl_binary(mesh, stl_path)
    with scene3d.dish_size(dish_mm):
        scene3d.write_photo_scene(glb_path, mesh, texture_path=tex_path, agar_color=agar_rgb)
    watertight = mesh.is_watertight()

    conn = db()
    conn.execute("DELETE FROM models WHERE organism_id=? AND file_path LIKE ?",
                 (org["organism_id"], f"%img{image_row['image_id']}-photorelief%"))
    conn.execute("""INSERT INTO models
        (organism_id, tenant_id, model_type, source, file_format, file_path, scale_factor,
         level_of_detail, is_print_ready, is_watertight, qa_status, version, created_date, notes)
        VALUES (?, ?, 'photo_relief', 'photo', 'STL', ?, 1.0, 'medium', ?, ?, 'review', 1, ?, ?)""",
        (org["organism_id"], org["tenant_id"], stl_path.relative_to(DATA_DIR).as_posix(), 1 if watertight else 0,
         1 if watertight else 0, date.today().isoformat(),
         f"Photo-driven relief from plate image #{image_row['image_id']} on a {dish_mm:g} mm dish; "
         f"GLB: {glb_path.name}"))
    conn.execute("UPDATE plate_images SET cv_morphology_json=? WHERE image_id=?",
                (json.dumps(morph), image_row["image_id"]))
    conn.commit()
    conn.close()
    return dict(made=True, morphology=morph)


def _cv_missing_reason():
    """Why the photo-driven 3D relief might be unavailable on this machine,
    if it is. Returns None when everything needed is present."""
    missing = []
    if pv.cv2 is None:
        missing.append("opencv-python-headless")
    if pv.gaussian_laplace is None or pv.maximum_filter is None:
        missing.append("scipy")
    if pv.Image is None:
        missing.append("Pillow")
    if not missing:
        return None
    return ("the photo-driven 3D relief needs " + " and ".join(missing) +
           f"; run: pip install {' '.join(missing)}")


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def _csv_safe(value):
    """Spreadsheet programs run a cell that starts with = + - @ as a formula,
    so a strain note like =HYPERLINK(...) -- typed in, or arriving via an
    imported file -- would execute when the export is opened. Prefix such text
    with an apostrophe (genuine numbers such as -80 are left alone)."""
    if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        try:
            float(value.replace(",", ""))
        except ValueError:
            return "'" + value
    return value


FLAT_CATALOG_SQL = """
    SELECT o.collection_id, o.full_name, o.genus, o.species, o.strain, o.collection_type,
           o.ncbi_taxonomy_id, o.target_status, o.notes, o.added_by, o.viability_category,
           o.revival_interval_days, o.last_revival_date, o.gram_stain,
           o.media, o.oxygen_requirement, o.optimal_temp_c, o.morphology_notes,
           o.storage_freezer, o.storage_rack, o.storage_box, o.storage_position,
           o.storage_location, o.storage_format, o.storage_temp_c, o.date_stored,
           CASE WHEN o.sample_source_type='lab' THEN l.name END AS source_lab,
           CASE WHEN o.sample_source_type='online' THEN o.sample_source END AS online_reference,
           GROUP_CONCAT(DISTINCT a.culture_collection || ' ' || a.accession_number) AS accessions,
           GROUP_CONCAT(DISTINCT t.tag) AS tags,
           b.role, b.attenuation_min, b.attenuation_max, b.temp_range_c,
           b.flavor_compounds, b.off_flavor_potential, b.spoilage_risk, b.application_notes
    FROM organisms o
    LEFT JOIN accessions a ON a.organism_id=o.organism_id
    LEFT JOIN tags t ON t.organism_id=o.organism_id
    LEFT JOIN brewing_relevance b ON b.organism_id=o.organism_id
    LEFT JOIN labs l ON l.lab_id=o.sample_source_lab_id
    WHERE o.tenant_id=?
    GROUP BY o.organism_id ORDER BY o.collection_id"""


def flat_catalog(conn, tid):
    """One row per strain with everything people want in a spreadsheet: the
    core fields, storage, source, accessions/tags, brewing relevance, and one
    column per custom field. Column names are ones the importer recognizes, so
    this file can be re-imported (to move strains between labs, say).
    Returns (column_names, list_of_dicts)."""
    rows = [dict(r) for r in conn.execute(FLAT_CATALOG_SQL, (tid,)).fetchall()]
    cols = [d[0] for d in conn.execute(FLAT_CATALOG_SQL + " LIMIT 0", (tid,)).description] if not rows \
        else list(rows[0].keys())
    fields = conn.execute("SELECT field_id, name FROM custom_fields WHERE tenant_id=? ORDER BY sort_order, name",
                          (tid,)).fetchall()
    if fields:
        values = {}
        for v in conn.execute("""SELECT v.field_id, o.collection_id, v.value FROM custom_field_values v
                                 JOIN organisms o ON o.organism_id=v.organism_id WHERE v.tenant_id=?""", (tid,)):
            values[(v["field_id"], v["collection_id"])] = v["value"]
        for f in fields:
            name = f["name"] if f["name"] not in cols else f"{f['name']} (custom)"
            cols.append(name)
            for r in rows:
                r[name] = values.get((f["field_id"], r["collection_id"]))
    return cols, rows


def build_export_zip():
    """Admin-only export of the caller's own lab -- every table is filtered
    to tenant_id() so one lab's export never carries another lab's rows."""
    conn = db()
    tid = tenant_id()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in ["organisms", "accessions", "plate_images", "models",
                      "brewing_relevance", "tags", "revival_log", "gram_stain_images",
                      "feedback", "labs", "custom_fields", "custom_field_values", "audit_log"]:
            rows = conn.execute(f"SELECT * FROM {table} WHERE tenant_id=?", (tid,)).fetchall()
            cols = rows[0].keys() if rows else []
            s = io.StringIO()
            w = csv.writer(s)
            w.writerow(cols)
            for r in rows:
                w.writerow([_csv_safe(r[c]) for c in cols])
            zf.writestr(f"{table}.csv", s.getvalue())
        cols, rows = flat_catalog(conn, tid)
        s = io.StringIO()
        w = csv.writer(s)
        w.writerow(cols)
        for r in rows:
            w.writerow([_csv_safe(r[c]) for c in cols])
        zf.writestr("catalog_flat.csv", s.getvalue())
    conn.close()
    buf.seek(0)
    return buf


def build_export_xlsx():
    """Admin-only "Excel profile" of the caller's own lab's catalog -- one
    sheet per table, plus the same flat catalog_flat view as the CSV export,
    as a single real .xlsx workbook rather than a zip of CSVs. Every table is
    filtered to tenant_id() so one lab's workbook never carries another
    lab's rows. The users table is left out on purpose -- it holds password
    hashes, and that stays true no matter who's exporting."""
    import openpyxl
    from openpyxl.utils import get_column_letter

    conn = db()
    tid = tenant_id()
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    def _sheet(name, cols, rows):
        ws = wb.create_sheet(title=name[:31])
        ws.append(list(cols))
        for cell in ws[1]:
            cell.font = openpyxl.styles.Font(bold=True)
        for row_no, r in enumerate(rows, start=2):
            for col_no, c in enumerate(cols, start=1):
                value = r[c]
                cell = ws.cell(row=row_no, column=col_no, value=value)
                if isinstance(value, str):
                    cell.data_type = "s"  # text, never a formula, even if it starts with "="
        for i, col in enumerate(cols, start=1):
            width = max(10, min(40, len(col) + 2))
            ws.column_dimensions[get_column_letter(i)].width = width

    for table in ["organisms", "accessions", "plate_images", "models",
                  "brewing_relevance", "tags", "revival_log", "gram_stain_images",
                  "feedback", "labs", "custom_fields", "custom_field_values", "audit_log"]:
        rows = conn.execute(f"SELECT * FROM {table} WHERE tenant_id=?", (tid,)).fetchall()
        cols = rows[0].keys() if rows else [
            r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
        _sheet(table, cols, rows)

    flat_cols, flat_rows = flat_catalog(conn, tid)
    _sheet("catalog_flat", flat_cols, flat_rows)
    conn.close()

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


# --------------------------------------------------------------------------- #
# Routes -- login/logout + admin (accounts, labs)
# --------------------------------------------------------------------------- #
MIN_PASSWORD_LENGTH = 8
_COMMON_PASSWORDS = {"password", "password1", "12345678", "123456789", "qwertyui", "qwerty123",
                     "11111111", "iloveyou", "letmein1", "admin123", "welcome1"}


def _password_problem(password, username=None):
    """None if the password is acceptable, else a sentence telling the person
    what to change. Deliberately modest (length, not the username, not a
    well-known password): sign-in is also rate-limited and accounts lock."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use a password of at least {MIN_PASSWORD_LENGTH} characters."
    if username and password.lower() == username.lower():
        return "The password can't be the same as the username."
    if password.lower() in _COMMON_PASSWORDS:
        return "That password is too common. Pick something less guessable."
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user() is not None:
        return redirect(url_for("home"))
    if request.method == "POST":
        if not app.config.get("TESTING") and _ip_rate_limited(f"login:{_client_ip()}"):
            flash("Too many login attempts from this connection. Wait a few minutes and try again.")
            return redirect(url_for("login"))
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = db()
        user = conn.execute(
            "SELECT * FROM users WHERE username=? AND (is_active=1 OR pending_approval=1)",
            (username,)).fetchone()
        if user is not None and _account_locked(user):
            conn.close()
            flash(f"Too many failed attempts on this account. Try again in a few minutes, "
                 f"or ask your lab admin to reset the password.")
            return redirect(url_for("login"))
        if user is None or not check_password_hash(user["password_hash"], password):
            if user is not None:
                _register_failed_login(conn, user)
            conn.close()
            flash("Incorrect username or password.")
            return redirect(url_for("login"))
        if user["pending_approval"]:
            # Correct password, but a lab admin hasn't approved this person yet.
            conn.close()
            flash("Your request to join this lab is waiting for an admin to approve it. "
                  "Ask your lab admin to approve you (Admin page > Join requests), then log in again.")
            return redirect(url_for("login"))
        _register_successful_login(conn, user)
        conn.close()
        session.clear()
        session["user_id"] = user["user_id"]
        session["stamp"] = user["session_stamp"] or ""
        dest = session.pop("login_next", None)
        return redirect(dest or url_for("home"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.")
    return redirect(url_for("login"))


@app.route("/setup", methods=["GET", "POST"])
def setup():
    """First run only: name the lab and create its first admin account. Needs the
    one-time setup code printed in the terminal (and saved in the data folder)."""
    if lab_exists():
        return redirect(url_for("login"))
    if request.method == "POST":
        if not app.config.get("TESTING") and _ip_rate_limited(f"setup:{_client_ip()}"):
            flash("Too many attempts from this connection. Wait a few minutes and try again.")
            return redirect(url_for("setup"))
        code = _normalize_join_code(request.form.get("setup_code", ""))
        lab_name = request.form.get("lab_name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        full_name = request.form.get("full_name", "").strip() or None
        if not secrets.compare_digest(code, _normalize_join_code(setup_code())):
            flash("That setup code isn't right. It is shown in the window where the app was started, "
                  "and saved as setup-code.txt in the app's data folder.")
            return redirect(url_for("setup"))
        if not lab_name or not username or not password:
            flash("Lab name, username and password are all needed.")
            return redirect(url_for("setup"))
        problem = _password_problem(password, username)
        if problem:
            flash(problem)
            return redirect(url_for("setup"))
        conn = db()
        if lab_exists(conn):            # someone finished setup a moment ago
            conn.close()
            return redirect(url_for("login"))
        uid = create_lab(conn, lab_name, username, password, full_name,
                         include_examples=bool(request.form.get("include_examples")))
        conn.close()
        try:
            SETUP_CODE_FILE.unlink()
        except OSError:
            pass
        session.clear()
        session["user_id"] = uid
        session["stamp"] = ""
        flash(f"\"{lab_name}\" is set up and you are its admin. Colleagues can ask to join from the "
              "sign-in page with the join code on the Admin page.")
        return redirect(url_for("home"))
    return render_template("setup.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    """Ask to join the lab. Needs the lab's join code; an admin then approves the
    request before the new account can sign in."""
    if current_user() is not None:
        return redirect(url_for("home"))
    if request.method == "POST":
        if not app.config.get("TESTING") and _ip_rate_limited(f"signup:{_client_ip()}"):
            flash("Too many attempts from this connection. Wait a few minutes and try again.")
            return redirect(url_for("signup"))
        join_code = _normalize_join_code(request.form.get("join_code", ""))
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        full_name = request.form.get("full_name", "").strip() or None
        if not join_code or not username or not password:
            flash("The join code, a username and a password are all needed.")
            return redirect(url_for("signup"))
        problem = _password_problem(password, username)
        if problem:
            flash(problem)
            return redirect(url_for("signup"))
        conn = db()
        target = conn.execute("SELECT * FROM tenants WHERE join_code=?", (join_code,)).fetchone()
        if target is None:
            conn.close()
            flash("That join code wasn't recognised. Your lab admin can find it on the Admin page.")
            return redirect(url_for("signup"))
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            conn.close()
            flash(f"\"{username}\" is already taken. Choose another username.")
            return redirect(url_for("signup"))
        conn.execute(
            "INSERT INTO users (tenant_id, username, password_hash, role, full_name, is_active, pending_approval) "
            "VALUES (?, ?, ?, 'user', ?, 0, 1)",
            (target["tenant_id"], username, generate_password_hash(password), full_name))
        conn.commit()
        conn.close()
        flash("Request sent. A lab admin has to approve it before you can sign in "
              "with the username and password you just chose.")
        return redirect(url_for("login"))
    return render_template("signup.html")


@app.route("/admin")
@admin_required
def admin_home():
    tid = tenant_id()
    conn = db()
    users = conn.execute("SELECT * FROM users WHERE tenant_id=? AND pending_approval=0 ORDER BY username",
                         (tid,)).fetchall()
    pending = conn.execute("SELECT * FROM users WHERE tenant_id=? AND pending_approval=1 ORDER BY user_id",
                           (tid,)).fetchall()
    labs = conn.execute("SELECT * FROM labs WHERE tenant_id=? ORDER BY name", (tid,)).fetchall()
    _ensure_join_codes(conn)
    code_row = conn.execute("SELECT join_code FROM tenants WHERE tenant_id=?", (tid,)).fetchone()
    messages = conn.execute("SELECT * FROM contact_messages ORDER BY is_read ASC, message_id DESC "
                            "LIMIT 200").fetchall()
    conn.close()
    return render_template("admin.html", users=users, labs=labs, pending=pending,
                           messages=messages, category_labels=dict(CONTACT_CATEGORIES),
                           unread_messages=sum(1 for m in messages if not m["is_read"]),
                           join_code=_show_join_code(code_row["join_code"] if code_row else ""),
                           role_choices=USER_ROLE_CHOICES, tenant=current_tenant(),
                           custom_fields=get_custom_fields())


@app.route("/admin/lab-name", methods=["POST"])
@admin_required
def admin_rename_tenant():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Lab name can't be empty.")
        return redirect(url_for("admin_home"))
    conn = db()
    conn.execute("UPDATE tenants SET name=? WHERE tenant_id=?", (name, tenant_id()))
    conn.commit()
    conn.close()
    flash("Lab name updated.")
    return redirect(url_for("admin_home"))


@app.route("/admin/photo-storage", methods=["POST"])
@admin_required
def admin_photo_storage():
    """Store large photos compactly (the default) or exactly as uploaded."""
    keep = 1 if request.form.get("keep_original_photos") == "1" else 0
    conn = db()
    before = conn.execute("SELECT keep_original_photos FROM tenants WHERE tenant_id=?",
                          (tenant_id(),)).fetchone()[0]
    if before != keep:
        conn.execute("UPDATE tenants SET keep_original_photos=? WHERE tenant_id=?", (keep, tenant_id()))
        audit(conn, "update", "lab", tenant_id(), {"keep_original_photos": [before, keep]})
        conn.commit()
    conn.close()
    flash("New photos will be stored exactly as uploaded." if keep else
          "New photos will be turned upright and stored in less space.")
    return redirect(url_for("admin_home") + "#photo-storage")


@app.route("/admin/join-requests/<int:user_id>/<action>", methods=["POST"])
@admin_required
def admin_join_request(user_id, action):
    if action not in ("approve", "decline"):
        abort(404)
    conn = db()
    target = conn.execute("SELECT * FROM users WHERE user_id=? AND tenant_id=? AND pending_approval=1",
                          (user_id, tenant_id())).fetchone()
    if target is None:
        conn.close()
        abort(404)
    if action == "approve":
        conn.execute("UPDATE users SET is_active=1, pending_approval=0 WHERE user_id=?", (user_id,))
        flash(f"Approved \"{target['username']}\". They can log in now as a regular user.")
    else:
        conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        flash(f"Declined \"{target['username']}\". Their request was removed.")
    conn.commit()
    conn.close()
    return redirect(url_for("admin_home"))


@app.route("/admin/join-code/regenerate", methods=["POST"])
@admin_required
def admin_regenerate_join_code():
    conn = db()
    conn.execute("UPDATE tenants SET join_code=? WHERE tenant_id=?", (_new_join_code(), tenant_id()))
    conn.commit()
    conn.close()
    flash("New join code created. The old one no longer works; people who already joined are unaffected.")
    return redirect(url_for("admin_home"))


@app.route("/admin/labs", methods=["POST"])
@admin_required
def admin_add_lab():
    name = request.form.get("name", "").strip()
    if name:
        conn = db()
        try:
            conn.execute("INSERT INTO labs (tenant_id, name) VALUES (?, ?)", (tenant_id(), name))
            conn.commit()
        except sqlite3.IntegrityError:
            flash(f"\"{name}\" is already in your source-labs list.")
        conn.close()
    return redirect(url_for("admin_home"))


# --------------------------------------------------------------------------- #
# Bulk import (admin only): upload an existing strain list, check it on a
# preview page, then import the rows you keep. The uploaded file is parsed once
# and held as JSON under DATA_DIR/imports until the import is confirmed.
# --------------------------------------------------------------------------- #
IMPORT_DIR = DATA_DIR / "imports"
_IMPORT_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")


def _import_path(token):
    if not _IMPORT_TOKEN_RE.match(token or ""):
        abort(404)
    return IMPORT_DIR / f"{token}.json"


def _load_import(token):
    """Staged import for this admin's lab only; 404 for anyone else's token."""
    path = _import_path(token)
    if not path.exists():
        abort(404)
    staged = json.loads(path.read_text())
    if staged.get("tenant_id") != tenant_id():
        abort(404)
    return staged


def _cleanup_old_imports(max_age_hours=24):
    if not IMPORT_DIR.exists():
        return
    cutoff = datetime.now().timestamp() - max_age_hours * 3600
    for f in IMPORT_DIR.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def _existing_for_dupes(conn, tid):
    acc = {(r["culture_collection"].lower(), r["accession_number"].lower()): r["collection_id"]
           for r in conn.execute(
               """SELECT a.culture_collection, a.accession_number, o.collection_id
                  FROM accessions a JOIN organisms o ON o.organism_id=a.organism_id
                  WHERE a.tenant_id=?""", (tid,))}
    strains = {(r["genus"].lower(), r["species"].lower(), (r["strain"] or "").lower()): r["collection_id"]
               for r in conn.execute(
                   "SELECT genus, species, strain, collection_id FROM organisms WHERE tenant_id=?", (tid,))}
    return acc, strains


def _import_custom_defs():
    """This lab's custom fields keyed the way the importer expects: cf_<id>."""
    return {f"cf_{f['field_id']}": f for f in get_custom_fields()}


def _mapping_from_form(form, ncols):
    valid = set(importer.FIELDS) | set(_import_custom_defs())
    mapping, used = [], set()
    for i in range(ncols):
        key = form.get(f"map_{i}", "").strip()
        if key in valid and key not in used:
            used.add(key)
            mapping.append(key)
        else:
            mapping.append(None)
    return mapping


def _render_import_preview(token, staged, mapping):
    cdefs = _import_custom_defs()
    records = importer.build_records(staged["rows"], mapping, cdefs)
    conn = db()
    acc, strains = _existing_for_dupes(conn, staged["tenant_id"])
    conn.close()
    importer.mark_duplicates(records, acc, strains)
    counts = dict(ok=sum(1 for r in records if not r["errors"] and not r["duplicate_of"]),
                  errors=sum(1 for r in records if r["errors"]),
                  dupes=sum(1 for r in records if r["duplicate_of"] and not r["errors"]))
    examples = []
    for i in range(len(staged["headers"])):
        ex = next((row[i] for row in staged["rows"] if i < len(row) and row[i]), "")
        examples.append(ex[:40])
    return render_template("import_preview.html", token=token, filename=staged["filename"],
                           headers=staged["headers"], mapping=mapping, fields=importer.FIELDS,
                           custom_defs=cdefs, examples=examples,
                           records=records, counts=counts,
                           has_name=bool({"genus", "species"} <= set(mapping) or "full_name" in mapping))


@app.route("/admin/import", methods=["GET", "POST"])
@admin_required
def admin_import():
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Choose a .csv or .xlsx file first.")
            return redirect(url_for("admin_import"))
        try:
            headers, rows = importer.parse_file(f.filename, f.read())
        except importer.ImportFileError as e:
            flash(str(e))
            return redirect(url_for("admin_import"))
        IMPORT_DIR.mkdir(parents=True, exist_ok=True)
        _cleanup_old_imports()
        token = secrets.token_urlsafe(24)
        _import_path(token).write_text(json.dumps(
            dict(tenant_id=tenant_id(), filename=secure_filename(f.filename) or "import",
                 headers=headers, rows=rows)))
        return redirect(url_for("admin_import_preview", token=token))
    return render_template("import.html")


@app.route("/admin/import/template.csv")
@admin_required
def admin_import_template():
    return Response(importer.template_csv(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=strain-import-template.csv"})


@app.route("/admin/import/<token>", methods=["GET", "POST"])
@admin_required
def admin_import_preview(token):
    staged = _load_import(token)
    ncols = len(staged["headers"])
    if request.method == "GET":
        cmap = {k: f["name"] for k, f in _import_custom_defs().items()}
        return _render_import_preview(token, staged, importer.auto_map(staged["headers"], cmap))

    mapping = _mapping_from_form(request.form, ncols)
    if request.form.get("action") != "commit":
        return _render_import_preview(token, staged, mapping)

    chosen = {int(x) for x in request.form.getlist("row") if x.isdigit()}
    cdefs = _import_custom_defs()
    records = importer.build_records(staged["rows"], mapping, cdefs)
    conn = db()
    tid = staged["tenant_id"]
    acc, strains = _existing_for_dupes(conn, tid)
    importer.mark_duplicates(records, acc, strains)
    # Only rows the admin ticked, and never rows with errors or known duplicates.
    todo = [r for r in records if r["row_number"] in chosen and not r["errors"] and not r["duplicate_of"]]
    if not todo:
        conn.close()
        flash("No rows were selected to import.")
        return redirect(url_for("admin_import_preview", token=token))

    # Look this up before writing anything: current_user() opens its own database
    # connection, and a second connection can't read while this one holds a large
    # uncommitted write ("database is locked").
    importing_user = current_user()["username"]
    counters = {}   # collection-ID prefix -> highest number used so far (existing + this import)
    labs = {r["name"].lower(): r["lab_id"]
            for r in conn.execute("SELECT lab_id, name FROM labs WHERE tenant_id=?", (tid,))}
    new_labs = 0
    try:
        for rec in todo:
            v = rec["values"]
            begin_write(conn)
            prefix = _code(v["genus"], v["species"])
            if prefix not in counters:
                nums = [int(r["collection_id"].rsplit("-", 1)[1]) for r in conn.execute(
                    "SELECT collection_id FROM organisms WHERE tenant_id=? AND collection_id LIKE ?",
                    (tid, prefix + "-%")) if r["collection_id"].rsplit("-", 1)[1].isdigit()]
                counters[prefix] = max(nums, default=0)
            counters[prefix] += 1
            cid = f"{prefix}-{counters[prefix]:03d}"

            source_type = lab_id = online_ref = None
            if v["source_lab"]:
                lab_id = labs.get(v["source_lab"].lower())
                if lab_id is None:
                    lab_id = conn.execute("INSERT INTO labs (tenant_id, name) VALUES (?,?)",
                                          (tid, v["source_lab"])).lastrowid
                    labs[v["source_lab"].lower()] = lab_id
                    new_labs += 1
                source_type = "lab"
            elif v["online_reference"]:
                source_type, online_ref = "online", v["online_reference"]

            ncbi = int(v["ncbi_taxonomy_id"]) if (v["ncbi_taxonomy_id"] or "").isdigit() else None
            cur = conn.execute("""INSERT INTO organisms
                (tenant_id, collection_id, genus, species, strain, full_name, ncbi_taxonomy_id, notes,
                 collection_type, gram_stain, added_by, viability_category, revival_interval_days,
                 last_revival_date, media, oxygen_requirement, optimal_temp_c, morphology_notes,
                 storage_location, storage_format, storage_temp_c, date_stored,
                 sample_source, sample_source_type, sample_source_lab_id,
                 storage_freezer, storage_rack, storage_box, storage_position)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (tid, cid, v["genus"], v["species"], v["strain"], v["full_name"], ncbi, v["notes"],
                 v["collection_type"], v["gram_stain"], v["added_by"] or importing_user,
                 v["viability_category"], v["revival_interval_days"], v["last_revival_date"],
                 v["media"], v["oxygen_requirement"], v["optimal_temp_c"], v["morphology_notes"],
                 v["storage_location"], v["storage_format"], v["storage_temp_c"], v["date_stored"],
                 online_ref, source_type, lab_id,
                 v["storage_freezer"], v["storage_rack"], v["storage_box"], v["storage_position"]))
            oid = cur.lastrowid
            # an imported last-revival date is the baseline the in-app log builds on
            conn.execute("UPDATE organisms SET revival_baseline_date=last_revival_date WHERE organism_id=?", (oid,))
            for key, val in rec["custom"].items():
                conn.execute("""INSERT INTO custom_field_values (organism_id, field_id, tenant_id, value)
                                VALUES (?,?,?,?)""", (oid, int(key[3:]), tid, val))
            for cc, num in rec["accessions"]:
                conn.execute("""INSERT OR IGNORE INTO accessions
                    (organism_id, tenant_id, culture_collection, accession_number) VALUES (?,?,?,?)""",
                             (oid, tid, cc, num))
            for tag in dict.fromkeys(rec["tags"]):
                conn.execute("INSERT OR IGNORE INTO tags (organism_id, tenant_id, tag) VALUES (?,?,?)",
                             (oid, tid, tag))
            conn.execute("""INSERT INTO brewing_relevance (organism_id, tenant_id, role, spoilage_risk)
                            VALUES (?,?,?,?)""", (oid, tid, v["role"], v["spoilage_risk"]))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        conn.close()
        app.logger.exception("bulk import failed")
        flash("The import failed and nothing was added. Check the file and try again.")
        return redirect(url_for("admin_import_preview", token=token))
    conn.close()
    try:
        _import_path(token).unlink()
    except OSError:
        pass
    msg = f"Imported {len(todo)} strain{'s' if len(todo) != 1 else ''}."
    if new_labs:
        msg += f" Added {new_labs} new source lab{'s' if new_labs != 1 else ''}."
    flash(msg)
    return redirect(url_for("catalog"))


@app.route("/admin/fields", methods=["POST"])
@admin_required
def admin_add_field():
    name = " ".join(request.form.get("name", "").split())[:60]
    ftype = request.form.get("field_type", "text")
    choices = [c.strip() for c in request.form.get("choices", "").replace(",", "\n").split("\n") if c.strip()]
    if not name:
        flash("Give the field a name.")
    elif ftype not in importer.CUSTOM_TYPES:
        flash("Pick a field type.")
    elif ftype == "choice" and len(choices) < 2:
        flash("A choice field needs at least two options (comma or line separated).")
    else:
        tid = tenant_id()
        conn = db()
        n = conn.execute("SELECT COUNT(*) FROM custom_fields WHERE tenant_id=?", (tid,)).fetchone()[0]
        if n >= MAX_CUSTOM_FIELDS:
            flash(f"A lab can have up to {MAX_CUSTOM_FIELDS} custom fields.")
        else:
            try:
                conn.execute("""INSERT INTO custom_fields (tenant_id, name, field_type, choices, sort_order)
                                VALUES (?,?,?,?,?)""",
                             (tid, name, ftype, "\n".join(choices) if ftype == "choice" else None, n))
                conn.commit()
                flash(f"Added the field \"{name}\". It now appears on every strain's add and edit form.")
            except sqlite3.IntegrityError:
                flash(f"You already have a field called \"{name}\".")
        conn.close()
    return redirect(url_for("admin_home"))


@app.route("/admin/fields/<int:field_id>/delete", methods=["POST"])
@admin_required
def admin_delete_field(field_id):
    conn = db()
    conn.execute("DELETE FROM custom_field_values WHERE field_id=? AND tenant_id=?", (field_id, tenant_id()))
    conn.execute("DELETE FROM custom_fields WHERE field_id=? AND tenant_id=?", (field_id, tenant_id()))
    conn.commit()
    conn.close()
    flash("Field removed, along with the values recorded for it.")
    return redirect(url_for("admin_home"))


@app.route("/admin/labs/<int:lab_id>/delete", methods=["POST"])
@admin_required
def admin_delete_lab(lab_id):
    conn = db()
    conn.execute("DELETE FROM labs WHERE lab_id=? AND tenant_id=?", (lab_id, tenant_id()))
    conn.commit()
    conn.close()
    flash("Source lab removed. Strains already recorded against it keep showing its name.")
    return redirect(url_for("admin_home"))


@app.route("/admin/users", methods=["POST"])
@admin_required
def admin_add_user():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "user").strip()
    full_name = request.form.get("full_name", "").strip() or None
    if role not in USER_ROLE_CHOICES:
        role = "user"
    if username and password and _password_problem(password, username):
        flash(_password_problem(password, username))
        return redirect(url_for("admin_home"))
    if not username or not password:
        flash("Username and password are both required.")
        return redirect(url_for("admin_home"))
    conn = db()
    try:
        conn.execute(
            "INSERT INTO users (tenant_id, username, password_hash, role, full_name) VALUES (?,?,?,?,?)",
            (tenant_id(), username, generate_password_hash(password), role, full_name))
        conn.commit()
        flash(f"Added {role} account \"{username}\".")
    except sqlite3.IntegrityError:
        flash(f"\"{username}\" is already taken. Usernames are shared across every lab on this install.")
    conn.close()
    return redirect(url_for("admin_home"))


@app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def admin_toggle_user(user_id):
    tid = tenant_id()
    conn = db()
    target = conn.execute("SELECT * FROM users WHERE user_id=? AND tenant_id=?",
                          (user_id, tid)).fetchone()
    if target is None:
        conn.close()
        abort(404)
    if target["is_active"] and target["role"] == "admin":
        active_admins = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE tenant_id=? AND role='admin' AND is_active=1",
            (tid,)).fetchone()["c"]
        if active_admins <= 1:
            conn.close()
            flash("Can't deactivate the last active admin account in this lab.")
            return redirect(url_for("admin_home"))
    conn.execute("UPDATE users SET is_active = 1 - is_active WHERE user_id=? AND tenant_id=?",
                (user_id, tid))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_home"))


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def admin_reset_password(user_id):
    password = request.form.get("password", "")
    if not password:
        flash("Enter a new password.")
        return redirect(url_for("admin_home"))
    conn = db()
    target = conn.execute("SELECT * FROM users WHERE user_id=? AND tenant_id=?",
                         (user_id, tenant_id())).fetchone()
    if target is None:
        conn.close()
        abort(404)
    problem = _password_problem(password, target["username"])
    if problem:
        conn.close()
        flash(problem)
        return redirect(url_for("admin_home"))
    me = current_user()
    _pwreset.set_password(conn, user_id, generate_password_hash(password), keep_session=(user_id == me["user_id"]))
    _pwreset.record_set(conn, target, me["user_id"])
    _pwreset._audit(conn, "password_set_by_admin", target, {})
    conn.commit()
    conn.close()
    flash("Password updated. Tell the person in person; any sign-ins they had have ended.")
    return redirect(url_for("admin_home"))


@app.route("/account", methods=["GET", "POST"])
def account():
    """Anyone signed in can change their own password (no admin needed)."""
    user = current_user()
    if request.method == "POST":
        if not app.config.get("TESTING") and _ip_rate_limited(f"pw:{user['user_id']}"):
            flash("Too many attempts. Wait a few minutes and try again.")
            return redirect(url_for("account"))
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        if not check_password_hash(user["password_hash"], current):
            flash("Your current password isn't right.")
            return redirect(url_for("account"))
        if new != request.form.get("confirm_password", ""):
            flash("The two new passwords don't match.")
            return redirect(url_for("account"))
        problem = _password_problem(new, user["username"])
        if problem:
            flash(problem)
            return redirect(url_for("account"))
        conn = db()
        _pwreset.set_password(conn, user["user_id"], generate_password_hash(new), keep_session=True)
        conn.commit()
        conn.close()
        flash("Password changed. Any other sign-ins to your account have ended.")
        return redirect(url_for("account"))
    return render_template("account.html", min_length=MIN_PASSWORD_LENGTH)


# --------------------------------------------------------------------------- #
# Routes -- landing + catalog
# --------------------------------------------------------------------------- #
@app.route("/")
def home():
    conn = db()
    tid = tenant_id()
    stats = dict(
        species=conn.execute("SELECT COUNT(*) c FROM organisms WHERE tenant_id=?", (tid,)).fetchone()["c"],
        library=conn.execute("SELECT COUNT(*) c FROM organisms WHERE tenant_id=? AND collection_type='library'", (tid,)).fetchone()["c"],
        validation=conn.execute("SELECT COUNT(*) c FROM organisms WHERE tenant_id=? AND collection_type='validation'", (tid,)).fetchone()["c"],
        models=conn.execute("SELECT COUNT(*) c FROM models WHERE tenant_id=?", (tid,)).fetchone()["c"],
        images=conn.execute("SELECT COUNT(*) c FROM plate_images WHERE tenant_id=? AND voided_at IS NULL", (tid,)).fetchone()["c"],
    )
    recent = conn.execute(
        "SELECT * FROM organisms WHERE tenant_id=? ORDER BY organism_id DESC LIMIT 4", (tid,)
    ).fetchall()

    periodic = conn.execute(
        "SELECT * FROM organisms WHERE tenant_id=? AND viability_category='periodic'",
        (tid,)
    ).fetchall()
    conn.close()

    needs_revival = []
    for org in periodic:
        if not org["revival_interval_days"]:
            # periodic but no interval: it can never come due, so say so
            status = dict(status="nointerval", due_date=None, days_overdue=0, days_until_due=0)
            needs_revival.append(dict(org=org, status=status,
                                      badge=dict(cls="b-warn", color="amber", label="periodic, but no interval set"),
                                      message=f"{org['full_name']} is marked periodic but has no revival interval."))
            continue
        status = org_revival_status(org)
        badge = revival_badge(status)
        # surface overdue/due (red) and the amber "due soon" early-warning
        # window, but not strains that are comfortably not due yet (green)
        if badge["color"] in ("red", "amber"):
            needs_revival.append(dict(org=org, status=status, badge=badge,
                                      message=revival_notification_message(
                                          org["full_name"], status["status"],
                                          status["due_date"], status["days_overdue"])))
    # red first (most overdue first), then amber (soonest due first)
    needs_revival.sort(key=lambda r: (
        0 if r["badge"]["color"] == "red" else 1,
        0 if r["status"]["status"] in ("failed", "nobaseline") else 1,
        -r["status"]["days_overdue"],
        r["status"]["days_until_due"],
    ))

    return render_template("home.html", stats=stats, recent=recent, needs_revival=needs_revival)


@app.route("/catalog")
def catalog():
    q = request.args.get("q", "").strip().lower()
    genus = request.args.get("genus", "").strip()
    role = request.args.get("role", "").strip()
    ctype = request.args.get("type", "").strip()
    gram = request.args.get("gram", "").strip()
    orgs = get_organisms()
    result = []
    conn = db()
    tid = tenant_id()
    # Everything searchable that lives outside the organisms row, loaded in a
    # handful of queries (not one per strain).
    extra, roles_by_org = {}, {}
    for r in conn.execute("SELECT organism_id, culture_collection || ' ' || accession_number AS t "
                          "FROM accessions WHERE tenant_id=?", (tid,)):
        extra.setdefault(r["organism_id"], []).append(r["t"])
    for r in conn.execute("SELECT organism_id, tag AS t FROM tags WHERE tenant_id=?", (tid,)):
        extra.setdefault(r["organism_id"], []).append(r["t"])
    for r in conn.execute("SELECT organism_id, value AS t FROM custom_field_values WHERE tenant_id=?", (tid,)):
        extra.setdefault(r["organism_id"], []).append(r["t"])
    for r in conn.execute("SELECT o.organism_id, l.name AS t FROM organisms o JOIN labs l "
                          "ON l.lab_id=o.sample_source_lab_id WHERE o.tenant_id=?", (tid,)):
        extra.setdefault(r["organism_id"], []).append(r["t"])
    for r in conn.execute("SELECT organism_id, role FROM brewing_relevance WHERE tenant_id=?", (tid,)):
        roles_by_org[r["organism_id"]] = r["role"]
    for o in orgs:
        if genus and o["genus"] != genus:
            continue
        if ctype and o["collection_type"] != ctype:
            continue
        if gram and o["gram_stain"] != gram:
            continue
        if role and roles_by_org.get(o["organism_id"]) != role:
            continue
        if q:
            hay = " ".join([str(o[c]) for c in ("collection_id", "full_name", "genus", "species", "strain", "notes",
                                                "storage_freezer", "storage_rack", "storage_box",
                                                "storage_position", "storage_location", "media")
                            if o[c] is not None] + extra.get(o["organism_id"], [])).lower()
            # every word must appear somewhere ("freezer 2 ale" finds the ale strain in freezer 2)
            if not all(word in hay for word in q.split()):
                continue
        result.append(o)
    genera = [r["genus"] for r in conn.execute(
        "SELECT DISTINCT genus FROM organisms WHERE tenant_id=? ORDER BY genus", (tid,))]
    roles = [r["role"] for r in conn.execute(
        "SELECT DISTINCT role FROM brewing_relevance WHERE tenant_id=? AND role IS NOT NULL ORDER BY role", (tid,))]
    stats = dict(
        species=conn.execute("SELECT COUNT(*) c FROM organisms WHERE tenant_id=?", (tid,)).fetchone()["c"],
        models=conn.execute("SELECT COUNT(*) c FROM models WHERE tenant_id=?", (tid,)).fetchone()["c"],
        print_ready=conn.execute("SELECT COUNT(*) c FROM models WHERE tenant_id=? AND is_print_ready=1", (tid,)).fetchone()["c"],
        images=conn.execute("SELECT COUNT(*) c FROM plate_images WHERE tenant_id=? AND voided_at IS NULL", (tid,)).fetchone()["c"],
    )
    conn.close()
    return render_template("index.html", orgs=result, genera=genera, roles=roles,
                           stats=stats, q=q, sel_genus=genus, sel_role=role, sel_type=ctype,
                           sel_gram=gram, type_choices=COLLECTION_TYPE_CHOICES,
                           gram_choices=GRAM_CHOICES)


# --------------------------------------------------------------------------- #
# Routes -- species detail / CRUD
# --------------------------------------------------------------------------- #
@app.route("/species/<cid>")
def species(cid):
    data = get_organism(cid)
    if data is None:
        abort(404)
    conn = db()
    shape_in = _shape_input(conn, data["org"])
    conn.close()
    cell = resolve_shape_config(shape_in)["cell"]
    auto_kind = resolve_shape_config(dict(shape_in, cell_shape=None,
                                          cell_arrangement=None))["cell"]["kind"]
    basis = shapes.BASIS_TEXT.get(cell.get("basis"), "")
    if cell.get("basis") == "gram_record" and cell.get("basis_date"):
        basis = f"as recorded with the Gram stain of {cell['basis_date']}"
    return render_template("species.html", auto_kind=auto_kind, cid=cid, role_choices=ROLE_CHOICES,
                           gram_choices=GRAM_CHOICES,
                           cell=cell, cell_text=shapes.describe_cell(cell),
                           cell_basis=basis, describe_cell=shapes.describe_cell,
                           shape_labels=shapes.SHAPE_LABELS,
                           arrangement_labels=shapes.ARRANGEMENT_LABELS,
                           arrangements_for=shapes.ARRANGEMENTS_FOR, plate_sizes=PLATE_SIZES_MM,
                           today=date.today().isoformat(), **data)


# --------------------------------------------------------------------------- #
# Storage map and printable labels
# --------------------------------------------------------------------------- #
def _natural_key(text):
    """A2 < A10 < B1 (so box positions sort the way people read them)."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text or "")]


@app.route("/storage")
def storage_map():
    """Where everything lives: strains grouped freezer > rack > box, each box
    listing its positions. Flags two strains recorded in the same position."""
    tid = tenant_id()
    flt_freezer = request.args.get("freezer", "").strip()
    flt_box = request.args.get("box", "").strip()
    conn = db()
    rows = conn.execute("""SELECT collection_id, full_name, strain, storage_freezer, storage_rack,
                                  storage_box, storage_position, storage_location, viability_category
                           FROM organisms WHERE tenant_id=? ORDER BY collection_id""", (tid,)).fetchall()
    conn.close()
    tree, unplaced, conflicts = {}, [], 0
    seen = {}
    for r in rows:
        if not (r["storage_freezer"] or r["storage_rack"] or r["storage_box"] or r["storage_position"]):
            unplaced.append(r)
            continue
        if flt_freezer and (r["storage_freezer"] or "") != flt_freezer:
            continue
        if flt_box and (r["storage_box"] or "") != flt_box:
            continue
        key = (r["storage_freezer"] or "(no freezer)", r["storage_rack"] or "", r["storage_box"] or "(no box)")
        if r["storage_position"]:
            pk = tuple(x.lower() for x in key) + (r["storage_position"].lower(),)
            seen.setdefault(pk, []).append(r["collection_id"])
        tree.setdefault(key, []).append(r)
    clash = {cid for ids in seen.values() if len(ids) > 1 for cid in ids}
    conflicts = sum(1 for ids in seen.values() if len(ids) > 1)
    boxes = []
    for key in sorted(tree, key=lambda k: tuple(_natural_key(x) for x in k)):
        entries = sorted(tree[key], key=lambda r: _natural_key(r["storage_position"] or "~"))
        boxes.append(dict(freezer=key[0], rack=key[1], box=key[2], entries=entries))
    return render_template("storage.html", boxes=boxes, unplaced=unplaced, clash=clash,
                           conflicts=conflicts, flt_freezer=flt_freezer, flt_box=flt_box,
                           total=len(rows))


def _qr_svg(url):
    if segno is None:
        return None
    from markupsafe import Markup
    return Markup(segno.make(url, error="m").svg_inline(scale=3, border=0, dark="#000", light=None, omitsize=True))


@app.route("/labels")
def label_picker():
    """Choose strains to print labels for, then open the print sheet."""
    return render_template("label_picker.html", orgs=get_organisms(),
                           flt_freezer=request.args.get("freezer", "").strip(),
                           flt_box=request.args.get("box", "").strip())


@app.route("/labels/print")
def print_labels():
    """A sheet of labels: QR code (links to the strain's page), ID, name,
    accessions and storage position. Scoped to this lab's own strains."""
    cids = list(dict.fromkeys(request.args.getlist("cid")))[:300]
    if not cids:
        flash("Pick at least one strain to print a label for.")
        return redirect(url_for("label_picker"))
    tid = tenant_id()
    conn = db()
    marks = ",".join("?" * len(cids))
    orgs = conn.execute(f"SELECT * FROM organisms WHERE tenant_id=? AND collection_id IN ({marks}) "
                        f"ORDER BY collection_id", (tid, *cids)).fetchall()
    accs = {}
    for a in conn.execute(f"""SELECT o.collection_id, a.culture_collection, a.accession_number
                              FROM accessions a JOIN organisms o ON o.organism_id=a.organism_id
                              WHERE o.tenant_id=? AND o.collection_id IN ({marks})""", (tid, *cids)):
        accs.setdefault(a["collection_id"], []).append(f"{a['culture_collection']} {a['accession_number']}")
    conn.close()
    if not orgs:
        abort(404)
    base = (os.environ.get("PUBLIC_BASE_URL") or request.url_root).rstrip("/")
    qr_blocked = False
    if not os.environ.get("PUBLIC_BASE_URL"):
        here = urlparse(base)
        host = (here.hostname or "").lower()
        if host in ("localhost", "::1") or host.startswith("127."):
            # Printed from the host computer itself: a phone can't open "localhost",
            # so the codes use this computer's address on the lab network instead.
            lan = get_lan_ip()
            if lan.startswith("127."):
                qr_blocked = True          # no network address to point to
            else:
                base = f"{here.scheme}://{lan}" + (f":{here.port}" if here.port else "")
    labels = [dict(cid=o["collection_id"], name=o["full_name"], strain=o["strain"],
                   accessions="; ".join(accs.get(o["collection_id"], [])[:3]),
                   where=storage_summary(o),
                   qr=None if qr_blocked else _qr_svg(
                       f"{base}{url_for('label_redirect', tid=tid, cid=o['collection_id'])}"))
              for o in orgs]
    size = request.args.get("size", "standard")
    if size not in ("small", "standard", "large"):
        size = "standard"
    tenant = current_tenant()
    return render_template("labels.html", labels=labels, size=size, base=base,
                           lab_name=tenant["name"] if tenant else "", cids=[l["cid"] for l in labels],
                           has_qr=segno is not None, qr_blocked=qr_blocked,
                           public_url_set=bool(os.environ.get("PUBLIC_BASE_URL")))


@app.route("/s/<int:tid>/<cid>")
def label_redirect(tid, cid):
    """Where a printed QR label points. Opens the strain only for members of the
    lab that printed it; anyone else gets the ordinary not-found page."""
    if tenant_id() != tid:
        abort(404)
    return redirect(url_for("species", cid=cid))


@app.route("/species/<cid>/generate", methods=["POST"])
def generate(cid):
    data = get_organism(cid)
    if data is None:
        abort(404)
    n = generate_models_for(data["org"])
    flash(f"Generated {n} procedural models for {data['org']['full_name']}." if n else
          f"Couldn't generate models for {data['org']['full_name']}.")
    return redirect(url_for("species", cid=cid))


@app.route("/species/<cid>/cell-shape", methods=["POST"])
def set_cell_shape(cid):
    """Choose the cell shape and arrangement for the 3D cell model, or go back to
    the automatic choice, then rebuild the procedural models."""
    data = get_organism(cid)
    if data is None:
        abort(404)
    org = data["org"]
    kind = request.form.get("cell_shape", "").strip() or None
    arr = request.form.get("cell_arrangement", "").strip() or None
    if kind is not None and kind not in shapes.CELL_DIMENSIONS:
        abort(400)
    if arr is not None and arr not in dict(shapes.ARRANGEMENT_LABELS):
        abort(400)
    if kind and arr and arr not in shapes.ARRANGEMENTS_FOR[kind]:
        flash(f"{dict(shapes.ARRANGEMENT_LABELS)[arr]} don't go with "
              f"{dict(shapes.SHAPE_LABELS)[kind].split(' (')[0].lower()} cells. Nothing was changed.")
        return redirect(url_for("species", cid=cid) + "#cell-shape")
    conn = db()
    before = (org["cell_shape"], org["cell_arrangement"])
    if before != (kind, arr):
        conn.execute("UPDATE organisms SET cell_shape=?, cell_arrangement=? WHERE organism_id=?",
                     (kind, arr, org["organism_id"]))
        audit(conn, "update", "organism", org["organism_id"],
              {"cell_shape": [before[0], kind], "cell_arrangement": [before[1], arr]})
        conn.commit()
    conn.close()
    fresh = get_organism(cid)["org"]
    generate_models_for(fresh)
    conn = db()
    cell = resolve_shape_config(_shape_input(conn, fresh))["cell"]
    conn.close()
    flash(f"Cell model rebuilt: {shapes.describe_cell(cell)}.")
    return redirect(url_for("species", cid=cid) + "#cell-shape")


@app.route("/species/<cid>/rebuild/<int:image_id>", methods=["POST"])
def build_plate_replica(cid, image_id):
    """Build (or rebuild) the 3D photo relief for one specific plate image.
    3D generation is opt-in per plate, not automatic on upload (see
    CHANGELOG v2.0). POST only (v4.7): it writes files and database rows, so it
    must carry the CSRF token like every other change."""
    data = get_organism(cid)
    if data is None:
        abort(404)
    conn = db()
    img = conn.execute("SELECT * FROM plate_images WHERE image_id=? AND organism_id=?",
                       (image_id, data["org"]["organism_id"])).fetchone()
    conn.close()
    if img is None:
        abort(404)
    try:
        result = build_photo_relief(img, data["org"])
    except Exception as exc:
        result = dict(made=False, error=str(exc))
    if result.get("made"):
        flash("Built the 3D relief from this plate photo.")
    else:
        reason = result.get("error", "unknown error")
        cv_note = _cv_missing_reason()
        extra = f" ({cv_note})" if cv_note else ""
        flash(f"Couldn't build the 3D model: {reason}.{extra}")
    return redirect(url_for("species", cid=cid))


@app.route("/species/<cid>/mark-background/<int:image_id>", methods=["POST"])
def mark_background(cid, image_id):
    data = get_organism(cid)
    if data is None:
        abort(404)
    x = request.form.get("x", type=float)
    y = request.form.get("y", type=float)
    conn = db()
    conn.execute("UPDATE plate_images SET bg_marker_x=?, bg_marker_y=? WHERE image_id=? AND organism_id=?",
                 (x, y, image_id, data["org"]["organism_id"]))
    conn.commit()
    conn.close()
    flash("Background marker saved. Rebuild the model to use it.")
    return redirect(url_for("species", cid=cid))


@app.route("/species/<cid>/clear-background/<int:image_id>", methods=["POST"])
def clear_background_marker(cid, image_id):
    data = get_organism(cid)
    if data is None:
        abort(404)
    conn = db()
    conn.execute("UPDATE plate_images SET bg_marker_x=NULL, bg_marker_y=NULL WHERE image_id=? AND organism_id=?",
                 (image_id, data["org"]["organism_id"]))
    conn.commit()
    conn.close()
    flash("Background marker cleared.")
    return redirect(url_for("species", cid=cid))


@app.route("/species/<cid>/plate/<int:image_id>/fix-3d", methods=["POST"])
def fix_plate_3d(cid, image_id):
    """Set the dish size and the areas to ignore (painted over writing, a label or
    glare) for one plate photo, then rebuild its 3D relief."""
    data = get_organism(cid)
    if data is None:
        abort(404)
    conn = db()
    img = conn.execute("SELECT * FROM plate_images WHERE image_id=? AND organism_id=? AND voided_at IS NULL",
                       (image_id, data["org"]["organism_id"])).fetchone()
    if img is None:
        conn.close()
        abort(404)
    size = _plate_size(request.form.get("plate_size")) or img["plate_diameter_mm"] or DEFAULT_PLATE_MM
    areas = [] if request.form.get("clear") else _ignore_areas(request.form.get("ignore_areas"))
    areas_json = json.dumps(areas) if areas else None
    if (img["plate_diameter_mm"], img["ignore_areas"]) != (size, areas_json):
        conn.execute("UPDATE plate_images SET plate_diameter_mm=?, ignore_areas=? WHERE image_id=?",
                     (size, areas_json, image_id))
        audit(conn, "update", "plate_image", image_id,
              {"plate_diameter_mm": [img["plate_diameter_mm"], size],
               "ignored_areas": [len(_ignore_areas(img["ignore_areas"])), len(areas)]})
        conn.commit()
    img = conn.execute("SELECT * FROM plate_images WHERE image_id=?", (image_id,)).fetchone()
    conn.close()
    try:
        result = build_photo_relief(img, data["org"])
    except Exception as exc:
        result = dict(made=False, error=str(exc))
    if result.get("made"):
        flash(f"3D relief rebuilt on a {size:g} mm dish" +
              (f", ignoring {len(areas)} painted spot(s)." if areas else "."))
    else:
        cv_note = _cv_missing_reason()
        flash(f"Saved, but the 3D model couldn't be rebuilt: {result.get('error', 'unknown error')}."
              + (f" ({cv_note})" if cv_note else ""))
    return redirect(url_for("species", cid=cid) + f"#plate-{image_id}")


@app.route("/species/<cid>/log-revival", methods=["POST"])
def log_revival(cid):
    data = get_organism(cid)
    if data is None:
        abort(404)
    oid = data["org"]["organism_id"]
    revival_date = request.form.get("revival_date", "").strip() or date.today().isoformat()
    revival_time = request.form.get("revival_time", "").strip() or None
    performed_by = request.form.get("performed_by", "").strip() or None
    growth_success = request.form.get("growth_success", "").strip() or None
    contamination_notes = request.form.get("contamination_notes", "").strip() or None
    colony_count = _parsed_int(request.form, "colony_count")
    problem = _date_problem(revival_date, "Revival date")
    if growth_success not in ("pass", "fail"):
        problem = problem or "Choose whether the revival passed or failed."
    if problem:
        flash(problem + " Nothing was saved.")
        return redirect(url_for("species", cid=cid))

    conn = db()
    cur = conn.execute("""INSERT INTO revival_log
        (organism_id, tenant_id, revival_date, revival_time, performed_by, growth_success,
         contamination_notes, colony_count, entered_by_user)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (oid, data["org"]["tenant_id"], revival_date, revival_time, performed_by, growth_success,
         contamination_notes, colony_count, _signed_in_username()))
    audit(conn, "create", "revival", cur.lastrowid,
          dict(revival_date=revival_date, result=growth_success, performed_by=performed_by))
    recompute_revival_summary(conn, oid)
    conn.commit()
    conn.close()
    if growth_success == "fail":
        flash("Revival check logged as FAILED. The strain stays flagged red until a passing check is logged.")
    else:
        flash("Revival check logged.")
    return redirect(url_for("species", cid=cid))


@app.route("/species/<cid>/revival/<int:log_id>/void", methods=["POST"])
def void_revival(cid, log_id):
    data = get_organism(cid)
    if data is None:
        abort(404)
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Give a reason to void a revival entry.")
        return redirect(url_for("species", cid=cid))
    oid = data["org"]["organism_id"]
    conn = db()
    cur = conn.execute("""UPDATE revival_log SET voided_at=?, voided_by=?, void_reason=?
                          WHERE log_id=? AND organism_id=? AND voided_at IS NULL""",
                       (datetime.now().isoformat(timespec="seconds"), _signed_in_username(), reason,
                        log_id, oid))
    if cur.rowcount:
        audit(conn, "void", "revival", log_id, dict(reason=reason))
        recompute_revival_summary(conn, oid)
    conn.commit()
    conn.close()
    flash("Revival entry voided. It stays in the history, marked as voided.")
    return redirect(url_for("species", cid=cid))


@app.route("/species/<cid>/edit", methods=["GET", "POST"])
def edit_species(cid):
    data = get_organism(cid)
    if data is None:
        abort(404)
    if request.method == "POST":
        problem = _date_problem(request.form.get("revival_baseline_date", "").strip(),
                                "Last revival before this app")
        if problem:
            flash(problem + " Nothing was saved.")
            return redirect(url_for("edit_species", cid=cid))
        conn = db()
        before = dict(data["org"])
        _save_organism_form(conn, data["org"]["organism_id"], request.form)
        after = dict(conn.execute("SELECT * FROM organisms WHERE organism_id=?",
                                  (data["org"]["organism_id"],)).fetchone())
        changed = {k: [before.get(k), v] for k, v in after.items()
                   if k not in ("updated_at",) and before.get(k) != v}
        if changed:
            audit(conn, "update", "organism", data["org"]["organism_id"], changed)
        rejected = _save_custom_values(conn, data["org"]["organism_id"], tenant_id(), request.form)
        if rejected:
            flash("Not saved (wrong format): " + ", ".join(rejected) + ".")
        gram_image = request.files.get("gram_image")
        if gram_image and gram_image.filename:
            result = request.form.get("gram_stain", "").strip() or None
            _, rejected = _save_gram_stain(
                conn, data["org"], result,
                request.form.get("gram_stain_date", "").strip() or date.today().isoformat(),
                request.form.get("gram_stain_time", "").strip() or None,
                request.form.get("gram_stained_by", "").strip() or None,
                None, None, gram_image)
            if rejected:
                flash("Gram stain image wasn't saved. Use PNG/JPG/WebP/TIFF.")
        _flash_position_clash(conn, tenant_id(), request.form, exclude_oid=data["org"]["organism_id"])
        conn.commit()
        conn.close()
        flash(f"Saved changes to {data['org']['full_name']}.")
        return redirect(url_for("species", cid=cid))
    return render_template("edit_species.html", cid=cid, role_choices=ROLE_CHOICES,
                           viability_choices=VIABILITY_CHOICES,
                           type_choices=COLLECTION_TYPE_CHOICES,
                           gram_choices=GRAM_CHOICES, today=date.today().isoformat(),
                           source_choices=SAMPLE_SOURCE_CHOICES, labs=get_labs(), **data)


@app.route("/species/new", methods=["GET", "POST"])
def add_species():
    if request.method == "POST":
        genus = request.form.get("genus", "").strip()
        species_name = request.form.get("species", "").strip()
        if not genus or not species_name:
            flash("Genus and species are required.")
            return redirect(url_for("add_species"))
        if not request.form.get("storage_freezer", "").strip() or not request.form.get("storage_box", "").strip():
            flash("Storage location is required: enter the freezer or fridge and the box so this isolate can be found later.")
            return redirect(url_for("add_species"))
        baseline = request.form.get("revival_baseline_date", "").strip() or None
        problem = _date_problem(baseline, "Last revival before this app")
        if problem:
            flash(problem)
            return redirect(url_for("add_species"))
        full = f"{genus} {species_name}"
        conn = db()
        tid = tenant_id()
        begin_write(conn)
        cid = next_collection_id(genus, species_name, conn)
        source_type, lab_id, online_ref = _sample_source_fields(request.form)
        cur = conn.execute("""INSERT INTO organisms
            (tenant_id, collection_id, genus, species, strain, full_name, ncbi_taxonomy_id, notes,
             collection_type, gram_stain, added_by, viability_category, revival_interval_days,
             sample_source, sample_source_type, sample_source_lab_id,
             storage_freezer, storage_rack, storage_box, storage_position,
             storage_location, storage_format, storage_temp_c, date_stored,
             revival_baseline_date, last_revival_date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, cid, genus, species_name, request.form.get("strain", "").strip() or None,
             request.form.get("full_name", "").strip() or full,
             request.form.get("ncbi", "").strip() or None,
             request.form.get("notes", "").strip() or None,
             request.form.get("collection_type", "library").strip() or "library",
             request.form.get("gram_stain", "").strip() or None,
             request.form.get("added_by", "").strip() or None,
             request.form.get("viability_category", "unknown").strip() or "unknown",
             _parsed_int(request.form, "revival_interval_days"),
             online_ref, source_type, lab_id,
             request.form.get("storage_freezer", "").strip() or None,
             request.form.get("storage_rack", "").strip() or None,
             request.form.get("storage_box", "").strip() or None,
             request.form.get("storage_position", "").strip() or None,
             request.form.get("storage_location", "").strip() or None,
             request.form.get("storage_format", "").strip() or None,
             request.form.get("storage_temp_c", "").strip() or None,
             request.form.get("date_stored", "").strip() or None,
             baseline, baseline))
        oid = cur.lastrowid
        audit(conn, "create", "organism", oid, dict(collection_id=cid, full_name=full), tenant=tid)
        rejected = _save_custom_values(conn, oid, tid, request.form)
        if rejected:
            flash("Not saved (wrong format): " + ", ".join(rejected) + ".")
        cc = request.form.get("collection", "").strip()
        acc = request.form.get("accession", "").strip()
        if cc and acc:
            conn.execute("INSERT INTO accessions (organism_id, tenant_id, culture_collection, accession_number) VALUES (?,?,?,?)",
                         (oid, tid, cc, acc))
        for tag in [t.strip() for t in request.form.get("tags", "").split(",") if t.strip()]:
            conn.execute("INSERT INTO tags (organism_id, tenant_id, tag) VALUES (?,?,?)", (oid, tid, tag))
        conn.execute("""INSERT INTO brewing_relevance
            (organism_id, tenant_id, role, attenuation_min, attenuation_max, temp_range_c,
             flavor_compounds, off_flavor_potential, spoilage_risk, application_notes)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (oid, tid, request.form.get("role", "").strip() or None,
             request.form.get("att_min", "").strip() or None,
             request.form.get("att_max", "").strip() or None,
             request.form.get("temp", "").strip() or None,
             request.form.get("flavor", "").strip() or None,
             request.form.get("off_flavor", "").strip() or None,
             request.form.get("risk", "").strip() or None,
             request.form.get("application", "").strip() or None))

        gram_image = request.files.get("gram_image")
        if gram_image and gram_image.filename:
            gram_result = request.form.get("gram_stain", "").strip() or None
            org_stub = dict(organism_id=oid, collection_id=cid, tenant_id=tid)
            _, rejected = _save_gram_stain(
                conn, org_stub, gram_result,
                request.form.get("gram_stain_date", "").strip() or date.today().isoformat(),
                request.form.get("gram_stain_time", "").strip() or None,
                request.form.get("added_by", "").strip() or None,
                None, None, gram_image)
            if rejected:
                flash("Gram stain image wasn't saved. Use PNG/JPG/WebP/TIFF.")

        _flash_position_clash(conn, tid, request.form, exclude_oid=oid)
        conn.commit()
        conn.close()
        flash(f"Added {full} ({cid}).")
        return redirect(url_for("species", cid=cid))
    return render_template("add_species.html", role_choices=ROLE_CHOICES,
                           viability_choices=VIABILITY_CHOICES,
                           type_choices=COLLECTION_TYPE_CHOICES,
                           gram_choices=GRAM_CHOICES, today=date.today().isoformat(),
                           source_choices=SAMPLE_SOURCE_CHOICES, labs=get_labs(),
                           custom=custom_inputs())


def _remove_files(rel_paths, image_ids=()):
    """Delete a removed strain's photos and models (plus each model's GLB and
    texture companions) from disk. Only ever touches uploads/ and models/."""
    victims = set()
    for rel in rel_paths:
        p = DATA_DIR / rel
        victims.add(p)
        if p.suffix == ".stl":
            victims.add(p.with_suffix(".glb"))
            victims.add(p.with_name(p.stem + "-texture.png"))
    for iid in image_ids:
        victims.add(MODEL_DIR / f"img{iid}-texture.png")
    for p in victims:
        try:
            resolved = p.resolve()
            if resolved.is_file() and (UPLOAD_DIR.resolve() in resolved.parents
                                       or MODEL_DIR.resolve() in resolved.parents):
                resolved.unlink()
        except OSError:
            pass


@app.route("/species/<cid>/delete", methods=["POST"])
@admin_required
def delete_species(cid):
    """Removing a whole strain is an admin action that needs a reason; a full
    snapshot of the record and its histories is written to the audit log first."""
    data = get_organism(cid)
    if data is None:
        abort(404)
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Give a reason to delete a strain. Its full record is kept in the audit log.")
        return redirect(url_for("species", cid=cid))
    oid = data["org"]["organism_id"]
    conn = db()
    snap = dict(reason=reason, organism=dict(data["org"]))
    for t in ("revival_log", "gram_stain_images", "plate_images", "accessions", "tags"):
        snap[t] = [dict(r) for r in conn.execute(f"SELECT * FROM {t} WHERE organism_id=?", (oid,))]
    audit(conn, "delete", "organism", oid, snap)
    files = [r["file_path"] for r in conn.execute(
        "SELECT file_path FROM plate_images WHERE organism_id=? UNION ALL "
        "SELECT file_path FROM gram_stain_images WHERE organism_id=? UNION ALL "
        "SELECT file_path FROM models WHERE organism_id=?", (oid, oid, oid)) if r["file_path"]]
    image_ids = [r["image_id"] for r in conn.execute(
        "SELECT image_id FROM plate_images WHERE organism_id=?", (oid,))]
    conn.execute("DELETE FROM organisms WHERE collection_id=? AND tenant_id=?", (cid, tenant_id()))
    conn.commit()
    conn.close()
    _remove_files(files, image_ids)
    flash("Strain deleted.")
    return redirect(url_for("catalog"))


@app.route("/species/<cid>/accession/<int:accession_id>/delete", methods=["POST"])
def delete_accession(cid, accession_id):
    data = get_organism(cid)
    if data is None:
        abort(404)
    conn = db()
    old = conn.execute("SELECT * FROM accessions WHERE accession_id=? AND organism_id=?",
                       (accession_id, data["org"]["organism_id"])).fetchone()
    if old is not None:
        audit(conn, "delete", "accession", accession_id, dict(old))
    conn.execute("DELETE FROM accessions WHERE accession_id=? AND organism_id=?",
                 (accession_id, data["org"]["organism_id"]))
    conn.commit()
    conn.close()
    return redirect(url_for("species", cid=cid))


@app.route("/species/<cid>/image/<int:image_id>/delete", methods=["POST"])
def delete_image(cid, image_id):
    """Void a plate photo: it is hidden from the current record but kept, with
    who voided it, when and why. A 3D relief built from it is derived data and
    is removed (it can be rebuilt if the photo is ever restored)."""
    data = get_organism(cid)
    if data is None:
        abort(404)
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Give a reason to void a plate photo (for example, \"wrong plate\").")
        return redirect(url_for("species", cid=cid))
    oid = data["org"]["organism_id"]
    conn = db()
    files = [m["file_path"] for m in conn.execute(
        "SELECT file_path FROM models WHERE organism_id=? AND file_path LIKE ?",
        (oid, f"%-img{image_id}-photorelief-%")).fetchall()]
    conn.execute("DELETE FROM models WHERE organism_id=? AND file_path LIKE ?",
                 (oid, f"%-img{image_id}-photorelief-%"))
    cur = conn.execute("""UPDATE plate_images SET voided_at=?, voided_by=?, void_reason=?
                          WHERE image_id=? AND organism_id=? AND voided_at IS NULL""",
                       (datetime.now().isoformat(timespec="seconds"), _signed_in_username(), reason,
                        image_id, oid))
    if cur.rowcount:
        audit(conn, "void", "plate_image", image_id, dict(reason=reason))
    conn.commit()
    conn.close()
    _remove_files(files, [image_id])
    flash("Plate photo voided. It stays in the history, marked as voided.")
    return redirect(url_for("species", cid=cid))


@app.route("/species/<cid>/image/<int:image_id>/edit", methods=["POST"])
def edit_plate_image(cid, image_id):
    """Change a plate's media/incubation conditions or capture metadata
    after the fact -- a batch upload can mix media/plates, and the right
    values aren't always known (or correct) at upload time."""
    data = get_organism(cid)
    if data is None:
        abort(404)
    conn = db()
    img = conn.execute("SELECT * FROM plate_images WHERE image_id=? AND organism_id=?",
                       (image_id, data["org"]["organism_id"])).fetchone()
    if img is None:
        conn.close()
        abort(404)
    problem = _date_problem(request.form.get("captured_date", "").strip(), "Captured date")
    if problem:
        conn.close()
        flash(problem + " Nothing was saved.")
        return redirect(url_for("species", cid=cid))
    conn.execute("""UPDATE plate_images SET
        media=?, atmosphere=?, capture_protocol_version=?, lighting=?, angle=?,
        resolution=?, background=?, captured_by=?, captured_date=?, captured_time=?,
        plate_diameter_mm=?
        WHERE image_id=?""",
        (request.form.get("media", "").strip() or None,
         request.form.get("atmosphere", "").strip() or None,
         request.form.get("protocol", "").strip() or None,
         request.form.get("lighting", "").strip() or None,
         request.form.get("angle", "").strip() or None,
         request.form.get("resolution", "").strip() or None,
         request.form.get("background", "").strip() or None,
         request.form.get("captured_by", "").strip() or None,
         request.form.get("captured_date", "").strip() or None,
         request.form.get("captured_time", "").strip() or None,
         _plate_size(request.form.get("plate_size")) or img["plate_diameter_mm"],
         image_id))
    after = conn.execute("SELECT * FROM plate_images WHERE image_id=?", (image_id,)).fetchone()
    changed = {k: [img[k], after[k]] for k in after.keys() if img[k] != after[k]}
    if changed:
        audit(conn, "update", "plate_image", image_id, changed)
    conn.commit()
    conn.close()
    flash("Plate metadata updated.")
    return redirect(url_for("species", cid=cid))


BROWSER_IMG = {".png", ".jpg", ".jpeg", ".webp"}


def _lab_keeps_original_photos():
    """True if this lab chose to store photos exactly as uploaded."""
    try:
        conn = db()
        row = conn.execute("SELECT keep_original_photos FROM tenants WHERE tenant_id=?",
                           (tenant_id(),)).fetchone()
        conn.close()
        return bool(row and row[0])
    except Exception:
        return False


def _save_image_upload(file_storage, stem):
    """Validate an uploaded picture with Pillow and store it under uploads/.

    Checks the content, not just the extension. Large phone photos are turned
    upright and stored in less space (photo_store.compact) unless the lab keeps
    originals. Formats most browsers cannot show (TIFF, GIF, ...) are converted
    so the photo displays on its card. Returns the stored relative path, or None
    if the file is not an image. What was done is added to g.photo_notes."""
    ext = Path(file_storage.filename or "").suffix.lower()
    if ext not in ALLOWED_IMG:
        return None
    data = file_storage.read()
    try:
        from PIL import Image as _PIL
        with _PIL.open(io.BytesIO(data)) as probe:
            probe.verify()
        im = _PIL.open(io.BytesIO(data))
        im.load()
    except Exception:
        return None
    safe = secure_filename(file_storage.filename) or "image"
    if not _lab_keeps_original_photos():
        try:
            new, new_ext, info = photo_store.compact(data, ext)
        except Exception:
            new, new_ext, info = data, ext, None
        if info and info.get("changed"):
            data, ext = new, new_ext
            safe = Path(safe).stem + new_ext
            im = _PIL.open(io.BytesIO(data))
            im.load()
            g.setdefault("photo_notes", []).append(info)
    if ext not in BROWSER_IMG:
        safe = Path(safe).stem + ".png"
        name = f"{stem}-{secrets.token_hex(3)}-{safe}"
        im.convert("RGB").save(UPLOAD_DIR / name, format="PNG")
    else:
        name = f"{stem}-{secrets.token_hex(3)}-{safe}"
        (UPLOAD_DIR / name).write_bytes(data)
    return f"uploads/{name}"


def _date_problem(value, label):
    """None if value is an ISO date not in the future, else a message."""
    if not value:
        return None
    try:
        d = datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return f"{label} isn't a valid date (use YYYY-MM-DD)."
    if d > date.today():
        return f"{label} ({value}) is in the future. Record a check on or after the day it happens."
    return None


def _recompute_gram_summary(conn, organism_id):
    row = conn.execute("""SELECT result FROM gram_stain_images
                          WHERE organism_id=? AND voided_at IS NULL AND result IS NOT NULL
                          ORDER BY COALESCE(stain_date,'') DESC, COALESCE(stain_time,'') DESC,
                                   image_id DESC LIMIT 1""",
                       (organism_id,)).fetchone()
    if row is not None:
        conn.execute("UPDATE organisms SET gram_stain=?, updated_at=datetime('now') WHERE organism_id=?",
                     (row["result"], organism_id))


def _observed_cell(conn, organism_id):
    """The cell shape recorded with the latest Gram stain that has one, or None."""
    return conn.execute("""SELECT cell_shape, cell_arrangement, stain_date FROM gram_stain_images
                           WHERE organism_id=? AND voided_at IS NULL AND cell_shape IS NOT NULL
                           ORDER BY COALESCE(stain_date,'') DESC, COALESCE(stain_time,'') DESC,
                                    image_id DESC LIMIT 1""", (organism_id,)).fetchone()


def _shape_input(conn, org):
    """The organism as resolve_shape_config() wants it: the record itself plus
    what was seen with its latest Gram stain."""
    d = dict(org)
    seen = _observed_cell(conn, org["organism_id"]) if org["organism_id"] else None
    if seen:
        d.update(observed_shape=seen["cell_shape"], observed_arrangement=seen["cell_arrangement"],
                 observed_date=seen["stain_date"])
    return d


def _cell_fields(form):
    """(shape, arrangement) from a form, checked against each other. Returns
    (shape, arrangement, problem) where problem is a message or None."""
    kind = (form.get("cell_shape") or "").strip() or None
    arr = (form.get("cell_arrangement") or "").strip() or None
    if kind is not None and kind not in shapes.CELL_DIMENSIONS:
        return None, None, "That cell shape isn't one the app knows."
    if arr is not None and arr not in dict(shapes.ARRANGEMENT_LABELS):
        return None, None, "That arrangement isn't one the app knows."
    if arr and not kind:
        return None, None, "Choose the cell shape as well as the arrangement."
    if kind and arr and arr not in shapes.ARRANGEMENTS_FOR[kind]:
        return None, None, (f"{dict(shapes.ARRANGEMENT_LABELS)[arr]} don't go with "
                            f"{dict(shapes.SHAPE_LABELS)[kind].split(' (')[0].lower()} cells.")
    return kind, arr, None


def _save_gram_stain(conn, org, result, stain_date, stain_time, stained_by,
                     magnification, notes, image_file, cell_shape=None, cell_arrangement=None):
    """Shared by log_gram_stain() and the add/edit-species forms -- saves an
    optional micrograph as a dated Gram-stain history entry (the same
    pattern as plate photos and revival checks, so a strain can be
    re-stained later without losing the record) and updates the strain's
    current gram_stain field if a result was given. Returns
    (saved_image_path_or_None, image_rejected_bool) -- image_rejected is
    True only when a file was actually offered but had an unsupported
    extension, so the caller can flash a specific warning."""
    rel_path = None
    image_rejected = False
    if image_file and image_file.filename:
        rel_path = _save_image_upload(
            image_file, f"{org['collection_id']}-gram-{date.today().strftime('%Y%m%d')}")
        image_rejected = rel_path is None

    if cell_arrangement and not cell_shape:
        cell_arrangement = None
    cur = conn.execute("""INSERT INTO gram_stain_images
        (organism_id, tenant_id, file_path, result, magnification, stained_by, stain_date, stain_time, notes,
         uploaded_by, upload_timestamp, entered_by_user, cell_shape, cell_arrangement)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (org["organism_id"], org["tenant_id"], rel_path, result, magnification, stained_by, stain_date, stain_time, notes,
         stained_by, datetime.now().isoformat(timespec="seconds"), _signed_in_username(),
         cell_shape, cell_arrangement))
    detail = dict(result=result, stain_date=stain_date, stained_by=stained_by)
    if cell_shape:
        detail.update(cell_shape=cell_shape, cell_arrangement=cell_arrangement)
    audit(conn, "create", "gram_stain", cur.lastrowid, detail, tenant=org["tenant_id"])
    if result:
        _recompute_gram_summary(conn, org["organism_id"])
    if cell_shape:
        # A new observation replaces an earlier hand correction of the model.
        prev = conn.execute("SELECT cell_shape, cell_arrangement FROM organisms WHERE organism_id=?",
                            (org["organism_id"],)).fetchone()
        if prev and (prev["cell_shape"] or prev["cell_arrangement"]):
            conn.execute("UPDATE organisms SET cell_shape=NULL, cell_arrangement=NULL WHERE organism_id=?",
                         (org["organism_id"],))
            audit(conn, "update", "organism", org["organism_id"],
                  {"cell_shape": [prev["cell_shape"], None],
                   "cell_arrangement": [prev["cell_arrangement"], None],
                   "why": "replaced by the Gram stain record"}, tenant=org["tenant_id"])
    return rel_path, image_rejected


@app.route("/species/<cid>/gram-stain", methods=["POST"])
def log_gram_stain(cid):
    """Record a Gram stain result, optionally with a micrograph."""
    data = get_organism(cid)
    if data is None:
        abort(404)
    org = data["org"]
    result = request.form.get("result", "").strip() or None
    if result and result not in GRAM_CHOICES:
        result = None
    stained_by = request.form.get("stained_by", "").strip() or None
    stain_date = request.form.get("stain_date", "").strip() or date.today().isoformat()
    problem = _date_problem(stain_date, "Stain date")
    if problem:
        flash(problem + " Nothing was saved.")
        return redirect(url_for("species", cid=cid))
    stain_time = request.form.get("stain_time", "").strip() or None
    magnification = request.form.get("magnification", "").strip() or None
    notes = request.form.get("notes", "").strip() or None
    kind, arr, problem = _cell_fields(request.form)
    if problem:
        flash(problem + " Nothing was saved.")
        return redirect(url_for("species", cid=cid) + "#gram-log")
    f = request.files.get("image")
    if not result and not (f and f.filename) and not notes and not kind:
        flash("Choose a result, the cell shape or a photo to save a Gram stain.")
        return redirect(url_for("species", cid=cid) + "#gram-log")

    conn = db()
    rel_path, rejected = _save_gram_stain(conn, org, result, stain_date, stain_time, stained_by,
                                         magnification, notes, f, kind, arr)
    conn.commit()
    conn.close()
    if kind or result:
        # the cell model follows the latest Gram stain (shape, and colour from the result)
        fresh = get_organism(cid)["org"]
        generate_models_for(fresh)
        if kind:
            conn = db()
            cell = resolve_shape_config(_shape_input(conn, fresh))["cell"]
            conn.close()
            flash(f"Cell model updated: {shapes.describe_cell(cell)}.")
    if rejected:
        flash("Gram stain image wasn't saved. Use PNG/JPG/WebP/TIFF." +
              (" The result was still recorded." if result else ""))
    if result:
        flash("Gram stain recorded." + (" Photo saved." if rel_path else ""))
    elif rel_path:
        flash("Gram stain photo saved.")
    return redirect(url_for("species", cid=cid))


@app.route("/species/<cid>/gram-stain/<int:image_id>/delete", methods=["POST"])
def delete_gram_stain(cid, image_id):
    data = get_organism(cid)
    if data is None:
        abort(404)
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Give a reason to void a Gram stain entry (for example, \"wrong strain\").")
        return redirect(url_for("species", cid=cid))
    oid = data["org"]["organism_id"]
    conn = db()
    cur = conn.execute("""UPDATE gram_stain_images SET voided_at=?, voided_by=?, void_reason=?
                          WHERE image_id=? AND organism_id=? AND voided_at IS NULL""",
                       (datetime.now().isoformat(timespec="seconds"), _signed_in_username(), reason,
                        image_id, oid))
    if cur.rowcount:
        audit(conn, "void", "gram_stain", image_id, dict(reason=reason))
        _recompute_gram_summary(conn, oid)
    conn.commit()
    conn.close()
    flash("Gram stain entry voided. It stays in the history, marked as voided.")
    if cur.rowcount:
        generate_models_for(get_organism(cid)["org"])
    return redirect(url_for("species", cid=cid))


# --------------------------------------------------------------------------- #
# Routes -- upload
# --------------------------------------------------------------------------- #
@app.route("/upload", methods=["GET", "POST"])
@app.route("/species/<cid>/upload", methods=["GET", "POST"])
def upload(cid=None):
    conn = db()
    tid = tenant_id()
    orgs = conn.execute("SELECT collection_id, full_name FROM organisms WHERE tenant_id=? ORDER BY collection_id",
                        (tid,)).fetchall()
    if request.method == "POST":
        cid = cid or request.form.get("collection_id")
        org = conn.execute("SELECT * FROM organisms WHERE collection_id=? AND tenant_id=?", (cid, tid)).fetchone()
        if org is None:
            conn.close()
            flash("Unknown organism.")
            return redirect(url_for("upload"))
        # Each plate row in the form repeats the same field names (image/
        # media/atmosphere), so Werkzeug's getlist() gives parallel lists in
        # the same order the rows were submitted -- that's how one upload
        # covers several plates, each with its own photo + media + condition,
        # rather than one set of metadata forced onto every file.
        files = request.files.getlist("image")
        medias = request.form.getlist("media")
        atmospheres = request.form.getlist("atmosphere")
        sizes = request.form.getlist("plate_size")
        if not files or all(f.filename == "" for f in files):
            conn.close()
            flash("No file selected.")
            return redirect(url_for("upload", cid=cid))

        uploaded_by = request.form.get("uploaded_by", "").strip() or None
        captured_date = request.form.get("captured_date", "").strip() or date.today().isoformat()
        captured_time = request.form.get("captured_time", "").strip() or None
        now_iso = datetime.now().isoformat(timespec="seconds")
        problem = _date_problem(captured_date, "Captured date")
        if problem:
            conn.close()
            flash(problem + " Nothing was uploaded.")
            return redirect(url_for("upload", cid=cid))
        saved = 0
        skipped = 0
        for i, f in enumerate(files):
            if f.filename == "":
                continue
            rel_path = _save_image_upload(f, f"{org['collection_id']}-{date.today().strftime('%Y%m%d')}")
            if rel_path is None:
                skipped += 1
                continue
            media = (medias[i].strip() if i < len(medias) else "") or None
            atmosphere = (atmospheres[i].strip() if i < len(atmospheres) else "") or None
            plate_mm = _plate_size(sizes[i] if i < len(sizes) else None) or DEFAULT_PLATE_MM
            conn.execute("""INSERT INTO plate_images
                (organism_id, tenant_id, file_path, capture_protocol_version, lighting, angle,
                 scale_bar_present, resolution, background, license, captured_by, captured_date,
                 captured_time, media, atmosphere, uploaded_by, upload_timestamp, entered_by_user,
                 plate_diameter_mm)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (org["organism_id"], tid, rel_path,
                 request.form.get("protocol", "").strip() or None,
                 request.form.get("lighting", "").strip() or None,
                 request.form.get("angle", "").strip() or None,
                 1 if request.form.get("scale_bar") else 0,
                 request.form.get("resolution", "").strip() or None,
                 request.form.get("background", "").strip() or None,
                 request.form.get("license", "").strip() or None,
                 request.form.get("captured_by", "").strip() or None,
                 captured_date, captured_time, media, atmosphere,
                 uploaded_by, now_iso, _signed_in_username(), plate_mm))
            saved += 1
        conn.commit()
        conn.close()

        # No automatic 3D build here -- it's optional per plate (see
        # build_plate_replica, a plain GET link on the species page) since
        # not every photo needs a model, and building one for every upload
        # used to happen whether or not anyone wanted it.
        if skipped:
            flash(f"{skipped} file(s) were not readable images and were skipped.")
        notes = g.get("photo_notes") or []
        if notes:
            before = sum(n["original_bytes"] for n in notes)
            after = sum(n["stored_bytes"] for n in notes)
            extra = [w for w, k in (("turned upright", "turned"), ("location data removed", "had_location"))
                     if any(n.get(k) for n in notes)]
            flash(f"Stored {len(notes)} large photo(s) in {after / 1e6:.1f} MB instead of "
                  f"{before / 1e6:.1f} MB, with no visible loss" +
                  (f" ({', '.join(extra)})" if extra else "") + ".")
        if saved:
            flash(f"Uploaded {saved} plate image(s) for {org['full_name']}. "
                 f"Build a 3D model for any of them from its card below, if you want one.")
        else:
            flash("No supported image files were uploaded (use PNG/JPG/WebP/TIFF).")
        return redirect(url_for("species", cid=cid))
    org_row = conn.execute("SELECT organism_id FROM organisms WHERE collection_id=? AND tenant_id=?",
                           (cid, tid)).fetchone() if cid else None
    default_size = _lab_plate_size(conn, tid, org_row["organism_id"] if org_row else None)
    conn.close()
    return render_template("upload.html", orgs=orgs, cid=cid,
                           plate_sizes=PLATE_SIZES_MM, default_size=default_size)


# --------------------------------------------------------------------------- #
# Routes -- static file serving, export, connect/QR
# --------------------------------------------------------------------------- #
def _lab_owns_file(folder, fn):
    """True only if a record belonging to the signed-in lab points at this
    file. Filenames are guessable (they contain the collection ID), and every
    lab shares one uploads/ and models/ folder, so being logged in is not
    enough: without this check any lab could fetch another's photos/models."""
    if not fn or re.search(r"[\\/]", fn):
        return False
    tid = tenant_id()
    conn = db()
    try:
        if folder == "uploads":
            rel = f"uploads/{fn}"
            return any(conn.execute(f"SELECT 1 FROM {t} WHERE tenant_id=? AND REPLACE(file_path, char(92), '/')=?",
                                    (tid, rel)).fetchone()
                       for t in ("plate_images", "gram_stain_images"))
        m = re.match(r"^img(\d+)-texture\.png$", fn)
        if m:  # baked texture of a photo relief, keyed by the (global) plate image id
            return conn.execute("SELECT 1 FROM plate_images WHERE tenant_id=? AND image_id=?",
                                (tid, int(m.group(1)))).fetchone() is not None
        for suffix in ("-texture.png", ".stl", ".glb"):
            if fn.endswith(suffix):
                stem = fn[:-len(suffix)]
                break
        else:
            return False
        return conn.execute("SELECT 1 FROM models WHERE tenant_id=? AND REPLACE(file_path, char(92), '/') IN (?,?)",
                            (tid, f"models/{stem}.stl", f"models/{stem}.glb")).fetchone() is not None
    finally:
        conn.close()


@app.route("/models/<path:fn>")
def serve_model(fn):
    if not _lab_owns_file("models", fn):
        abort(404)
    return send_from_directory(MODEL_DIR, fn)


@app.route("/uploads/<path:fn>")
def serve_upload(fn):
    if not _lab_owns_file("uploads", fn):
        abort(404)
    return send_from_directory(UPLOAD_DIR, fn)


@app.route("/export")
@admin_required
def export():
    buf = build_export_zip()
    return send_file(buf, as_attachment=True,
                     download_name=f"microbial_digital_library_{date.today().isoformat()}.zip",
                     mimetype="application/zip")


@app.route("/export/xlsx")
@admin_required
def export_xlsx():
    buf = build_export_xlsx()
    return send_file(buf, as_attachment=True,
                     download_name=f"microbial_digital_library_{date.today().isoformat()}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _phone_url():
    """The address a phone on the same network should open. PUBLIC_BASE_URL
    wins; otherwise the address you're browsing at -- unless that's localhost,
    which a phone can't reach, so swap in this computer's LAN address."""
    configured = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    host = request.host
    name, _, port = host.partition(":") if not host.startswith("[") else (host, "", "")
    if name in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return f"{request.scheme}://{get_lan_ip()}" + (f":{port}" if port else "")
    return request.url_root.rstrip("/")


@app.route("/connect")
def connect():
    return render_template("connect.html", lan_url=_phone_url(), qr_available=segno is not None)


@app.route("/qr.png")
def qr_png():
    if segno is None:
        abort(501)
    buf = io.BytesIO()
    segno.make(_phone_url(), error="m").save(buf, kind="png", scale=8, border=2)
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/manifest.json")
def manifest():
    return send_from_directory(BASE / "static", "manifest.json", mimetype="application/manifest+json")


@app.route("/service-worker.js")
def service_worker():
    return send_from_directory(BASE / "static", "service-worker.js", mimetype="application/javascript")


def _usage_snapshot(conn, tid):
    """Facts measured from the lab's own catalog at the moment a response is
    sent, so the paper can quote real usage next to what people say."""
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
    strains = one("SELECT COUNT(*) FROM organisms WHERE tenant_id=?", tid)
    plates = one("SELECT COUNT(*) FROM plate_images WHERE tenant_id=?", tid)
    checks = one("""SELECT COUNT(*) FROM revival_log r JOIN organisms o ON o.organism_id=r.organism_id
                    WHERE o.tenant_id=?""", tid)
    located = one("""SELECT COUNT(*) FROM organisms WHERE tenant_id=? AND (
                       COALESCE(storage_freezer,'')<>'' OR COALESCE(storage_rack,'')<>'' OR
                       COALESCE(storage_box,'')<>'' OR COALESCE(storage_position,'')<>'')""", tid)
    days = one("SELECT CAST(julianday('now') - julianday(MIN(created_at)) AS INTEGER) FROM organisms WHERE tenant_id=?", tid)
    return dict(app_version=APP_VERSION, strains_count=strains, plates_count=plates,
                revival_checks_count=checks, strains_with_location=located,
                days_in_use=days if days is not None else 0)


def _render_feedback_form(form=None):
    from werkzeug.datastructures import MultiDict
    return render_template("feedback.html", ease_choices=fs.EASE_CHOICES,
                           feature_choices=fs.FEATURE_CHOICES, sections=fs.SECTIONS,
                           form=form if form is not None else MultiDict())


@app.route("/feedback", methods=["GET", "POST"])
def feedback():
    if request.method == "POST":
        f = request.form
        rating = _parsed_int(f, "rating")
        ease = f.get("ease", "").strip()
        valid_ease = {k for k, _ in fs.EASE_CHOICES}
        if not rating or not (1 <= rating <= 5) or ease not in valid_ease:
            flash("Please pick an overall rating and how easy it was to use. Everything else is optional.")
            return _render_feedback_form(f)
        answers, errors = fs.parse_answers(f)
        email = f.get("contact_email", "").strip()
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            errors.append("Email address: please check it, or leave it empty.")
        most_used = f.get("most_used", "").strip()
        if most_used and most_used not in {k for k, _ in fs.FEATURE_CHOICES}:
            most_used = ""
        if errors:
            for e in errors[:6]:
                flash(e)
            flash("Nothing was saved yet. Your answers are still in the form: fix the points above and send it again.")
            return _render_feedback_form(f)
        conn = db()
        tid = tenant_id()
        snap = _usage_snapshot(conn, tid)
        conn.execute(
            """INSERT INTO feedback (tenant_id, rating, ease, most_used, improvement, name,
                                     answers, sus_score, consent_research, contact_email,
                                     app_version, strains_count, plates_count,
                                     revival_checks_count, strains_with_location, days_in_use)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, rating, ease, most_used or None,
             f.get("improvement", "").strip()[:fs.MAX_TEXT] or None,
             f.get("name", "").strip()[:120] or None,
             fs.dumps(answers) if answers else None,
             fs.sus_score(answers),
             1 if f.get("consent_research") else 0,
             email[:200] or None,
             snap["app_version"], snap["strains_count"], snap["plates_count"],
             snap["revival_checks_count"], snap["strains_with_location"], snap["days_in_use"]))
        conn.commit()
        conn.close()
        flash("Thanks, your feedback was recorded.")
        return redirect(url_for("feedback"))
    return _render_feedback_form()


@app.route("/feedback/summary")
def feedback_summary():
    conn = db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM feedback WHERE tenant_id=? ORDER BY submitted_at DESC", (tenant_id(),)).fetchall()]
    conn.close()
    summary = fs.summarize(rows)
    ease_labels = dict(fs.EASE_CHOICES)
    feature_labels = dict(fs.FEATURE_CHOICES)
    count = len(rows)
    avg_rating = round(sum(r["rating"] for r in rows) / count, 1) if count else None
    ease_counts, feature_counts = {}, {}
    for r in rows:
        ease_counts[r["ease"]] = ease_counts.get(r["ease"], 0) + 1
        if r["most_used"]:
            feature_counts[r["most_used"]] = feature_counts.get(r["most_used"], 0) + 1
    ease_breakdown = [(ease_labels.get(k, k), v) for k, v in
                      sorted(ease_counts.items(), key=lambda kv: -kv[1])]
    feature_breakdown = [(feature_labels.get(k, k), v) for k, v in
                         sorted(feature_counts.items(), key=lambda kv: -kv[1])]
    comments = [r for r in rows if r["improvement"]]
    latest = rows[0] if rows else None
    return render_template("feedback_summary.html", rows=rows, count=count,
                           avg_rating=avg_rating, ease_breakdown=ease_breakdown,
                           feature_breakdown=feature_breakdown, comments=comments,
                           ease_labels=ease_labels, feature_labels=feature_labels,
                           s=summary, latest=latest, is_lab_admin=(current_user() or {"role": ""})["role"] == "admin")


@app.route("/feedback/export")
@admin_required
def feedback_export():
    """Anonymous results for a paper: responses.csv and data_dictionary.csv in one
    ZIP. Only responses whose author agreed to research use are included unless
    ?all=1 is given. Names and email addresses are never included."""
    include_all = request.args.get("all") == "1"
    conn = db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM feedback WHERE tenant_id=? ORDER BY submitted_at", (tenant_id(),)).fetchall()]
    conn.close()
    cols, data = fs.export_rows(rows, consent_only=not include_all)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        s = io.StringIO()
        w = csv.writer(s)
        w.writerow(cols)
        for r in data:
            w.writerow([_csv_safe(r.get(c, "")) if r.get(c) is not None else "" for c in cols])
        zf.writestr("responses.csv", "\ufeff" + s.getvalue())
        dcols, drows = fs.data_dictionary()
        s = io.StringIO()
        w = csv.writer(s)
        w.writerow(dcols)
        for r in drows:
            w.writerow([_csv_safe(x) for x in r])
        zf.writestr("data_dictionary.csv", "\ufeff" + s.getvalue())
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name="evaluation_responses_%s.zip" % date.today().isoformat(),
                     mimetype="application/zip")


# --------------------------------------------------------------------------- #
# Routes -- About and Contact (public: readable without signing in)
# --------------------------------------------------------------------------- #
CONTACT_CATEGORIES = [
    ("question", "A question"),
    ("access", "Access to the lab"),
    ("bug", "Something isn't working"),
    ("feature", "A feature request"),
    ("other", "Something else"),
]
CONTACT_MESSAGE_MAX = 4000


def _contact_details():
    """Who to contact, from the environment (set in .env), so the person running
    the install decides what is shown. Nothing is shown that isn't set."""
    def env(name):
        return os.environ.get(name, "").strip()
    d = {"name": env("CONTACT_NAME"), "org": env("CONTACT_ORG"), "email": env("CONTACT_EMAIL"),
         "phone": env("CONTACT_PHONE"), "website": env("CONTACT_WEBSITE"), "note": env("CONTACT_NOTE")}
    if d["website"] and not re.match(r"^https?://", d["website"], re.I):
        d["website"] = ""        # only ever link to http(s) addresses
    if d["email"] and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", d["email"]):
        d["email"] = ""
    d["any"] = any(d[k] for k in ("name", "org", "email", "phone", "website"))
    return d


@app.route("/about")
def about():
    return render_template("about.html", version=APP_VERSION, contact=_contact_details(),
                           )


@app.route("/contact", methods=["GET", "POST"])
def contact():
    user = current_user()
    if request.method == "POST":
        if not app.config.get("TESTING") and _ip_rate_limited(f"contact:{_client_ip()}"):
            flash("Too many messages from this connection. Please wait a few minutes and try again.")
            return redirect(url_for("contact"))
        if request.form.get("website_url", "").strip():
            # Hidden field only automated form-fillers complete: pretend it worked.
            flash("Thanks, your message was sent.")
            return redirect(url_for("contact"))
        category = request.form.get("category", "question")
        if category not in {k for k, _ in CONTACT_CATEGORIES}:
            category = "other"
        name = request.form.get("name", "").strip()[:100] or None
        email = request.form.get("email", "").strip()[:200] or None
        message = request.form.get("message", "").strip()
        if len(message) < 5:
            flash("Please write a message (at least a few words).")
            return redirect(url_for("contact"))
        if len(message) > CONTACT_MESSAGE_MAX:
            flash(f"That message is too long. Please keep it under {CONTACT_MESSAGE_MAX} characters.")
            return redirect(url_for("contact"))
        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            flash("That email address doesn't look right. Fix it, or leave it empty.")
            return redirect(url_for("contact"))
        conn = db()
        conn.execute(
            "INSERT INTO contact_messages (category, name, email, message, tenant_id, username) "
            "VALUES (?,?,?,?,?,?)",
            (category, name, email, message,
             user["tenant_id"] if user else None, user["username"] if user else None))
        conn.commit()
        conn.close()
        flash("Thanks, your message was sent to the lab's admins.")
        return redirect(url_for("contact"))
    return render_template("contact.html", contact=_contact_details(), categories=CONTACT_CATEGORIES,
                           version=APP_VERSION, max_len=CONTACT_MESSAGE_MAX,
                           prefill_name=(user["full_name"] or user["username"]) if user else "")


@app.route("/admin/messages/<int:message_id>/<action>", methods=["POST"])
@admin_required
def admin_message_action(message_id, action):
    if action not in ("read", "unread", "delete"):
        abort(404)
    conn = db()
    if action == "delete":
        conn.execute("DELETE FROM contact_messages WHERE message_id=?", (message_id,))
    else:
        conn.execute("UPDATE contact_messages SET is_read=? WHERE message_id=?",
                     (1 if action == "read" else 0, message_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_home") + "#messages")




def _print_startup_diagnostics():
    print("Microbial Digital Library")
    cv_note = _cv_missing_reason()
    print("  Photo-driven 3D relief: "
         + ("OFF: " + cv_note if cv_note else "ON"))
    conn = db()
    n_users = conn.execute("SELECT COUNT(*) c FROM users WHERE is_active=1").fetchone()["c"]
    n_admins = conn.execute(
        "SELECT COUNT(*) c FROM users WHERE is_active=1 AND role='admin'").fetchone()["c"]
    lab = conn.execute("SELECT name FROM tenants LIMIT 1").fetchone()
    conn.close()
    if lab is None:
        print(f"  Not set up yet. Open the address below and enter this setup code: {_show_join_code(setup_code())}")
    else:
        print(f"  Lab: {lab['name']}, {n_users} active account(s) ({n_admins} admin). "
              "Colleagues ask to join at /signup with the lab's join code.")
    print("  QR codes (phone access, labels): " + ("ON" if segno is not None
          else "OFF (run: pip install segno)"))
    port = os.environ.get("MDL_PORT", "5000")
    scheme = "https" if os.environ.get("MDL_SSL_CERT") else "http"
    print(f"  Open {scheme}://localhost:{port}, or {scheme}://{get_lan_ip()}:{port} from another device on this network")


# --------------------------------------------------------------------------- #
# Public demo mode (MDL_DEMO=1; see demo.py). Off by default.
# --------------------------------------------------------------------------- #
import password_reset as _pwreset  # noqa: E402  (forgot password, reset links)
_pwreset.init(sys.modules[__name__])
import demo as _demo  # noqa: E402
app.jinja_env.globals.setdefault("demo_mode", False)
if _demo.enabled():
    _demo.install(sys.modules[__name__])


def start_up():
    """Prepare the database (and, in demo mode, rebuild the demo lab)."""
    if _demo.enabled():
        _demo.build(sys.modules[__name__])
    else:
        init_db_if_needed()


def _serve():
    """Start the app. Uses waitress (a production WSGI server) when installed,
    otherwise Flask's built-in server with a warning. HTTPS: set MDL_SSL_CERT and
    MDL_SSL_KEY to a certificate and key; phones only install the app (PWA) and
    browsers only keep passwords off the network in clear text over HTTPS."""
    host = os.environ.get("MDL_HOST", "0.0.0.0")
    port = int(os.environ.get("MDL_PORT", "5000"))
    cert, key = os.environ.get("MDL_SSL_CERT"), os.environ.get("MDL_SSL_KEY")
    if cert and key:
        print(f"  HTTPS on port {port} with {cert}")
        app.run(host=host, port=port, debug=False, ssl_context=(cert, key), threaded=True)
        return
    print("  Plain HTTP: passwords cross the network unencrypted and phones will not install the app.")
    print("  For HTTPS set MDL_SSL_CERT/MDL_SSL_KEY, or use the Caddy/nginx setup in deploy/.")
    try:
        from waitress import serve
    except ImportError:
        print("  Using Flask's development server (pip install waitress for a production server).")
        app.run(host=host, port=port, debug=False, threaded=True)
        return
    serve(app, host=host, port=port, threads=8)


if __name__ == "__main__":
    start_up()
    _print_startup_diagnostics()
    _serve()
