"""Orchestration: sync flow, card actions, push/export. Used by api.py (and tests)."""
from __future__ import annotations

from .config import Settings
from .dedup import slugify, normalize
from .generator.base import GenerationError, GenerationRequest, Generator
from .generator.note import build_note
from .generator.validate import validate_card
from .models import SOURCE_KIND, CardKind, CardStatus, GeneratedCard, NoteStatus
from .sinks.ankiconnect import AnkiConnect, AnkiConnectError
from .sources.base import SourceAdapter
from .store.backup import make_backup
from .store.db import Store


class Service:
    def __init__(self, settings: Settings, store: Store, sources: dict[str, SourceAdapter],
                 generator: Generator, anki: AnkiConnect | None = None):
        self.s, self.store, self.sources, self.gen, self.anki = settings, store, sources, generator, anki

    # ---- helpers ----
    def backup(self, reason: str):
        if self.s.db_path.exists() and str(self.s.db_path) != ":memory:":
            make_backup(self.s.db_path, self.s.backup_dir, self.s.store.backup_keep, reason)

    def build_tags(self, note: dict) -> list[str]:
        kind = SOURCE_KIND.get(note["source"], note["source"])
        tags = ["auto", f"src::{kind}"]
        if kind != "sheets" and note.get("book_title"):
            tags.append(f"book::{slugify(note['book_title'])}")
        return tags

    def _request(self, note: dict, **kw) -> GenerationRequest:
        parent = (note.get("raw") or {}).get("parent") or {}
        return GenerationRequest(
            text=note["text"], source=note["source"], book_title=note.get("book_title"), author=note.get("author"),
            zotero_language=parent.get("language"), language_hint=note.get("language_hint"),
            instruction=note.get("instruction"), **kw)

    def _card_fields(self, note: dict, g: GeneratedCard) -> dict:
        deck = self.s.anki.decks.get(g.language)
        gd = g.grammar.model_dump()
        return dict(
            language=g.language, language_confidence=g.language_confidence, language_reason=g.language_reason,
            text=g.text, expression=g.expression, form_used=g.form_used, is_base_form=int(g.is_base_form),
            grammar=gd, translation=g.translation, hint=g.hint,
            note=build_note(g.expression, g.is_base_form, gd, note["source"], note.get("book_title"),
                            note.get("author"), g.language),
            tags=self.build_tags(note), deck=deck, meaning_gloss_ru=g.meaning_gloss_ru,
            model=self.gen.model, prompt_version=self.gen.prompt_version)

    def _known(self, language: str, expression: str, deck: str | None, exclude_card: int | None = None) -> bool:
        if self.store.is_known(language, expression, exclude_card):
            return True
        if self.anki and deck:
            return self.anki.known_in_anki(deck, expression)
        return False

    # ---- sync ----
    def fetch_all(self) -> dict:
        report: dict = {}
        for name, src in self.sources.items():
            ok, why = src.health()
            if not ok:
                report[name] = {"skipped": why}
                continue
            try:
                st = self.store.get_sync_state(name)
                res = src.fetch(st["updated_after"])
                new = self.store.upsert_source_notes(res.notes)
                self.store.set_sync_state(name, res.cursor)
                report[name] = {"fetched": len(res.notes), "new": new}
            except Exception as e:  # a failing source must not block the others
                report[name] = {"error": str(e)}
        return report

    def sync(self) -> dict:
        self.backup("sync")
        report = {"sources": self.fetch_all()}
        self.store.requeue_unsupported(self.s.supported_languages)
        notes = self.store.pending_notes(self.s.sync.max_cards_per_sync)
        made = failed = unsupported = 0
        for n in notes:
            try:
                g = self.gen.generate(self._request(n))
            except GenerationError as e:
                self.store.insert_card(source_note_id=n["id"], kind=CardKind.INITIAL, status=CardStatus.FAILED,
                                       error=str(e), model=self.gen.model, prompt_version=self.gen.prompt_version)
                self.store.set_note_status(n["id"], NoteStatus.GENERATED, "generation failed")
                failed += 1
                continue
            if g.language not in self.s.supported_languages:
                self.store.set_note_status(n["id"], NoteStatus.UNSUPPORTED, f"language:{g.language}")
                unsupported += 1
                continue
            f = self._card_fields(n, g)
            known = self._known(g.language, g.expression, f["deck"])
            self.store.insert_card(source_note_id=n["id"], kind=CardKind.INITIAL, status=CardStatus.GENERATED,
                                   already_known=int(known), **f)
            self.store.set_note_status(n["id"], NoteStatus.GENERATED)
            made += 1
        report.update(generated=made, failed=failed, unsupported=unsupported,
                      remaining_pending=len(self.store.pending_notes(10**6)))
        return report

    # ---- card actions ----
    def approve(self, ids: list[int]) -> dict:
        done, refused = [], {}
        for i in ids:
            c = self.store.get_card(i)
            if not c or c["status"] not in (CardStatus.GENERATED, CardStatus.FAILED):
                refused[i] = "not approvable"
                continue
            errs = validate_card(c)
            if errs:
                refused[i] = "; ".join(errs)
                continue
            if not c.get("deck"):
                refused[i] = "no deck for language"
                continue
            self.store.update_card(i, status=CardStatus.APPROVED, error=None)
            done.append(i)
        return {"approved": done, "refused": refused}

    def reject(self, card_id: int):
        c = self.store.get_card(card_id)
        self.store.update_card(card_id, status=CardStatus.REJECTED)
        self._sync_note_after_reject(c)

    def _sync_note_after_reject(self, c: dict):
        live = self.store.conn.execute(
            "SELECT 1 FROM cards WHERE source_note_id=? AND status!='rejected'", (c["source_note_id"],)).fetchone()
        if not live:
            self.store.set_note_status(c["source_note_id"], NoteStatus.REJECTED, "all cards rejected")

    def unreject(self, card_id: int):
        c = self.store.get_card(card_id)
        self.store.update_card(card_id, status=CardStatus.GENERATED)
        self.store.set_note_status(c["source_note_id"], NoteStatus.GENERATED, "unrejected")

    def edit(self, card_id: int, **fields) -> dict:
        allowed = {"text", "expression", "form_used", "is_base_form", "grammar", "translation", "hint", "note",
                   "language", "meaning_gloss_ru"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        c = self.store.get_card(card_id)
        if "is_base_form" in fields:
            fields["is_base_form"] = int(bool(fields["is_base_form"]))
        merged = {**c, **fields}
        if "note" not in fields and fields.keys() & {"expression", "is_base_form", "grammar"}:
            n = self.store.get_source_note(c["source_note_id"])
            fields["note"] = build_note(merged["expression"], bool(merged["is_base_form"]), merged.get("grammar"),
                                        n["source"], n.get("book_title"), n.get("author"), merged["language"])
        if "language" in fields:
            fields["deck"] = self.s.anki.decks.get(fields["language"])
        if c["status"] in (CardStatus.APPROVED, CardStatus.FAILED):
            fields["status"] = CardStatus.GENERATED  # edits require re-approval
        self.store.update_card(card_id, **fields)
        card = self.store.get_card(card_id)
        card["validation_errors"] = validate_card(card)
        return card

    def regenerate(self, card_id: int, instruction: str | None = None, language: str | None = None) -> dict:
        c = self.store.get_card(card_id)
        n = self.store.get_source_note(c["source_note_id"])
        extra: dict = {"regen_instruction": instruction, "force_language": language}
        if c["kind"] == CardKind.NEW_FORM:
            extra["lemma"] = c["expression"]
            extra["excluded_forms"] = self.store.forms_used(c["language"], c["expression"])
        elif c["kind"] == CardKind.ANOTHER_MEANING:
            extra["excluded_meanings"] = [m for m in self.store.meanings_used(c["language"], c["expression"])
                                          if m != c.get("meaning_gloss_ru")]
        try:
            g = self.gen.generate(self._request(n, **extra))
        except GenerationError as e:
            self.store.update_card(card_id, status=CardStatus.FAILED, error=str(e))
            return self.store.get_card(card_id)
        if g.language not in self.s.supported_languages:
            self.store.update_card(card_id, language=g.language, deck=None, status=CardStatus.GENERATED,
                                   error=f"unsupported language {g.language}")
            return self.store.get_card(card_id)
        f = self._card_fields(n, g)
        known = self._known(g.language, g.expression, f["deck"], exclude_card=card_id) \
            if c["kind"] == CardKind.INITIAL else False
        self.store.update_card(card_id, status=CardStatus.GENERATED, error=None, already_known=int(known),
                               regen_instruction=instruction, **f)
        return self.store.get_card(card_id)

    def change_language(self, card_id: int, language: str) -> dict:
        return self.regenerate(card_id, instruction=None, language=language)

    def add_child(self, card_id: int, kind: CardKind) -> dict:
        parent = self.store.get_card(card_id)
        n = self.store.get_source_note(parent["source_note_id"])
        lang, expr = parent["language"], parent["expression"]
        extra: dict = {"lemma": expr}
        if kind == CardKind.NEW_FORM:
            extra["excluded_forms"] = sorted(set(self.store.forms_used(lang, expr)) | {parent["form_used"]})
        else:
            extra["excluded_meanings"] = self.store.meanings_used(lang, expr)
        req = self._request(n, force_language=lang, **extra)
        try:
            g = self.gen.generate(req)
        except GenerationError as e:
            cid = self.store.insert_card(source_note_id=n["id"], parent_card_id=card_id, kind=kind,
                                         status=CardStatus.FAILED, language=lang, expression=expr, error=str(e))
            return self.store.get_card(cid)
        f = self._card_fields(n, g)
        cid = self.store.insert_card(source_note_id=n["id"], parent_card_id=card_id, kind=kind,
                                     status=CardStatus.GENERATED, already_known=0, **f)
        return self.store.get_card(cid)

    # ---- push / export ----
    def push(self) -> dict:
        if not self.anki:
            raise AnkiConnectError("AnkiConnect not configured")
        problems = self.anki.check()
        if problems:
            raise AnkiConnectError("; ".join(problems))
        self.backup("push")
        cards = [c for c in self.store.list_cards(CardStatus.APPROVED)] + \
                [c for c in self.store.list_cards(CardStatus.FAILED) if c.get("text") and not validate_card(c)]
        results = self.anki.push(cards)
        sent = failed = 0
        for r in results:
            if r.ok:
                c = self.store.get_card(r.card_id)
                self.store.update_card(r.card_id, status=CardStatus.SENT, anki_note_id=r.anki_note_id, error=None)
                self.store.set_note_status(c["source_note_id"], NoteStatus.DONE)
                sent += 1
            else:
                self.store.update_card(r.card_id, status=CardStatus.FAILED, error=r.error)
                failed += 1
        return {"sent": sent, "failed": failed}

    def export_apkg(self) -> dict:
        from .sinks.apkg import ApkgSink

        if not self.anki:
            raise AnkiConnectError("templates need AnkiConnect once")
        t = self.anki.model_templates()
        sink = ApkgSink(self.s, t["templates"], t["css"])
        cards = self.store.list_cards(CardStatus.APPROVED)
        if not cards:
            return {"exported": 0}
        self.backup("export")
        sink.push(cards)
        for c in cards:
            self.store.update_card(c["id"], status=CardStatus.EXPORTED)
        return {"exported": len(cards), "path": str(sink.last_path)}

    def confirm_exported(self) -> int:
        cards = self.store.list_cards(CardStatus.EXPORTED)
        for c in cards:
            self.store.update_card(c["id"], status=CardStatus.SENT)
            self.store.set_note_status(c["source_note_id"], NoteStatus.DONE)
        return len(cards)
