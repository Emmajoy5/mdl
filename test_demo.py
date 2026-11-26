"""Checks for the public demo mode (MDL_DEMO=1). Runs in its own temporary data
folder, because the demo deletes and rebuilds everything it finds there.

    python test_demo.py
"""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp(prefix="mdl-demo-")
os.environ.update(APP_DATA_DIR=tmp, MDL_DEMO="1", MDL_DEMO_RESET_HOURS="6")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as A  # noqa: E402
import demo  # noqa: E402

failures = []


def check(label, ok):
    print(f"{label:60s} -> {'OK' if ok else 'FAIL'}")
    if not ok:
        failures.append(label)


A.app.config["TESTING"] = True
A.start_up()
c = A.app.test_client()
conn = A.db()
tid = conn.execute("SELECT tenant_id FROM tenants WHERE name=?", (demo.DEMO_LAB,)).fetchone()[0]
n_org = conn.execute("SELECT COUNT(*) FROM organisms WHERE tenant_id=?", (tid,)).fetchone()[0]
n_img = conn.execute("SELECT COUNT(*) FROM plate_images WHERE tenant_id=?", (tid,)).fetchone()[0]
n_mod = conn.execute("SELECT COUNT(*) FROM models WHERE tenant_id=?", (tid,)).fetchone()[0]
conn.close()
check("demo: lab built with the example panel", n_org == 7)
check("demo: real plate photographs attached", n_img == 3)
check("demo: photo reliefs and procedural models built", n_mod >= 9)
r = c.get("/login")
check("demo: sign-in page shows the demo account", demo.DEMO_PASSWORD.encode() in r.data)
r = c.post("/login", data=dict(username=demo.DEMO_USERNAME, password=demo.DEMO_PASSWORD), follow_redirects=True)
check("demo: demo account signs in", r.status_code == 200 and b"Public demo" in r.data)
check("demo: banner warns that data is wiped", b"Do not enter real data" in r.data)
for path, method in [("/account", "get"), ("/admin/users", "post"), ("/admin/lab-name", "post"),
                     ("/admin/join-code/regenerate", "post"), ("/contact", "get"),
                     ("/forgot", "post"), ("/account/email", "post"), ("/admin/users/1/reset-link", "post")]:
    r = getattr(c, method)(path, follow_redirects=True)
    check(f"demo: {path} is blocked", b"available in the public demo" in r.data)
check("demo: the log-in page has no 'Forgot your password?' link",
      b"Forgot your password?" not in A.app.test_client().get("/login").data)
r = c.get("/signup", follow_redirects=True)
check("demo: sign-up is off", b"Sign-up is off in the public demo" in r.data)
check("demo: upload limit is 8 MB", A.app.config["MAX_CONTENT_LENGTH"] == 8 * 1024 * 1024)
r = c.get("/species/MDL-BACSUB-007")
check("demo: strain page with a photo relief renders", r.status_code == 200 and b"photo_relief" in r.data)
# a reset removes what a visitor added
c.post("/species/new", data=dict(genus="Visitor", species="added", storage_freezer="F", storage_box="B"))
demo.build(A)
conn = A.db()
gone = conn.execute("SELECT COUNT(*) FROM organisms WHERE genus='Visitor'").fetchone()[0] == 0
conn.close()
check("demo: a reset removes what visitors added", gone)
print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    raise SystemExit(1)
print("All demo checks passed.")
