"""Command-line export of one lab's catalog to plain files you own.

Run on the machine that hosts the app. Writes the same files as the in-app
Export button (per-table CSVs plus a flat catalog the importer can read back),
with spreadsheet-formula text neutralised:

    python export_catalog.py [out_dir]                   # defaults to ./export-lab1
    python export_catalog.py --tenant 3 [out_dir]        # one lab of an old multi-lab database
    python export_catalog.py --list                      # show lab ids and names

Account tables (users, passwords) are never exported. Feedback contact details
(name, email) are left out of the CLI export; the in-app research export is the
route for questionnaire data.
"""
import argparse
import csv
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("APP_DATA_DIR") or BASE)
DB_PATH = DATA_DIR / "microbe_catalog.db"
TABLES = ["organisms", "accessions", "plate_images", "models", "brewing_relevance", "tags",
          "revival_log", "gram_stain_images", "feedback", "labs", "custom_fields",
          "custom_field_values", "audit_log"]
PERSONAL = {"feedback": {"contact_email", "respondent_name", "name", "email"}}


def _safe(value):
    """Text starting with = + - @ would run as a formula in a spreadsheet;
    prefix it with an apostrophe. Real numbers such as -80 are left alone."""
    if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        try:
            float(value.replace(",", ""))
        except ValueError:
            return "'" + value
    return value


def export_lab(conn, tid, out):
    out.mkdir(parents=True, exist_ok=True)
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in TABLES:
        if table not in have:
            continue
        cur = conn.execute(f"SELECT * FROM {table} WHERE tenant_id=?", (tid,))
        cols = [d[0] for d in cur.description]
        keep = [i for i, c in enumerate(cols) if c not in PERSONAL.get(table, set())]
        with (out / f"{table}.csv").open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([cols[i] for i in keep])
            for row in cur:
                w.writerow([_safe(row[i]) for i in keep])
    # the flat, re-importable catalog: the app's own query, one row per strain
    import app
    cols, rows = app.flat_catalog(conn, tid)
    with (out / "catalog_flat.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([_safe(r[c]) for c in cols])
    (out / "export_info.json").write_text(json.dumps(
        {"tenant_id": tid, "exported_at": datetime.now(timezone.utc).isoformat(),
         "strains": len(rows)}, indent=2), encoding="utf-8")
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="Export one lab's catalog.")
    ap.add_argument("--tenant", type=int)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("out_dir", nargs="?")
    a = ap.parse_args()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if a.tenant is None and not a.list:
        ids = [r[0] for r in conn.execute("SELECT tenant_id FROM tenants")]
        if len(ids) == 1:
            a.tenant = ids[0]
    if a.list or a.tenant is None:
        for r in conn.execute("SELECT tenant_id, name FROM tenants ORDER BY tenant_id"):
            print(f"  {r['tenant_id']:4d}  {r['name']}")
        if a.tenant is None and not a.list:
            print("Choose one with --tenant <id>.")
        return
    out = Path(a.out_dir or f"export-lab{a.tenant}")
    n = export_lab(conn, a.tenant, out)
    conn.close()
    print(f"Exported {n} strains of lab {a.tenant} into {out}/")


if __name__ == "__main__":
    main()
