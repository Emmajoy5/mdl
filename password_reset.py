"""Password recovery.

Forgot your password?
    The sign-in page links to /forgot. A person enters their username (or the
    email address on their account). If the account has an email address and
    the server can send mail (SMTP_HOST, SMTP_FROM and PUBLIC_BASE_URL are set),
    a reset link valid for 60 minutes is emailed to them. Otherwise the lab's
    administrators see a request on the Admin page and make a link for them.
    The page says the same thing whether or not the account exists.

Reset links
    A lab administrator can make a single-use link for anyone in the lab, valid
    for 24 hours, and pass it on in person or by a message only that person
    reads. The administrator never learns the new password. If the lab's only
    administrator is locked out, whoever runs the server makes a link with
    `python manage.py reset-link USERNAME`.

    Links are stored only as SHA-256 hashes. Making a new link cancels older
    ones. Using a link changes the password, clears any lockout and signs the
    account out everywhere else. Every link made, used or cancelled stays in the
    lab's list of password resets, and each step is written to the audit log.
"""
import hashlib
import os
import re
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage

from flask import Blueprint, abort, flash, make_response, redirect, render_template, request, session, url_for

bp = Blueprint("pwreset", __name__)
core = None

PRODUCT = "Microbial Digital Library"
PUBLIC = {"pwreset.forgot", "pwreset.reset"}
EMAIL_LINK_MINUTES = 60
ADMIN_LINK_HOURS = 24
EMAILS_PER_HOUR = 3                  # per account, so nobody can flood someone's inbox
REQUEST_REPEAT_HOURS = 24            # one open request per person at a time
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

VIA_LABELS = {
    "email": "emailed after a request",
    "admin": "link made by",
    "server": "link made on the server",
    "set": "password set by",
    "server-set": "password set on the server",
}

COLUMNS = {"email": "TEXT", "session_stamp": "TEXT"}
TABLES = """
CREATE TABLE IF NOT EXISTS password_resets (
    reset_id     INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(user_id),
    tenant_id    INTEGER REFERENCES tenants(tenant_id),
    token_hash   TEXT    NOT NULL UNIQUE,
    via          TEXT    NOT NULL,
    made_by      INTEGER REFERENCES users(user_id),
    created_at   TEXT    NOT NULL,
    expires_at   TEXT    NOT NULL,
    used_at      TEXT,
    cancelled_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_resets(user_id);
CREATE INDEX IF NOT EXISTS idx_password_resets_tenant ON password_resets(tenant_id, created_at);
CREATE TABLE IF NOT EXISTS password_help_requests (
    request_id   INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(user_id),
    tenant_id    INTEGER REFERENCES tenants(tenant_id),
    requested_at TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'open',
    handled_by   INTEGER REFERENCES users(user_id),
    handled_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_password_help_open ON password_help_requests(tenant_id, status);
"""


def init(app_module):
    global core
    core = app_module
    app_module.app.register_blueprint(bp)
    app_module.PUBLIC_ENDPOINTS.update(PUBLIC)
    g = app_module.app.jinja_env.globals
    g["password_help_count"] = password_help_count
    g["password_help_requests"] = lab_requests
    g["recent_password_resets"] = lab_resets
    g["mail_ready"] = mail_ready


def ensure_schema(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    for name, decl in COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {decl}")
    conn.executescript(TABLES)
    conn.commit()


def _now():
    return datetime.now()


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _audit(conn, action, user, detail, actor=None):
    """One audit-log row; outside a signed-in request the actor is named in the detail."""
    detail = dict(detail, account=user["username"])
    if actor:
        detail["by"] = actor
    try:
        core.audit(conn, action, "user", user["user_id"], detail, tenant=user["tenant_id"])
    except RuntimeError:            # no request (manage.py): write the row directly
        conn.execute("INSERT INTO audit_log (tenant_id, username, action, entity, entity_id, detail) "
                     "VALUES (?,?,?,?,?,?)", (user["tenant_id"], actor, action, "user", user["user_id"],
                                             core.json.dumps(detail)))


# --------------------------------------------------------------------------- #
# Sessions: a new stamp signs the account out everywhere
# --------------------------------------------------------------------------- #
def new_stamp(conn, user_id):
    stamp = secrets.token_hex(16)
    conn.execute("UPDATE users SET session_stamp=? WHERE user_id=?", (stamp, user_id))
    return stamp


def session_matches(row):
    """False when the account's password was reset or changed after this
    browser signed in. Sessions from before stamps existed match an empty stamp."""
    try:
        stamp = row["session_stamp"] or ""
    except (IndexError, KeyError):
        return True
    return stamp == (session.get("stamp") or "")


def set_password(conn, user_id, password_hash, keep_session=False):
    """Change a password, clear any lockout and sign the account out
    everywhere. With keep_session, this browser stays signed in."""
    conn.execute("UPDATE users SET password_hash=?, failed_login_count=0, locked_until=NULL WHERE user_id=?",
                 (password_hash, user_id))
    stamp = new_stamp(conn, user_id)
    if keep_session:
        session["stamp"] = stamp
    return stamp


# --------------------------------------------------------------------------- #
# Links and requests
# --------------------------------------------------------------------------- #
def make_link(conn, user, via, made_by=None, minutes=None):
    """A new single-use token for `user`; older unused ones are cancelled.
    Returns the token (only its hash is stored)."""
    now = _now()
    minutes = minutes or (EMAIL_LINK_MINUTES if via == "email" else ADMIN_LINK_HOURS * 60)
    conn.execute("UPDATE password_resets SET cancelled_at=? WHERE user_id=? AND used_at IS NULL "
                 "AND cancelled_at IS NULL", (_iso(now), user["user_id"]))
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO password_resets (user_id, tenant_id, token_hash, via, made_by, created_at, expires_at) "
                 "VALUES (?,?,?,?,?,?,?)", (user["user_id"], user["tenant_id"], _hash(token), via, made_by,
                                             _iso(now), _iso(now + timedelta(minutes=minutes))))
    if via != "email":
        conn.execute("UPDATE password_help_requests SET status='handled', handled_by=?, handled_at=? "
                     "WHERE user_id=? AND status='open'", (made_by, _iso(now), user["user_id"]))
    return token


