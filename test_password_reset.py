"""Tests for password recovery: forgotten passwords, reset links and sign-out everywhere.

Run with:  python test_password_reset.py
No mail server is used: the app collects outgoing mail in a list during the tests.
"""
import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

_TEST_DATA = tempfile.mkdtemp(prefix="mdl-pwreset-test-")
os.environ["APP_DATA_DIR"] = _TEST_DATA
for k in ("ADMIN_USERNAME", "ADMIN_PASSWORD", "LAB_NAME", "SMTP_HOST", "SMTP_FROM", "PUBLIC_BASE_URL", "MDL_DEMO"):
    os.environ.pop(k, None)
atexit.register(shutil.rmtree, _TEST_DATA, True)

import app as core                       # noqa: E402
import password_reset as pr              # noqa: E402

core.app.config["TESTING"] = True
core.init_db_if_needed()
failures = []
PW = {"lead": "Lead-pass-2026xyz", "sam": "Sam-pass-2026xyz", "kit": "Kit-pass-2026xyz", "deputy": "Deputy-pass-2026x"}


def check(label, condition):
    print(f"{label:66s} -> {'OK' if condition else 'FAIL'}")
    if not condition:
        failures.append(label)


def one(sql, args=()):
    conn = core.db()
    row = conn.execute(sql, args).fetchone()
    conn.close()
    return row


def client(username=None, password=None):
    c = core.app.test_client()
    if username:
        c.post("/login", data=dict(username=username, password=password or PW[username]))
    return c


def signed_in(c):
    return c.get("/account").status_code == 200


def can_log_in(username, password):
    return signed_in(client(username, password))


def link_from(resp):
    m = re.search(rb"/reset/([A-Za-z0-9_\-]{20,})", resp.data)
    return m.group(1).decode() if m else None


def strip_tokens(body):
    return re.sub(rb'name="csrf_token" value="[^"]*"', b"", body)


def uid(name):
    return one("SELECT user_id FROM users WHERE username=?", (name,))[0]


# --- the lab ----------------------------------------------------------------------------
c0 = core.app.test_client()
c0.post("/setup", data=dict(setup_code=core.setup_code(), lab_name="Brewing QC Lab", username="lead",
                            password=PW["lead"], full_name="Dr Lead"))
c0.post("/admin/users", data=dict(username="sam", password=PW["sam"], role="user", full_name="Sam Okafor"))
c0.post("/admin/users", data=dict(username="kit", password=PW["kit"], role="user", full_name="Kit Ade"))
SAM, KIT, LEAD = uid("sam"), uid("kit"), uid("lead")
lead = client("lead")
check("the lab and its people exist", signed_in(lead) and can_log_in("sam", PW["sam"]))

# --- the forgot page -----------------------------------------------------------------
check("the log-in page links to 'Forgot your password?'", b"Forgot your password?" in client().get("/login").data)
check("the forgot page opens without signing in", client().get("/forgot").status_code == 200)
r_unknown = client().post("/forgot", data=dict(username="nobody-here"))
r_sam = client().post("/forgot", data=dict(username="sam"))
check("the answer is the same whether or not the account exists",
      r_unknown.status_code == r_sam.status_code == 200 and strip_tokens(r_unknown.data) == strip_tokens(r_sam.data))
check("...and tells a lone admin about manage.py reset-link", b"manage.py reset-link" in r_sam.data)
check("without email, Sam's request goes to the lab's admins",
      one("SELECT COUNT(*) FROM password_help_requests WHERE user_id=? AND status='open'", (SAM,))[0] == 1)
client().post("/forgot", data=dict(username="sam"))
check("asking twice in a day keeps one open request",
      one("SELECT COUNT(*) FROM password_help_requests WHERE user_id=? AND status='open'", (SAM,))[0] == 1)
page = lead.get("/admin").data
check("the Admin page lists the request", b"Password help" in page and b"Sam Okafor" in page)
check("...and the Admin menu shows a count", re.search(rb"Admin <span class=\"badge b-warn\">1</span>", lead.get("/").data) is not None)
check("an unknown name creates nothing", one("SELECT COUNT(*) FROM password_help_requests")[0] == 1)

