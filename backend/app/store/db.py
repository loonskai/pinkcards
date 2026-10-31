from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..dedup import normalize
from ..models import NoteStatus, SourceNote

MIGRATIONS = Path(__file__).parent / "migrations"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for f in sorted(MIGRATIONS.glob("*.sql")):
        n = int(f.name.split("_")[0])
        if n > version:
            conn.executescript(f.read_text("utf-8"))
            conn.execute(f"PRAGMA user_version={n}")
            conn.commit()


_JSON_COLS = {"grammar", "tags", "raw"}


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in _JSON_COLS & d.keys():
        if d[k]:
            d[k] = json.loads(d[k])
    return d


class Store:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---- events ----
    def log(self, entity: str, entity_id: Any, event: str, old: Any = None, new: Any = None, detail: str | None = None):
        self.conn.execute(
            "INSERT INTO events(at,entity,entity_id,event,old,new,detail) VALUES (?,?,?,?,?,?,?)",
            (now(), entity, str(entity_id), event, None if old is None else str(old),
             None if new is None else str(new), detail),
        )

    # ---- sync state ----
    def get_sync_state(self, source: str) -> dict:
        r = self.conn.execute("SELECT * FROM sync_state WHERE source=?", (source,)).fetchone()
        return dict(r) if r else {"source": source, "updated_after": None, "last_run_at": None}

    def set_sync_state(self, source: str, updated_after: str | None):
        cur = self.get_sync_state(source)["updated_after"]
        self.conn.execute(
            "INSERT INTO sync_state(source,updated_after,last_run_at) VALUES (?,?,?) "
            "ON CONFLICT(source) DO UPDATE SET updated_after=excluded.updated_after, last_run_at=excluded.last_run_at",
            (source, updated_after or cur, now()),
        )
        self.conn.commit()

    # ---- source notes ----
    def upsert_source_notes(self, notes: list[SourceNote]) -> int:
        """Insert new notes; for known ones refresh metadata only (status is never reset). Returns #new."""
        new = 0
        for n in notes:
            exists = self.conn.execute("SELECT 1 FROM source_notes WHERE id=?", (n.id,)).fetchone()
            if exists:
                self.conn.execute(
                    "UPDATE source_notes SET instruction=?, book_title=?, author=?, raw=?, source_updated_at=? WHERE id=?",
                    (n.instruction, n.book_title, n.author, json.dumps(n.raw), n.source_updated_at, n.id),
                )
            else:
                self.conn.execute(
                    "INSERT INTO source_notes(id,source,source_id,text,instruction,language_hint,book_title,author,"
                    "raw,source_updated_at,fetched_at,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (n.id, n.source, n.source_id, n.text, n.instruction, n.language_hint, n.book_title,
                     n.author, json.dumps(n.raw), n.source_updated_at, now(), NoteStatus.PENDING),
                )
                self.log("source_note", n.id, "created", new=NoteStatus.PENDING)
                new += 1
        self.conn.commit()
        return new

    def get_source_note(self, id: str) -> dict | None:
        return _row(self.conn.execute("SELECT * FROM source_notes WHERE id=?", (id,)).fetchone())

    def pending_notes(self, limit: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM source_notes WHERE status='pending' ORDER BY fetched_at, id LIMIT ?", (limit,)
        ).fetchall()
        return [_row(r) for r in rows]

    def set_note_status(self, id: str, status: str, reason: str | None = None):
        old = self.conn.execute("SELECT status FROM source_notes WHERE id=?", (id,)).fetchone()
        self.conn.execute("UPDATE source_notes SET status=?, status_reason=? WHERE id=?", (status, reason, id))
        self.log("source_note", id, "status", old[0] if old else None, status, reason)
        self.conn.commit()

    def list_notes(self, status: str) -> list[dict]:
        return [_row(r) for r in self.conn.execute(
            "SELECT * FROM source_notes WHERE status=? ORDER BY fetched_at", (status,))]

    def requeue_unsupported(self, supported: set[str]) -> int:
        """Unsupported notes become pending again once their language is configured."""
        n = 0
        for r in self.list_notes(NoteStatus.UNSUPPORTED):
            lang = (r["status_reason"] or "").removeprefix("language:")
            if lang in supported:
                self.set_note_status(r["id"], NoteStatus.PENDING, "language now supported")
                n += 1
        return n

    # ---- cards ----
    _CARD_COLS = ["source_note_id", "parent_card_id", "kind", "language", "language_confidence", "language_reason",
                  "text", "expression", "form_used", "is_base_form", "grammar", "translation", "hint", "note",
                  "tags", "deck", "meaning_gloss_ru", "already_known", "status", "anki_note_id", "error",
                  "model", "prompt_version", "regen_instruction"]

    def insert_card(self, **f) -> int:
        for k in _JSON_COLS & f.keys():
            if f[k] is not None and not isinstance(f[k], str):
                f[k] = json.dumps(f[k], ensure_ascii=False)
        cols = [c for c in self._CARD_COLS if c in f]
        ts = now()
        cur = self.conn.execute(
            f"INSERT INTO cards({','.join(cols)},created_at,updated_at) VALUES ({','.join('?' * len(cols))},?,?)",
            [f[c] for c in cols] + [ts, ts],
        )
        self.log("card", cur.lastrowid, "created", new=f.get("status", "generated"))
        self.conn.commit()
        return cur.lastrowid

    def update_card(self, id: int, **f) -> None:
        for k in _JSON_COLS & f.keys():
            if f[k] is not None and not isinstance(f[k], str):
                f[k] = json.dumps(f[k], ensure_ascii=False)
        bad = set(f) - set(self._CARD_COLS)
        if bad:
            raise ValueError(f"unknown card fields: {bad}")
        old = self.conn.execute("SELECT status FROM cards WHERE id=?", (id,)).fetchone()
        sets = ",".join(f"{k}=?" for k in f)
        self.conn.execute(f"UPDATE cards SET {sets}, updated_at=? WHERE id=?", [*f.values(), now(), id])
        if "status" in f and old and old[0] != f["status"]:
            self.log("card", id, "status", old[0], f["status"], f.get("error"))
        elif f.keys() - {"status", "error", "anki_note_id"}:
            self.log("card", id, "edited", detail=",".join(sorted(f)))
        self.conn.commit()

    def get_card(self, id: int) -> dict | None:
        return _row(self.conn.execute("SELECT * FROM cards WHERE id=?", (id,)).fetchone())

    def list_cards(self, status: str | None = None, language: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM cards WHERE 1=1", []
        if status:
            q += " AND status=?"; args.append(status)
        if language:
            q += " AND language=?"; args.append(language)
        return [_row(r) for r in self.conn.execute(q + " ORDER BY language, id", args)]

    def forms_used(self, language: str, expression: str) -> list[str]:
        """Forms used per lemma, from approved/sent/exported cards."""
        rows = self.conn.execute(
            "SELECT expression, form_used FROM cards WHERE language=? AND status IN ('approved','sent','exported')",
            (language,)).fetchall()
        key = normalize(expression)
        return sorted({r["form_used"] for r in rows if normalize(r["expression"]) == key and r["form_used"]})

    def meanings_used(self, language: str, expression: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT expression, meaning_gloss_ru FROM cards WHERE language=? AND status!='rejected'",
            (language,)).fetchall()
        key = normalize(expression)
        return sorted({r["meaning_gloss_ru"] for r in rows if normalize(r["expression"]) == key and r["meaning_gloss_ru"]})

    def is_known(self, language: str, expression: str, exclude_card: int | None = None) -> bool:
        rows = self.conn.execute(
            "SELECT id, expression FROM cards WHERE language=? AND status IN ('approved','sent','exported')",
            (language,)).fetchall()
        key = normalize(expression)
        return any(normalize(r["expression"]) == key and r["id"] != exclude_card for r in rows)
