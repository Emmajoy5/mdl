"""Tests for several people using the library at the same moment.

A lab runs one installation for everybody, so two people pressing Save in the
same second is normal. Until the fix below they could be handed the
same collection ID and one of them lost the isolate. Each test here starts its clients
together on a barrier, so they really do overlap.

Run with:  python test_concurrent.py
"""
import atexit
import os
import shutil
import sqlite3
import tempfile
import threading
import time

_TEST_DATA = tempfile.mkdtemp(prefix="mdl-concurrent-test-")
os.environ["APP_DATA_DIR"] = _TEST_DATA
atexit.register(shutil.rmtree, _TEST_DATA, True)

from app import app, create_lab, db, init_db_if_needed     # noqa: E402

app.config["TESTING"] = True
init_db_if_needed()
failures = []
PEOPLE = 6
PW = "Pi-pass-2026xyz"


def check(label, condition):
    print(f"{label:72s} -> {'OK' if condition else 'FAIL'}")
    if not condition:
        failures.append(label)


def one(sql, args=()):
    conn = db()
    row = conn.execute(sql, args).fetchone()
    conn.close()
    return row


def client():
    c = app.test_client()
    c.post("/login", data=dict(username="pi", password=PW))
    return c


def together(work, n=PEOPLE):
    """Run work(i) in n threads that all start at the same moment."""
    barrier = threading.Barrier(n)
    out = [None] * n

    def go(i):
        barrier.wait()
        try:
            out[i] = work(i)
        except Exception as e:                       # noqa: BLE001 - the point is to report it
            out[i] = f"{type(e).__name__}: {e}"

    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


conn = db()
create_lab(conn, "Busy Lab", "pi", PW, "Dr Pi")
conn.commit()
conn.close()
STORE = dict(storage_freezer="Freezer Z", storage_box="Box Z")

# --- the database is set up to wait its turn ----------------------------------------------------------------------
conn = db()
check("the database reads and writes at the same time (write-ahead logging)",
      conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal")
check("a save waits for a busy database instead of giving up at once",
      conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 10000)
conn.close()

# --- six people adding the same species at once -------------------------------------------------------------------
before = one("SELECT COUNT(*) FROM organisms WHERE genus='Bacillus'")[0]
codes = together(lambda i: client().post("/species/new", data=dict(
    genus="Bacillus", species="subtilis", strain=f"isolate {i}", **STORE)).status_code)
ids = [r[0] for r in db().execute("SELECT collection_id FROM organisms WHERE genus='Bacillus'")]
check(f"{PEOPLE} people adding the same species at once: every isolate is saved", len(ids) - before == PEOPLE)
check("...and nobody gets an error page", all(c in (200, 302) for c in codes))
check("...each with its own collection ID", len(set(ids)) == len(ids))

# --- saving while a long import holds the database ----------------------------------------------------------------
# An import is one transaction, so a save landing mid-import has to wait. Before
# the fix it gave up after 5 seconds and the person lost what they typed.
me = client()                               # sign in first: signing in writes, so it would wait too
holder_ready = threading.Event()
release = threading.Event()


def hold_the_database():
    import app as core
    conn = sqlite3.connect(core.DB_PATH, timeout=30)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE tenants SET name = name")        # a write, so the write lock is held
    holder_ready.set()
    release.wait(25)
    conn.rollback()
    conn.close()


t = threading.Thread(target=hold_the_database, daemon=True)
t.start()
if not holder_ready.wait(5):
    raise SystemExit("the test could not hold the database open")
done = {}


def save_meanwhile():
    start = time.time()
    try:
        r = me.post("/species/new", data=dict(genus="Escherichia", species="coli", strain="waited its turn", **STORE))
        done["code"], done["waited"] = r.status_code, time.time() - start
    except Exception as e:                           # noqa: BLE001 - a refused save must be reported, not silent
        done["code"], done["waited"] = f"{type(e).__name__}: {e}", time.time() - start


saver = threading.Thread(target=save_meanwhile)
saver.start()
time.sleep(7)                                   # longer than the old 5 second limit
check("a save during a long import is still waiting after 7 seconds, not refused", not done)
release.set()
saver.join(20)
t.join(5)
check("...and goes through once the import finishes",
      done.get("code") in (200, 302) and one("SELECT 1 FROM organisms WHERE strain='waited its turn'"))
check("...having waited for it rather than failing", done.get("waited", 0) > 6)

print()
if failures:
    print(f"{len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("All checks passed.")
