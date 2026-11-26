"""Regenerate the procedural 3D models for every strain, from the command line.

The same builder the web app's "Generate 3D model" button uses (model_builder.py),
so batch output matches the app: file names carry the lab id, rows carry the
tenant_id (so the app serves them only to that lab), and the colony STL is the
same layout as the colony shown in the viewer.

Usage:
    python generate_models.py              # every lab on this installation
    python generate_models.py --tenant 3   # one lab only

Run it on the machine that hosts the app, with the app stopped or idle.
"""
import argparse
import hashlib
import os
import sqlite3
from datetime import date
from pathlib import Path

import model_builder
from shape_config import resolve_shape_config

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("APP_DATA_DIR") or BASE)
DB_PATH = DATA_DIR / "microbe_catalog.db"
MODEL_DIR = DATA_DIR / "models"


def _stable_seed(text):
    """Same seed as the app (app._stable_seed), so layouts match."""
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant", type=int, help="only this lab (tenant id)")
    args = ap.parse_args()
    MODEL_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    where, params = ("WHERE tenant_id=?", (args.tenant,)) if args.tenant else ("WHERE tenant_id IS NOT NULL", ())
    orgs = conn.execute(f"SELECT * FROM organisms {where} ORDER BY tenant_id, collection_id", params).fetchall()
    today = date.today().isoformat()
    made = 0
    for org in orgs:
        cid = org["collection_id"]
        prefix = f"t{org['tenant_id']}-{cid}"
        cfg = resolve_shape_config(org)
        seed = _stable_seed(cid)
        old = [r["file_path"] for r in conn.execute(
            "SELECT file_path FROM models WHERE organism_id=? AND source='procedural'", (org["organism_id"],))]
        conn.execute("DELETE FROM models WHERE organism_id=? AND source='procedural'", (org["organism_id"],))
        for rel in old:
            for p in (DATA_DIR / rel, (DATA_DIR / rel).with_suffix(".glb")):
                if p.is_file() and MODEL_DIR.resolve() in p.resolve().parents:
                    p.unlink()
        for mtype, shape in [("colony", cfg["colony"]), ("cell", cfg["cell"])]:
            base = f"{prefix}-{mtype}-STL-v2"
            stl, glb = MODEL_DIR / f"{base}.stl", MODEL_DIR / f"{base}.glb"
            if mtype == "colony":
                watertight, note = model_builder.build_colony_files(shape, seed, stl, glb)
            else:
                watertight, note = model_builder.build_cell_files(shape, stl, glb)
            conn.execute("""INSERT INTO models
                (organism_id, tenant_id, model_type, source, file_format, file_path, scale_factor,
                 level_of_detail, is_print_ready, is_watertight, qa_status, version, created_date, notes)
                VALUES (?, ?, ?, 'procedural', 'STL', ?, 1.0, 'medium', ?, ?, 'review', 2, ?, ?)""",
                (org["organism_id"], org["tenant_id"], mtype, stl.relative_to(DATA_DIR).as_posix(),
                 int(watertight), int(watertight), today, f"{note} GLB companion: {glb.name}"))
            made += 1
            print(f"  t{org['tenant_id']} {cid:16s} {mtype:7s} watertight={watertight}")
        conn.commit()
    conn.close()
    print(f"Generated {made} models for {len(orgs)} strains.")


if __name__ == "__main__":
    main()
