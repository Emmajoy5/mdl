"""Server-side tasks for the Microbial Digital Library. Run them on the machine
that hosts the app; anyone who can do that already has the app's files, so these
commands ask for nothing else. They honour APP_DATA_DIR like the app does.

    python manage.py list-users
    python manage.py reset-password USERNAME
    python manage.py reset-link USERNAME
    python manage.py make-admin USERNAME
    python manage.py list-labs
    python manage.py split-lab LAB_ID NEW_FOLDER
    python manage.py compact-photos [--apply]

reset-password also clears a sign-in lockout and ends the account's other
sign-ins. reset-link prints a single-use link, valid for 24 hours, so the person
chooses their own password (useful when the lab's only admin is locked out). make-admin helps when the lab's
only admin has left. list-labs and split-lab are for databases from versions
4.0 to 4.6 that held several labs: from 4.7 each lab has its own installation,
so split-lab copies one lab's records and files into a new data folder.
compact-photos stores plate and Gram stain photos uploaded before photos were
compacted on upload in less space (see photo_store.py): it lists what it would
save, and changes the files only with --apply. Make a backup first.
"""
import getpass
import shutil
import sqlite3
import sys
from pathlib import Path

import app as mdl


def _raw(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def list_users():
    mdl.init_db_if_needed()
    conn = mdl.db()
    rows = conn.execute("SELECT username, role, full_name, is_active, pending_approval FROM users "
                        "ORDER BY role, username").fetchall()
    conn.close()
    for r in rows:
        state = "waiting for approval" if r["pending_approval"] else ("active" if r["is_active"] else "deactivated")
        print(f"{r['username']:24s} {r['role']:6s} {state:22s} {r['full_name'] or ''}")


def reset_password(username):
    mdl.init_db_if_needed()
    pw1 = getpass.getpass(f"New password for {username}: ")
    pw2 = getpass.getpass("Repeat it: ")
    if pw1 != pw2:
        sys.exit("The two passwords differ. Nothing was changed.")
    problem = mdl._password_problem(pw1, username)
    if problem:
        sys.exit(f"{problem} Nothing was changed.")
    conn = mdl.db()
    done = mdl.reset_password(conn, username, pw1)
    conn.close()
    if not done:
        sys.exit(f"There is no account called \"{username}\". See: python manage.py list-users")
    print(f"Done. {username} can sign in with the new password.")


def reset_link(username):
    import os
    import password_reset
    mdl.init_db_if_needed()
    conn = mdl.db()
    user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if user is None:
        conn.close()
        sys.exit(f"There is no account called \"{username}\". See: python manage.py list-users")
    if not user["is_active"] or user["pending_approval"]:
        conn.close()
        sys.exit("That account is deactivated or still waiting for approval. Reactivate or approve it first.")
    token = password_reset.make_link(conn, user, "server", minutes=password_reset.ADMIN_LINK_HOURS * 60)
    password_reset._audit(conn, "password_link_made_on_server", user, {})
    conn.commit()
    conn.close()
    base = os.environ.get("PUBLIC_BASE_URL", "").strip() or "http://localhost:5000"
    print(f"Reset link for {username}, valid for {password_reset.ADMIN_LINK_HOURS} hours and usable once:\n")
    print(password_reset.link_url(token, base=base))
    if not os.environ.get("PUBLIC_BASE_URL"):
        print("\n(PUBLIC_BASE_URL is not set, so the address above may need changing to the one your lab uses.)")


def make_admin(username):
    mdl.init_db_if_needed()
    conn = mdl.db()
    n = conn.execute("UPDATE users SET role='admin', is_active=1, pending_approval=0 WHERE username=?",
                     (username,)).rowcount
    conn.commit()
    conn.close()
    if not n:
        sys.exit(f"There is no account called \"{username}\". See: python manage.py list-users")
    print(f"Done. {username} is now a lab admin.")


def list_labs():
    conn = _raw(mdl.DB_PATH)
    for r in conn.execute("SELECT t.tenant_id, t.name, (SELECT COUNT(*) FROM organisms o "
                          "WHERE o.tenant_id=t.tenant_id) AS strains FROM tenants t ORDER BY t.tenant_id"):
        print(f"{r['tenant_id']:4d}  {r['name']}  ({r['strains']} strains)")
    conn.close()


def split_lab(lab_id, folder):
    lab_id = int(lab_id)
    dest = Path(folder).resolve()
    if dest.exists() and any(dest.iterdir()):
        sys.exit(f"{dest} is not empty. Choose a new, empty folder.")
    src = _raw(mdl.DB_PATH)
    if src.execute("SELECT 1 FROM tenants WHERE tenant_id=?", (lab_id,)).fetchone() is None:
        sys.exit(f"There is no lab {lab_id}. See: python manage.py list-labs")
    (dest / "uploads").mkdir(parents=True)
    (dest / "models").mkdir()
    out_path = dest / mdl.DB_PATH.name
    out = sqlite3.connect(out_path)
    src.backup(out)
    src.close()
    out.row_factory = sqlite3.Row
    out.execute("PRAGMA foreign_keys = ON")
    tables = [r[0] for r in out.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                        "AND name NOT LIKE 'sqlite_%'")]
    cols = {t: [c[1] for c in out.execute(f"PRAGMA table_info({t})")] for t in tables}
    # strains first, accounts last: other tables (password resets, say) refer to users
    order = ["organisms"] + [t for t in tables if t not in ("organisms", "tenants", "users")] + ["users"]
    for t in order:
        if "tenant_id" in cols[t]:
            out.execute(f"DELETE FROM {t} WHERE tenant_id IS NOT NULL AND tenant_id != ?", (lab_id,))
    for t in tables:                      # rows that hang off a removed strain
        if t != "organisms" and "organism_id" in cols[t]:
            out.execute(f"DELETE FROM {t} WHERE organism_id IS NOT NULL "
                        "AND organism_id NOT IN (SELECT organism_id FROM organisms)")
    out.execute("DELETE FROM tenants WHERE tenant_id != ?", (lab_id,))
    out.execute("DELETE FROM users WHERE role='superadmin'")
    out.commit()
    copied = 0
    for t in ("plate_images", "models", "gram_stain_images"):
        if t in cols and "file_path" in cols[t]:
            for (rel,) in out.execute(f"SELECT file_path FROM {t} WHERE file_path IS NOT NULL"):
                f = mdl.DATA_DIR / rel
                if f.is_file():
                    (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest / rel)
                    copied += 1
    out.execute("VACUUM")
    out.close()
    print(f"Copied lab {lab_id} and {copied} photo and model files to {dest}.")
    print(f"Start its installation with APP_DATA_DIR={dest}")