def record_set(conn, user, by_id, via="set"):
    """A password typed by an administrator (or on the server): kept in the list of resets."""
    now = _iso(_now())
    conn.execute("INSERT INTO password_resets (user_id, tenant_id, token_hash, via, made_by, created_at, "
                 "expires_at, used_at) VALUES (?,?,?,?,?,?,?,?)",
                 (user["user_id"], user["tenant_id"], _hash(secrets.token_urlsafe(32)), via, by_id, now, now, now))
    conn.execute("UPDATE password_help_requests SET status='handled', handled_by=?, handled_at=? "
                 "WHERE user_id=? AND status='open'", (by_id, now, user["user_id"]))


def link_url(token, base=None):
    base = (base or os.environ.get("PUBLIC_BASE_URL", "").strip() or request.url_root).rstrip("/")
    return f"{base}/reset/{token}"


def _valid_reset(conn, token):
    """(reset row, user row) for a usable token, else (None, None)."""
    if not token or len(token) > 100:
        return None, None
    row = conn.execute("SELECT * FROM password_resets WHERE token_hash=?", (_hash(token),)).fetchone()
    if row is None or row["used_at"] or row["cancelled_at"] or row["expires_at"] < _iso(_now()):
        return None, None
    user = conn.execute("SELECT * FROM users WHERE user_id=? AND is_active=1 AND pending_approval=0",
                        (row["user_id"],)).fetchone()
    if user is None:
        return None, None
    if user["tenant_id"] is not None:
        t = conn.execute("SELECT is_active FROM tenants WHERE tenant_id=?", (user["tenant_id"],)).fetchone()
        if t is None or not t["is_active"]:
            return None, None
    return row, user


def _open_request(conn, user):
    since = _iso(_now() - timedelta(hours=REQUEST_REPEAT_HOURS))
    if conn.execute("SELECT 1 FROM password_help_requests WHERE user_id=? AND status='open' AND requested_at>=?",
                    (user["user_id"], since)).fetchone():
        return
    conn.execute("UPDATE password_help_requests SET status='expired' WHERE user_id=? AND status='open'",
                 (user["user_id"],))
    conn.execute("INSERT INTO password_help_requests (user_id, tenant_id, requested_at) VALUES (?,?,?)",
                 (user["user_id"], user["tenant_id"], _iso(_now())))


# --------------------------------------------------------------------------- #
# Email (optional)
# --------------------------------------------------------------------------- #
def mail_ready():
    return all(os.environ.get(k, "").strip() for k in ("SMTP_HOST", "SMTP_FROM", "PUBLIC_BASE_URL"))


def send_mail(to, subject, body):
    outbox = core.app.config.get("MAIL_OUTBOX")
    if outbox is not None:                       # tests collect mail instead of sending it
        outbox.append(dict(to=to, subject=subject, body=body))
        return
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_FROM"].strip()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    host = os.environ["SMTP_HOST"].strip()
    security = os.environ.get("SMTP_SECURITY", "starttls").strip().lower()
    port = int(os.environ.get("SMTP_PORT", "465" if security == "ssl" else "587"))
    ctx = ssl.create_default_context()
    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=15)
    else:
        server = smtplib.SMTP(host, port, timeout=15)
        if security == "starttls":
            server.starttls(context=ctx)
    with server:
        if os.environ.get("SMTP_USERNAME"):
            server.login(os.environ["SMTP_USERNAME"], os.environ.get("SMTP_PASSWORD", ""))
        server.send_message(msg)


