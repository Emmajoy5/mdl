"""Snapshot the whole catalog (database + photos + 3D models) into one archive.

    python backup.py                 # writes backups/mdl-backup-YYYYmmdd-HHMMSS.tar.gz
    python backup.py --keep 14       # also delete all but the 14 newest archives
    python backup.py --dest /mnt/nas/mdl-backups

Safe to run while the app is up: the database is copied with SQLite's own
online-backup API (a consistent snapshot even mid-write), not by copying the
file out from under a running process. Every lab on the install is included,
since they all share one database; archives therefore hold every lab's data,
so store them like the database itself.

Run it nightly with cron, e.g.:

    0 2 * * *  cd /path/to/microbe3d_app && python backup.py --keep 14

To restore: stop the app, unpack the archive into the data folder (the app
folder, or APP_DATA_DIR if you set one), and start the app again.
"""
import argparse
import os
import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("APP_DATA_DIR") or BASE)
DB_PATH = DATA_DIR / "microbe_catalog.db"


def snapshot_database(dest_file):
    """Copy the live database to dest_file using SQLite's online backup API."""
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest_file)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def make_backup(dest_dir):
    if not DB_PATH.exists():
        raise SystemExit(f"No database found at {DB_PATH}. Nothing to back up.")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive = dest_dir / f"mdl-backup-{stamp}.tar.gz"
    n = 1
    while archive.exists():  # two runs in the same second must not overwrite each other
        n += 1
        archive = dest_dir / f"mdl-backup-{stamp}-{n}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / "microbe_catalog.db"
        snapshot_database(db_copy)
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(db_copy, arcname="microbe_catalog.db")
            for folder in ("uploads", "models"):
                path = DATA_DIR / folder
                if path.exists():
                    tar.add(path, arcname=folder)
            key = DATA_DIR / ".secret_key"
            if key.exists():
                tar.add(key, arcname=".secret_key")
    return archive


def prune(dest_dir, keep):
    # by modification time, not name: "...-2.tar.gz" sorts *before* the plain
    # "....tar.gz" alphabetically even though it was written after it
    archives = sorted(Path(dest_dir).glob("mdl-backup-*.tar.gz"), key=lambda p: p.stat().st_mtime_ns)
    removed = archives[:-keep] if keep > 0 else []
    for old in removed:
        old.unlink()
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dest", default=str(DATA_DIR / "backups"),
                        help="folder to write archives into (default: <data folder>/backups)")
    parser.add_argument("--keep", type=int, default=0,
                        help="keep only this many newest archives (default: keep all)")
    args = parser.parse_args()

    archive = make_backup(args.dest)
    size_mb = archive.stat().st_size / (1024 * 1024)
    print(f"Wrote {archive} ({size_mb:.1f} MB)")
    if args.keep:
        for old in prune(args.dest, args.keep):
            print(f"Removed old backup {old.name}")


if __name__ == "__main__":
    main()
