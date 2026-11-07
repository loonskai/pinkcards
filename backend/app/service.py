"""Orchestration: sync flow, card actions, push/export. Used by api.py (and tests)."""
from __future__ import annotations

import base64
from pathlib import Path

from .config import Settings
from .dedup import slugify, normalize
from .generator.base import GenerationError, GenerationRequest, Generator
from .generator.note import build_note
from .generator.validate import validate_card
from .models import SOURCE_KIND, CardKind, CardStatus, GeneratedCard, NoteStatus
from .sinks.ankiconnect import AnkiConnect, AnkiConnectError
from .sources.base import SourceAdapter
from .store.backup import make_backup
from .store.db import Store, now
from .tts.base import TTS, audio_filename, text_hash, tts_text


DELIVERED = (CardStatus.SENT, CardStatus.EXPORTED)  # cards that are (or should now be) notes in Anki


class Service:
    def __init__(self, settings: Settings, store: Store, sources: dict[str, SourceAdapter],
                 generator: Generator, anki: AnkiConnect | None = None, tts: TTS | None = None):
        self.s, self.store, self.sources, self.gen, self.anki = settings, store, sources, generator, anki
        self.tts = tts

    # ---- audio ----
    def audio_path(self, name: str) -> Path:
        return self.s.audio_dir / Path(name).name

    def audio_valid(self, c: dict) -> bool:
        """Stored file exists and was made from the card's current text."""
        f = c.get("audio_file")
        return bool(f and c.get("text") and c.get("audio_text_hash") == text_hash(c["text"])
                    and self.audio_path(f).is_file())

    def _drop_audio_file(self, name: str | None, keep: str | None = None):
        if name and name != keep:
            self.audio_path(name).unlink(missing_ok=True)

    def ensure_audio(self, card: dict, force: bool = False) -> dict:
        """Make sure the card has up-to-date audio. Never raises; failures go to `audio_error`.

        Returns the refreshed card. `force` ignores the hash cache (used by regenerate_audio)."""
        cid = card["id"]
        c = self.store.get_card(cid)
        if not self.s.tts_enabled(c.get("language")):
            clear: dict = {}
            if c.get("audio_file") and c["status"] not in DELIVERED:  # e.g. language changed to one without audio
                self._drop_audio_file(c["audio_file"])
                clear = dict.fromkeys(("audio_file", "audio_text_hash", "audio_model", "audio_voice", "audio_at"))
            if c.get("audio_error") or clear:
                self.store.update_card(cid, audio_error=None, **clear)
            return self.store.get_card(cid)
        if not c.get("text"):
            return c
        h = text_hash(c["text"])
        if not force and self.audio_valid(c):
            if c.get("audio_error"):
                self.store.update_card(cid, audio_error=None)
            return self.store.get_card(cid)
        voice = self.s.tts.languages[c["language"]].voice
        name = audio_filename(cid, h)
        try:
            if self.tts is None:
                raise RuntimeError("TTS client not configured")
            mp3 = self.tts.synthesize(tts_text(c["text"]), c["language"])
            self.s.audio_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.audio_path(name + ".tmp")
            tmp.write_bytes(mp3)
            tmp.replace(self.audio_path(name))
        except Exception as e:  # noqa: BLE001 - TTS must never break approve/push
            msg = str(e) or type(e).__name__
            self.store.log("card", cid, "audio_failed", detail=msg)
            self.store.update_card(cid, audio_error=msg)
            return self.store.get_card(cid)
        self._drop_audio_file(c.get("audio_file"), keep=name)
        self.store.log("card", cid, "audio_generated", old=c.get("audio_file"), new=name,
                       detail=f"{self.s.tts.model} / {voice}")
        self.store.update_card(cid, audio_file=name, audio_text_hash=h, audio_model=self.s.tts.model,
                               audio_voice=voice, audio_error=None, audio_at=now())
        return self.store.get_card(cid)

    def regenerate_audio(self, card_id: int) -> dict:
        """Fresh audio for approved / failed (with content) / sent cards. Sent cards are updated in Anki too.

        Returns {"card": ..., "anki": {"updated": bool, "error"?: str, "reason"?: str} | None}."""
        c = self.store.get_card(card_id)
        ok_status = c["status"] in (CardStatus.APPROVED, CardStatus.SENT, CardStatus.EXPORTED) or \
            (c["status"] == CardStatus.FAILED and c.get("text") and not validate_card(c))
        if not ok_status:
            raise ValueError("audio can only be regenerated for approved, failed, exported or sent cards")
        if not self.s.tts_enabled(c.get("language")):
            raise ValueError(f"audio is not enabled for language {c.get('language')!r}")
        old = c.get("audio_file")
        new = self.ensure_audio(c, force=True)
        if new.get("audio_error"):
            return {"card": new, "anki": None}
        anki = None
        if c["status"] in DELIVERED:
            anki = self._update_anki_audio(new, old)
        return {"card": new, "anki": anki}

    def _resolve_note_id(self, c: dict) -> tuple[int | None, dict | None]:
        """Anki note id of a delivered card. Cards imported via .apkg have none: find the note by deck +
        Expression + exact Text and remember it. Only an unambiguous single match is accepted."""
        if c.get("anki_note_id"):
            return c["anki_note_id"], None
        if not self.anki:
            return None, {"updated": False, "error": "AnkiConnect not configured"}
        if not (c.get("deck") and c.get("expression") and c.get("text")):
            return None, {"updated": False, "reason": "card has no deck/expression/text to look the note up by"}
        try:
            ids = self.anki.find_note_ids(c["deck"], c["expression"], c["text"])
        except AnkiConnectError as e:
            return None, {"updated": False, "error": str(e)}
        if len(ids) != 1:
            why = "no matching note found in Anki (not imported yet?)" if not ids else \
                f"{len(ids)} Anki notes match this card; not updating (ambiguous)"
            self.store.log("card", c["id"], "anki_lookup_failed", detail=why)
            return None, {"updated": False, "reason": why}
        self.store.update_card(c["id"], anki_note_id=ids[0])
        self.store.log("card", c["id"], "anki_note_found", new=ids[0])
        c["anki_note_id"] = ids[0]
        return ids[0], None

    @staticmethod
    def _anki_err(e: Exception) -> str:
        msg = str(e)
        if "Close the affected editor" in msg:  # AnkiConnect refuses writes to a note open in the editor/browser
            return ("this note is open in Anki's editor or Browser. Close it (or select another note), "
                    "then click \"Resend to Anki\"")
        return msg

    def resend_to_anki(self, card_id: int) -> dict:
        """Re-send NOTE (+ Audio, if the card has valid audio) to the card's Anki note. No LLM / TTS call.

        Use after a failed update (e.g. the note was open in Anki's editor)."""
        c = self.store.get_card(card_id)
        if c["status"] not in DELIVERED:
            raise ValueError("only sent or exported cards are in Anki")
        nid, fail = self._resolve_note_id(c)
        if fail:
            return {"card": c, "anki": fail}
        fields: dict = {"NOTE": c.get("note") or ""}
        audio = self.s.tts_enabled(c.get("language")) and self.audio_valid(c)
        try:
            if audio:
                data = base64.b64encode(self.audio_path(c["audio_file"]).read_bytes()).decode()
                self.anki.invoke("storeMediaFile", filename=c["audio_file"], data=data)
                fields["Audio"] = f"[sound:{c['audio_file']}]"
            self.anki.invoke("updateNoteFields", note={"id": nid, "fields": fields})
        except (AnkiConnectError, OSError) as e:
            self.store.log("card", card_id, "resend_anki_failed", detail=str(e))
            return {"card": c, "anki": {"updated": False, "error": self._anki_err(e)}}
        self.store.log("card", card_id, "resend_anki_ok", detail=",".join(fields))
        return {"card": self.store.get_card(card_id), "anki": {"updated": True, "audio": bool(audio)}}

    def _update_anki_audio(self, c: dict, old_name: str | None) -> dict:
        nid, fail = self._resolve_note_id(c)
        if fail:
            return fail
        name = c["audio_file"]
        try:
            data = base64.b64encode(self.audio_path(name).read_bytes()).decode()
            self.anki.invoke("storeMediaFile", filename=name, data=data)
            self.anki.invoke("updateNoteFields", note={"id": nid, "fields": {"Audio": f"[sound:{name}]"}})
        except (AnkiConnectError, OSError) as e:
            self.store.log("card", c["id"], "audio_anki_failed", detail=str(e))
            return {"updated": False, "error": self._anki_err(e)}
        self.store.log("card", c["id"], "audio_anki_updated", old=old_name, new=name)
        if old_name and old_name != name:
            try:  # best-effort: leftover media is harmless
                self.anki.invoke("deleteMediaFile", filename=old_name)
            except AnkiConnectError:
                pass
        return {"updated": True}

    def _audio_ready(self, cards: list[dict]) -> tuple[list[dict], list[tuple[dict, str]]]:
        """Split cards into pushable ones (with `audio_file` only if valid) and (card, reason) refusals."""
        ok, bad = [], []
        for c in cards:
            if self.s.tts_enabled(c.get("language")) and not self.audio_valid(c):
                c = self.ensure_audio(c)
                if not self.audio_valid(c):
                    bad.append((c, f"audio missing: {c.get('audio_error') or 'not generated'}"))
                    continue
            c = dict(c)
            if not self.s.tts_enabled(c.get("language")) or not self.audio_valid(c):
                c["audio_file"] = None
            ok.append(c)
        return ok, bad

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
            instruction=note.get("instruction"), details_languages=self.s.details_languages, **kw)

    def _card_fields(self, note: dict, g: GeneratedCard) -> dict:
        deck = self.s.anki.decks.get(g.language)
        gd = g.grammar.model_dump()
        # the model may ignore "only for enabled languages": gate in code too
        details = g.details.model_dump() if g.details and not g.details.is_empty() \
            and self.s.details_enabled(g.language) else None
        return dict(
            language=g.language, language_confidence=g.language_confidence, language_reason=g.language_reason,
            text=g.text, expression=g.expression, form_used=g.form_used, is_base_form=int(g.is_base_form),
            grammar=gd, translation=g.translation, hint=g.hint,
            note=build_note(g.expression, g.is_base_form, gd, note["source"], note.get("book_title"),
                            note.get("author"), g.language, details),
            details=details, details_stale=0,
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
        done, refused, audio_errors = [], {}, {}
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
            if c.get("details_stale") and self.s.details_enabled(c.get("language")):
                try:  # stale notes must never reach Anki
                    r = self.regenerate_details(i)
                    err = r.get("error")
                except ValueError as e:
                    err = str(e)
                if err:
                    refused[i] = f"notes are stale and could not be regenerated: {err}"
                    continue
            self.store.update_card(i, status=CardStatus.APPROVED, error=None)
            done.append(i)
            try:  # approval never fails because of TTS; the error is shown on the card
                r = self.ensure_audio(c)
                if r.get("audio_error"):
                    audio_errors[i] = r["audio_error"]
            except Exception as e:  # noqa: BLE001
                audio_errors[i] = str(e)
        return {"approved": done, "refused": refused, "audio_errors": audio_errors}

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
        if "text" in fields and fields["text"] != c.get("text"):
            fields["details_stale"] = 1  # sentence changed: notes no longer match; user regenerates them
        if "note" not in fields and fields.keys() & {"expression", "is_base_form", "grammar"}:
            fields["note"] = self._note_for(merged)
        if "language" in fields:
            fields["deck"] = self.s.anki.decks.get(fields["language"])
        if c["status"] in (CardStatus.APPROVED, CardStatus.FAILED):
            fields["status"] = CardStatus.GENERATED  # edits require re-approval
        self.store.update_card(card_id, **fields)
        card = self.store.get_card(card_id)
        card["validation_errors"] = validate_card(card)
        return card

    def _note_for(self, c: dict, details: dict | None | bool = False) -> str:
        """NOTE HTML for a card dict. `details=False` = use the card's stored details (kept through edits)."""
        n = self.store.get_source_note(c["source_note_id"])
        if details is False:
            details = c.get("details")
        if not self.s.details_enabled(c.get("language")):
            details = None
        return build_note(c["expression"], bool(c["is_base_form"]), c.get("grammar"), n["source"],
                          n.get("book_title"), n.get("author"), c["language"], details)

    def regenerate_details(self, card_id: int) -> dict:
        """New grammar/word notes for the *unchanged* sentence; rebuilds NOTE, keeps approval.

        Returns {"card", "anki": {...}|None, "error"?: str}. A sent card is also updated in Anki (NOTE field)."""
        c = self.store.get_card(card_id)
        ok_status = c["status"] in (CardStatus.GENERATED, CardStatus.APPROVED, CardStatus.SENT,
                                    CardStatus.EXPORTED) or \
            (c["status"] == CardStatus.FAILED and c.get("text") and not validate_card(c))
        if not ok_status:
            raise ValueError("notes can only be regenerated for generated, approved, failed, exported or sent cards")
        if not self.s.details_enabled(c.get("language")):
            raise ValueError(f"details are not enabled for language {c.get('language')!r}")
        try:
            d = self.gen.generate_details(c)
        except GenerationError as e:
            self.store.log("card", card_id, "details_failed", detail=str(e))
            return {"card": self.store.get_card(card_id), "anki": None, "error": str(e)}
        details = d.model_dump() if d else None
        note = self._note_for(c, details)
        self.store.update_card(card_id, details=details, details_stale=0, note=note)
        self.store.log("card", card_id, "details_regenerated", detail="empty" if not details else "ok")
        card = self.store.get_card(card_id)
        anki = self._update_anki_note(card) if c["status"] in DELIVERED else None
        return {"card": card, "anki": anki}

    def _update_anki_note(self, c: dict) -> dict:
        """Push the rebuilt NOTE to an already-sent note (overwrites NOTE as edited in Anki)."""
        nid, fail = self._resolve_note_id(c)
        if fail:
            return fail
        try:
            self.anki.invoke("updateNoteFields", note={"id": nid, "fields": {"NOTE": c["note"] or ""}})
        except AnkiConnectError as e:
            self.store.log("card", c["id"], "details_anki_failed", detail=str(e))
            return {"updated": False, "error": self._anki_err(e)}
        self.store.log("card", c["id"], "details_anki_updated")
        return {"updated": True}

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
        cards, no_audio = self._audio_ready(cards)
        for c, why in no_audio:  # not pushed: the user can fix it with "Regenerate audio"
            self.store.update_card(c["id"], status=CardStatus.FAILED, error=why)
        results = self.anki.push(cards)
        sent, failed = 0, len(no_audio)
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
        cards, no_audio = self._audio_ready(self.store.list_cards(CardStatus.APPROVED))
        for c, why in no_audio:
            self.store.update_card(c["id"], status=CardStatus.FAILED, error=why)
        if not cards:
            return {"exported": 0, "failed": len(no_audio)}
        self.backup("export")
        sink.push(cards)
        for c in cards:
            self.store.update_card(c["id"], status=CardStatus.EXPORTED)
        return {"exported": len(cards), "failed": len(no_audio), "path": str(sink.last_path)}

    def confirm_exported(self) -> int:
        cards = self.store.list_cards(CardStatus.EXPORTED)
        for c in cards:
            self.store.update_card(c["id"], status=CardStatus.SENT)
            self.store.set_note_status(c["source_note_id"], NoteStatus.DONE)
        return len(cards)