# --- a link from the lab admin -------------------------------------------------------
sam_before = client("sam")
check("Sam is signed in on another computer", signed_in(sam_before))
r = lead.post(f"/admin/users/{SAM}/reset-link")
token1 = link_from(r)
check("the admin gets a link to pass on", r.status_code == 200 and token1 is not None and b"24 hours" in r.data)
check("the link page is not cached or sent on as a referrer",
      r.headers.get("Cache-Control") == "no-store" and r.headers.get("Referrer-Policy") == "no-referrer")
conn = core.db()
dump = "\n".join(" ".join(map(str, row)) for row in conn.execute("SELECT * FROM password_resets"))
conn.close()
check("only a hash of the link is stored", token1 not in dump)
check("making the link closes the request",
      one("SELECT status FROM password_help_requests WHERE user_id=?", (SAM,))[0] == "handled")
check("making the link is in the audit log",
      one("SELECT COUNT(*) FROM audit_log WHERE action='password_link_made' AND entity_id=?", (SAM,))[0] == 1)
token2 = link_from(lead.post(f"/admin/users/{SAM}/reset-link"))
check("a newer link cancels the older one", client().get(f"/reset/{token1}").status_code == 410
      and client().get(f"/reset/{token2}").status_code == 200)
g = client().get(f"/reset/{token2}")
check("the reset page names the account and is not cached", b"sam" in g.data and g.headers.get("Cache-Control") == "no-store")
r = client().post(f"/reset/{token2}", data=dict(new_password="Brand-new-2026", confirm_password="Different-2026"),
                  follow_redirects=True)
check("two different passwords are refused", b"don&#39;t match" in r.data and can_log_in("sam", PW["sam"]))
r = client().post(f"/reset/{token2}", data=dict(new_password="sam", confirm_password="sam"), follow_redirects=True)
check("a weak password is refused", b"at least" in r.data and can_log_in("sam", PW["sam"]))
conn = core.db()
conn.execute("UPDATE users SET failed_login_count=6, locked_until=? WHERE user_id=?",
             ((datetime.now() + timedelta(minutes=15)).isoformat(), SAM))
conn.commit()
conn.close()
r = client().post(f"/reset/{token2}", data=dict(new_password="Sam-new-pass-2027", confirm_password="Sam-new-pass-2027"),
                  follow_redirects=True)
check("a good password is saved", b"password has been changed" in r.data)
check("the lockout was cleared", one("SELECT failed_login_count, locked_until FROM users WHERE user_id=?",
                                     (SAM,))[:] == (0, None))
check("the new password works and the old one does not",
      can_log_in("sam", "Sam-new-pass-2027") and not can_log_in("sam", PW["sam"]))
PW["sam"] = "Sam-new-pass-2027"
check("the link works only once", client().get(f"/reset/{token2}").status_code == 410)
check("Sam's other sign-in has ended", not signed_in(sam_before))
check("using the link is in the audit log",
      one("SELECT COUNT(*) FROM audit_log WHERE action='password_reset_with_link' AND entity_id=?", (SAM,))[0] == 1)

