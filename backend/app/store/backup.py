from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

PREFIX = "anki-cloze-"


def make_backup(db_path: Path, backup_dir: Path, keep: int = 20, reason: str = "manual") -> Path:
    """Consistent copy via SQLite's online backup API (never a raw file copy)."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = backup_dir / f"{PREFIX}{stamp}-{reason}.db"
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    rotate(backup_dir, keep)
    return dest


def rotate(backup_dir: Path, keep: int) -> None:
    files = sorted(backup_dir.glob(f"{PREFIX}*.db"))
    for old in files[: max(0, len(files) - keep)]:
        old.unlink()


def restore_backup(db_path: Path, backup_dir: Path, backup_file: Path, keep: int = 20) -> Path | None:
    """Backs up the current DB first, then swaps in the chosen file."""
    # validate the file is a SQLite DB
    chk = sqlite3.connect(str(backup_file))
    try:
        chk.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        chk.close()
    pre = None
    if db_path.exists():
        pre = make_backup(db_path, backup_dir, keep, reason="pre-restore")
    for ext in ("-wal", "-shm"):
        Path(str(db_path) + ext).unlink(missing_ok=True)
    shutil.copyfile(backup_file, db_path)
    return pre