def compact_photos(*args):
    """Store existing photos in less space. Without --apply, only reports."""
    import photo_store
    apply = "--apply" in args
    mdl.init_db_if_needed()
    conn = mdl.db()
    before = after = changed = 0
    for table, key in (("plate_images", "image_id"), ("gram_stain_images", "image_id")):
        rows = conn.execute(f"SELECT {key}, file_path FROM {table} WHERE file_path IS NOT NULL").fetchall()
        for row in rows:
            path = mdl.DATA_DIR / row["file_path"]
            if not path.is_file():
                continue
            data = path.read_bytes()
            try:
                new, ext, info = photo_store.compact(data, path.suffix.lower())
            except Exception as exc:
                print(f"  skipped {row['file_path']}: {exc}")
                continue
            if not info["changed"]:
                continue
            changed += 1
            before += len(data)
            after += len(new)
            print(f"  {row['file_path']}: {photo_store.describe(info)}")
            if apply:
                target = path.with_suffix(ext)
                tmp = target.with_name(target.name + ".tmp")
                tmp.write_bytes(new)
                tmp.replace(target)
                if target != path:
                    path.unlink()
                    rel = str(Path(row["file_path"]).with_suffix(ext)).replace("\\", "/")
                    conn.execute(f"UPDATE {table} SET file_path=? WHERE {key}=?", (rel, row[key]))
    if apply:
        conn.commit()
    conn.close()
    if not changed:
        print("Nothing to do: every stored photo is already compact.")
        return
    verb = "Stored" if apply else "Would store"
    print(f"{verb} {changed} photo(s) in {after / 1e6:.1f} MB instead of {before / 1e6:.1f} MB.")
    if not apply:
        print("Run again with --apply to do it (make a backup first).")


COMMANDS = {"list-users": (list_users, 0), "reset-password": (reset_password, 1), "reset-link": (reset_link, 1),
            "make-admin": (make_admin, 1), "list-labs": (list_labs, 0), "split-lab": (split_lab, 2),
            "compact-photos": (compact_photos, (0, 1))}

if __name__ == "__main__":
    def _fits(cmd, n):
        want = COMMANDS[cmd][1]
        return n in want if isinstance(want, tuple) else n == want
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS or not _fits(sys.argv[1], len(sys.argv) - 2):
        sys.exit(__doc__)
    fn, _ = COMMANDS[sys.argv[1]]
    fn(*sys.argv[2:])