# --- expiry, deactivation and who may make links ------------------------------------
t = link_from(lead.post(f"/admin/users/{KIT}/reset-link"))
conn = core.db()
conn.execute("UPDATE password_resets SET expires_at=? WHERE user_id=? AND used_at IS NULL AND cancelled_at IS NULL",
             ((datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds"), KIT))
conn.commit()
conn.close()
check("an expired link is refused", client().get(f"/reset/{t}").status_code == 410)
t = link_from(lead.post(f"/admin/users/{KIT}/reset-link"))
lead.post(f"/admin/users/{KIT}/toggle")
check("a deactivated account's link is refused", client().get(f"/reset/{t}").status_code == 410)
r = lead.post(f"/admin/users/{KIT}/reset-link", follow_redirects=True)
check("...and no new link is made for it", b"Reactivate the account" in r.data)
lead.post(f"/admin/users/{KIT}/toggle")
check("a lab member who is not an admin cannot make links", client("sam").post(f"/admin/users/{KIT}/reset-link").status_code == 403)
check("...or dismiss requests", client("sam").post("/admin/password-requests/1/dismiss").status_code == 403)
r = lead.post(f"/admin/users/{LEAD}/reset-link")
check("an admin changes their own password on My account instead", r.status_code == 302 and "/account" in r.headers["Location"])
check("a link for an account that does not exist is a 404", lead.post("/admin/users/9999/reset-link").status_code == 404)
client().post("/forgot", data=dict(username="kit"))
req = one("SELECT request_id FROM password_help_requests WHERE user_id=? AND status='open'", (KIT,))[0]
lead.post(f"/admin/password-requests/{req}/dismiss")
check("the lab's admin can dismiss a request", one("SELECT status FROM password_help_requests WHERE request_id=?", (req,))[0] == "dismissed")

# --- passwords set by hand, and own changes ------------------------------------------
kit_before = client("kit")
lead.post(f"/admin/users/{KIT}/reset-password", data=dict(password="Kit-set-by-lead-1"))
PW["kit"] = "Kit-set-by-lead-1"
check("a password the admin sets ends the person's sign-ins", not signed_in(kit_before) and can_log_in("kit", PW["kit"]))
page = lead.get("/admin").data
check("the Admin page lists recent resets, with who made them",
      b"Recent password resets" in page and b"password set by lead" in page and b"link made by lead" in page)
sam_a, sam_b = client("sam"), client("sam")
sam_a.post("/account", data=dict(current_password=PW["sam"], new_password="Sam-own-change-28",
                                 confirm_password="Sam-own-change-28"))
PW["sam"] = "Sam-own-change-28"
check("changing your own password keeps you signed in here", signed_in(sam_a))
check("...and ends your other sign-ins", not signed_in(sam_b))

# --- email ------------------------------------------------------------------------------
os.environ.update(SMTP_HOST="mail.example.org", SMTP_FROM="mdl@example.org", PUBLIC_BASE_URL="https://mdl.example.org")
core.app.config["MAIL_OUTBOX"] = outbox = []
sam = client("sam")
r = sam.post("/account/email", data=dict(email="not-an-address"), follow_redirects=True)
check("a malformed email address is refused", b"doesn&#39;t look right" in r.data
      and one("SELECT email FROM users WHERE user_id=?", (SAM,))[0] is None)
sam.post("/account/email", data=dict(email="sam.okafor@example.org"))
check("Sam can save an email address", one("SELECT email FROM users WHERE user_id=?", (SAM,))[0] == "sam.okafor@example.org")
before = one("SELECT COUNT(*) FROM password_help_requests")[0]
r = client().post("/forgot", data=dict(username="Sam.Okafor@example.org"))
check("asking by email address sends one email", len(outbox) == 1 and outbox[0]["to"] == "sam.okafor@example.org")
check("the page says the same as before", strip_tokens(r.data) == strip_tokens(r_unknown.data))
mail_token = re.search(r"https://mdl\.example\.org/reset/([A-Za-z0-9_\-]+)", outbox[0]["body"])
check("the email holds a link to the lab's public address, valid for 60 minutes",
      mail_token is not None and "60 minutes" in outbox[0]["body"])
check("no request goes to the admins when the email was sent", one("SELECT COUNT(*) FROM password_help_requests")[0] == before)
client().post(f"/reset/{mail_token.group(1)}", data=dict(new_password="Sam-by-email-2029", confirm_password="Sam-by-email-2029"))
PW["sam"] = "Sam-by-email-2029"
check("the emailed link works", can_log_in("sam", PW["sam"]))
for _ in range(5):
    client().post("/forgot", data=dict(username="sam"))
check("no more than three emails an hour go to one account", len(outbox) == 3)
conn = core.db()
conn.execute("UPDATE users SET email='sam.okafor@example.org' WHERE user_id=?", (KIT,))
conn.commit()
conn.close()
n = len(outbox)
client().post("/forgot", data=dict(username="sam.okafor@example.org"))
check("an address shared by two accounts sends nothing", len(outbox) == n)
conn = core.db()
conn.execute("UPDATE users SET email=NULL WHERE user_id=?", (KIT,))
conn.execute("DELETE FROM password_resets WHERE user_id=? AND via='email'", (SAM,))
conn.commit()
conn.close()
real_send = pr.send_mail


def broken(*a, **k):
    raise OSError("mail server unreachable")


pr.send_mail = broken
client().post("/forgot", data=dict(username="sam"))
pr.send_mail = real_send
check("if the email cannot be sent, the admins are asked instead",
      one("SELECT COUNT(*) FROM password_help_requests WHERE user_id=? AND status='open'", (SAM,))[0] == 1
      and one("SELECT COUNT(*) FROM password_resets WHERE user_id=? AND via='email' AND cancelled_at IS NULL "
              "AND used_at IS NULL", (SAM,))[0] == 0)
for k in ("SMTP_HOST", "SMTP_FROM", "PUBLIC_BASE_URL"):
    os.environ.pop(k)
core.app.config["MAIL_OUTBOX"] = None

# --- people waiting for approval, and the CSRF token ---------------------------------
code = one("SELECT join_code FROM tenants")[0]
core.app.test_client().post("/signup", data=dict(join_code=code, username="newbie", password="Newbie-pass-2026"))
client().post("/forgot", data=dict(username="newbie"))
check("someone still waiting for approval gets no request or link",
      one("SELECT COUNT(*) FROM password_help_requests r JOIN users u ON u.user_id=r.user_id "
          "WHERE u.username='newbie'")[0] == 0)
t = link_from(lead.post(f"/admin/users/{KIT}/reset-link"))
core.app.config["TESTING"] = False
check("the forgot form needs the CSRF token", client().post("/forgot", data=dict(username="sam")).status_code in (400, 403))
check("the reset form needs the CSRF token", client().post(f"/reset/{t}", data=dict(
    new_password="Kit-no-token-12", confirm_password="Kit-no-token-12")).status_code in (400, 403))
core.app.config["TESTING"] = True

# --- the lab's only admin is locked out: manage.py -----------------------------------
here = os.path.dirname(os.path.abspath(__file__))
env = dict(os.environ, APP_DATA_DIR=_TEST_DATA, PUBLIC_BASE_URL="https://mdl.example.org")
client().post("/forgot", data=dict(username="lead"))
out = subprocess.run([sys.executable, os.path.join(here, "manage.py"), "reset-link", "lead"], capture_output=True,
                     text=True, cwd=here, env=env)
m = re.search(r"https://mdl\.example\.org/reset/([A-Za-z0-9_\-]+)", out.stdout)
check("manage.py reset-link prints a link", out.returncode == 0 and m is not None)
client().post(f"/reset/{m.group(1)}", data=dict(new_password="Lead-new-pass-2030", confirm_password="Lead-new-pass-2030"))
PW["lead"] = "Lead-new-pass-2030"
check("...that lets the lab's only admin back in", can_log_in("lead", PW["lead"]))
check("...and closes their request",
      one("SELECT COUNT(*) FROM password_help_requests WHERE user_id=? AND status='open'", (LEAD,))[0] == 0)
lead = client("lead")
check("the lab sees the link was made on the server", b"link made on the server" in lead.get("/admin").data)
out = subprocess.run([sys.executable, os.path.join(here, "manage.py"), "reset-link", "no-such-person"],
                     capture_output=True, text=True, cwd=here, env=env)
check("...and refuses an unknown account", out.returncode != 0 and "no account" in out.stderr)
kit_before = client("kit")
conn = core.db()
core.reset_password(conn, "kit", "Kit-from-server-31")
conn.close()
PW["kit"] = "Kit-from-server-31"
check("manage.py reset-password ends the person's other sign-ins", not signed_in(kit_before) and can_log_in("kit", PW["kit"]))
check("...and is listed and logged", b"password set on the server" in lead.get("/admin").data
      and one("SELECT COUNT(*) FROM audit_log WHERE action='password_set_on_server' AND entity_id=?", (KIT,))[0] == 1)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("All checks passed.")