def _email_link(conn, user):
    """True if a link was emailed. Falls back (False) when mail is not set up,
    the account has no address, or sending failed."""
    if not mail_ready() or not (user["email"] or "").strip():
        return False
    hour_ago = _iso(_now() - timedelta(hours=1))
    sent = conn.execute("SELECT COUNT(*) FROM password_resets WHERE user_id=? AND via='email' AND created_at>=?",
                        (user["user_id"], hour_ago)).fetchone()[0]
    if sent >= EMAILS_PER_HOUR:
        return True                              # say nothing new; earlier links still work
    token = make_link(conn, user, "email")
    lab = conn.execute("SELECT name FROM tenants WHERE tenant_id=?", (user["tenant_id"],)).fetchone()
    body = (f"Hello {user['full_name'] or user['username']},\n\n"
            f"Someone asked to reset the password for your {PRODUCT} account ({user['username']})"
            f"{' in ' + lab['name'] if lab else ''}. If it was you, open this link within "
            f"{EMAIL_LINK_MINUTES} minutes and choose a new password:\n\n{link_url(token)}\n\n"
            f"The link works once. If you did not ask for this, ignore this email and your "
            f"password stays as it is.\n\n{PRODUCT}\n")
    try:
        send_mail(user["email"].strip(), f"Reset your {PRODUCT} password", body)
    except Exception as exc:                     # noqa: BLE001  (mail server down: fall back to the admins)
        core.app.logger.warning("Password reset email could not be sent: %s", exc)
        conn.execute("UPDATE password_resets SET cancelled_at=? WHERE token_hash=?", (_iso(_now()), _hash(token)))
        return False
    _audit(conn, "password_link_emailed", user, {})
    return True


# --------------------------------------------------------------------------- #
# Lists for the Admin page
# --------------------------------------------------------------------------- #
def password_help_count():
    u = core.current_user()
    if u is None or u["role"] != "admin":
        return 0
    conn = core.db()
    n = conn.execute("SELECT COUNT(*) FROM password_help_requests r JOIN users x ON x.user_id=r.user_id "
                     "WHERE r.tenant_id=? AND r.status='open' AND x.is_active=1 AND r.user_id<>?",
                     (u["tenant_id"], u["user_id"])).fetchone()[0]
    conn.close()
    return n


def lab_requests():
    u = core.current_user()
    conn = core.db()
    rows = conn.execute("SELECT r.*, x.username, x.full_name, x.role FROM password_help_requests r "
                        "JOIN users x ON x.user_id=r.user_id WHERE r.tenant_id=? AND r.status='open' "
                        "AND x.is_active=1 AND r.user_id<>? ORDER BY r.requested_at",
                        (u["tenant_id"], u["user_id"])).fetchall()
    conn.close()
    return rows


def _state(r):
    if r["via"] in ("set", "server-set") or r["used_at"]:
        return "used"
    if r["cancelled_at"]:
        return "cancelled"
    return "expired" if r["expires_at"] < _iso(_now()) else "waiting"


def lab_resets(limit=15):
    u = core.current_user()
    conn = core.db()
    rows = conn.execute("SELECT p.*, x.username, m.username AS made_by_name FROM password_resets p "
                        "JOIN users x ON x.user_id=p.user_id LEFT JOIN users m ON m.user_id=p.made_by "
                        "WHERE p.tenant_id=? ORDER BY p.reset_id DESC LIMIT ?", (u["tenant_id"], limit)).fetchall()
    conn.close()
    out = []
    for r in rows:
        how = VIA_LABELS.get(r["via"], r["via"])
        if r["via"] in ("admin", "set"):
            how = f"{how} {r['made_by_name'] or 'an administrator'}"
        out.append(dict(username=r["username"], how=how, created_at=r["created_at"].replace("T", " "),
                        state=_state(r)))
    return out


def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


