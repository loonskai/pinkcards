from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_settings
from .store.backup import make_backup, restore_backup


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="anki-cloze")
    sub = p.add_subparsers(dest="cmd", required=True)
    ins = sub.add_parser("inspect", help="print raw JSON from a source, unfiltered")
    ins.add_argument("source", choices=["readwise", "zotero", "sheets"])
    ins.add_argument("--limit", type=int, default=10)
    ins.add_argument("--save", help="write output to this file (sanitize before committing!)")
    sub.add_parser("backup", help="back up the DB now")
    rs = sub.add_parser("restore", help="restore the DB from a backup file")
    rs.add_argument("file")
    args = p.parse_args(argv)
    s = load_settings()

    if args.cmd == "inspect":
        from .sources.gsheets import GsheetsSource
        from .sources.readwise import ReadwiseSource
        from .sources.zotero import ZoteroSource

        src = {"readwise": ReadwiseSource, "zotero": ZoteroSource, "sheets": GsheetsSource}[args.source](s)
        out = json.dumps(src.inspect(args.limit), indent=2, ensure_ascii=False)
        if args.save:
            Path(args.save).write_text(out, "utf-8")
        print(out)
    elif args.cmd == "backup":
        print(make_backup(s.db_path, s.backup_dir, s.store.backup_keep))
    elif args.cmd == "restore":
        pre = restore_backup(s.db_path, s.backup_dir, Path(args.file), s.store.backup_keep)
        print("restored; previous DB saved at", pre)
    return 0


if __name__ == "__main__":
    sys.exit(main())