# --------------------------------------------------------------------------- #
# Routes: asking for help, and using a link
# --------------------------------------------------------------------------- #
@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "GET":
        return render_template("forgot.html", sent=False)
    if not core.app.config.get("TESTING") and core._ip_rate_limited(f"forgot:{core._client_ip()}"):
        flash("Too many requests from this connection. Wait a few minutes and try again.")
        return redirect(url_for("pwreset.forgot"))
    ident = request.form.get("username", "").strip()[:200]
    conn = core.db()
    user = None
    if ident:
        user = conn.execute("SELECT * FROM users WHERE username=? AND is_active=1 AND pending_approval=0",
                            (ident,)).fetchone()
        if user is None and "@" in ident:
            found = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?) AND is_active=1 "
                                 "AND pending_approval=0", (ident,)).fetchall()
            user = found[0] if len(found) == 1 else None
    if user is not None and user["tenant_id"] is not None:
        t = conn.execute("SELECT is_active FROM tenants WHERE tenant_id=?", (user["tenant_id"],)).fetchone()
        if t is not None and t["is_active"]:
            if not _email_link(conn, user):
                _open_request(conn, user)
            conn.commit()
    conn.close()
    return render_template("forgot.html", sent=True)


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    conn = core.db()
    row, user = _valid_reset(conn, token)
    if row is None:
        conn.close()
        return _no_store(make_response(render_template("reset_password.html", target=None), 410))
    if request.method == "POST":
        if not core.app.config.get("TESTING") and core._ip_rate_limited(f"reset:{core._client_ip()}"):
            conn.close()
            flash("Too many attempts from this connection. Wait a few minutes and try again.")
            return redirect(url_for("pwreset.reset", token=token))
        new = request.form.get("new_password", "")
        if new != request.form.get("confirm_password", ""):
            problem = "The two passwords don't match."
        else:
            problem = core._password_problem(new, user["username"])
        if problem:
            conn.close()
            flash(problem)
            return redirect(url_for("pwreset.reset", token=token))
        now = _iso(_now())
        set_password(conn, user["user_id"], core.generate_password_hash(new))
        conn.execute("UPDATE password_resets SET used_at=? WHERE reset_id=?", (now, row["reset_id"]))
        conn.execute("UPDATE password_resets SET cancelled_at=? WHERE user_id=? AND used_at IS NULL "
                     "AND cancelled_at IS NULL", (now, user["user_id"]))
        conn.execute("UPDATE password_help_requests SET status='handled', handled_at=? "
                     "WHERE user_id=? AND status='open'", (now, user["user_id"]))
        _audit(conn, "password_reset_with_link", user, {"link": row["via"]}, actor=user["username"])
        conn.commit()
        conn.close()
        session.clear()
        flash("Your password has been changed and any other sign-ins have ended. Log in with the new password.")
        return redirect(url_for("login"))
    conn.close()
    return _no_store(make_response(render_template("reset_password.html", target=user,
                                                   min_length=core.MIN_PASSWORD_LENGTH)))


# --------------------------------------------------------------------------- #
# Routes: lab administrators
# --------------------------------------------------------------------------- #
def _lab_member(conn, user_id):
    me = core.current_user()
    if me is None or me["role"] != "admin":
        abort(403)
    target = conn.execute("SELECT * FROM users WHERE user_id=? AND tenant_id=? AND pending_approval=0",
                          (user_id, me["tenant_id"])).fetchone()
    if target is None:
        abort(404)
    return me, target


@bp.route("/admin/users/<int:user_id>/reset-link", methods=["POST"])
def admin_link(user_id):
    conn = core.db()
    me, target = _lab_member(conn, user_id)
    if not target["is_active"]:
        conn.close()
        flash("Reactivate the account before making a reset link.")
        return redirect(url_for("admin_home"))
    if target["user_id"] == me["user_id"]:
        conn.close()
        flash("Change your own password from My account.")
        return redirect(url_for("account"))
    token = make_link(conn, target, "admin", made_by=me["user_id"])
    _audit(conn, "password_link_made", target, {"valid_hours": ADMIN_LINK_HOURS})
    conn.commit()
    conn.close()
    resp = make_response(render_template("reset_link.html", target=target, link=link_url(token),
                                         hours=ADMIN_LINK_HOURS))
    return _no_store(resp)


@bp.route("/admin/password-requests/<int:request_id>/dismiss", methods=["POST"])
def admin_dismiss(request_id):
    me = core.current_user()
    if me is None or me["role"] != "admin":
        abort(403)
    conn = core.db()
    conn.execute("UPDATE password_help_requests SET status='dismissed', handled_by=?, handled_at=? "
                 "WHERE request_id=? AND tenant_id=? AND status='open'",
                 (me["user_id"], _iso(_now()), request_id, me["tenant_id"]))
    conn.commit()
    conn.close()
    flash("Request dismissed.")
    return redirect(url_for("admin_home"))


# --------------------------------------------------------------------------- #
# Route: the email address on My account
# --------------------------------------------------------------------------- #
@bp.route("/account/email", methods=["POST"])
def account_email():
    me = core.current_user()
    email = request.form.get("email", "").strip()[:200]
    if email and not EMAIL_RE.match(email):
        flash("That email address doesn't look right. Fix it, or leave it empty.")
        return redirect(url_for("account"))
    conn = core.db()
    conn.execute("UPDATE users SET email=? WHERE user_id=?", (email or None, me["user_id"]))
    conn.commit()
    conn.close()
    flash("Email address saved." if email else "Email address removed.")
    return redirect(url_for("account"))
