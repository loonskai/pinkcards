from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from .config import load_settings
from .generator.openrouter import OpenRouterGenerator
from .generator.validate import validate_card
from .models import CardKind
from .service import Service
from .sinks.ankiconnect import AnkiConnect, AnkiConnectError
from .sources.gsheets import GsheetsSource
from .sources.readwise import ReadwiseSource
from .sources.zotero import ZoteroSource
from .status import collect_status
from .store.db import Store, connect
from .tts.openrouter import OpenRouterTTS


def build_service() -> Service:
    s = load_settings()
    store = Store(connect(s.db_path))
    sources = {"readwise": ReadwiseSource(s), "zotero": ZoteroSource(s), "gsheets": GsheetsSource(s)}
    return Service(s, store, sources, OpenRouterGenerator(s), AnkiConnect(s), OpenRouterTTS(s))


svc: Service = None  # type: ignore


@asynccontextmanager
async def lifespan(app: FastAPI):
    global svc
    svc = build_service()
    yield


app = FastAPI(title="anki-cloze", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


def _card(c: dict) -> dict:
    c["validation_errors"] = validate_card(c) if c.get("text") else ["no content"]
    c["details_enabled"] = svc.s.details_enabled(c.get("language"))
    c["details_stale"] = bool(c.get("details_stale"))
    c["audio_enabled"] = svc.s.tts_enabled(c.get("language"))
    c["audio_url"] = f"/api/cards/{c['id']}/audio" \
        if c.get("audio_file") and svc.audio_path(c["audio_file"]).is_file() else None
    c["flagged"] = bool(c.get("already_known")) or c.get("language_confidence") == "low"
    return c


def _need_card(card_id: int) -> dict:
    c = svc.store.get_card(card_id)
    if not c:
        raise HTTPException(404, "card not found")
    return c


# Handlers are sync `def`, so FastAPI runs them in a threadpool; serialise store access via the GIL-safe
# single connection (check_same_thread=False). Fine for a single local user.

@app.get("/api/status")
def status():
    return collect_status(svc.s, svc.sources, svc.gen, svc.anki, svc.tts)


@app.post("/api/sync")
def sync():
    st = collect_status(svc.s, svc.sources, svc.gen, svc.anki, svc.tts)
    if not st["sync"]["enabled"]:
        raise HTTPException(409, st["sync"]["reason"])
    return svc.sync()


@app.get("/api/cards")
def cards(status: str | None = None, language: str | None = None, flagged: bool = False):
    items = [_card(c) for c in svc.store.list_cards(status, language)]
    if flagged:
        items = [c for c in items if c["flagged"]]
    return items


@app.get("/api/notes/unsupported")
def unsupported():
    return svc.store.list_notes("unsupported")


@app.patch("/api/cards/{card_id}")
def edit(card_id: int, fields: dict = Body(...)):
    _need_card(card_id)
    return _card(svc.edit(card_id, **fields))


@app.post("/api/cards/approve")
def approve(ids: list[int] = Body(..., embed=True)):
    return svc.approve(ids)


@app.post("/api/cards/{card_id}/reject")
def reject(card_id: int):
    _need_card(card_id); svc.reject(card_id); return {"ok": True}


@app.post("/api/cards/{card_id}/unreject")
def unreject(card_id: int):
    _need_card(card_id); svc.unreject(card_id); return {"ok": True}


@app.post("/api/cards/{card_id}/regenerate")
def regenerate(card_id: int, instruction: str | None = Body(None, embed=True)):
    _need_card(card_id)
    return _card(svc.regenerate(card_id, instruction))


@app.post("/api/cards/{card_id}/language")
def change_language(card_id: int, language: str = Body(..., embed=True)):
    _need_card(card_id)
    return _card(svc.change_language(card_id, language))


@app.post("/api/cards/{card_id}/another-meaning")
def another_meaning(card_id: int):
    _need_card(card_id)
    return _card(svc.add_child(card_id, CardKind.ANOTHER_MEANING))


@app.post("/api/cards/{card_id}/new-form")
def new_form(card_id: int):
    _need_card(card_id)
    return _card(svc.add_child(card_id, CardKind.NEW_FORM))


@app.get("/api/cards/{card_id}/audio")
def card_audio(card_id: int):
    c = _need_card(card_id)
    if not c.get("audio_file") or not svc.audio_path(c["audio_file"]).is_file():
        raise HTTPException(404, "no audio")
    return FileResponse(svc.audio_path(c["audio_file"]), media_type="audio/mpeg")


@app.post("/api/cards/{card_id}/regenerate-audio")
def regenerate_audio(card_id: int):
    _need_card(card_id)
    try:
        r = svc.regenerate_audio(card_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {**_card(r["card"]), "anki": r["anki"]}


@app.post("/api/cards/{card_id}/regenerate-details")
def regenerate_details(card_id: int):
    _need_card(card_id)
    try:
        r = svc.regenerate_details(card_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {**_card(r["card"]), "anki": r["anki"], "details_error": r.get("error")}


@app.post("/api/cards/{card_id}/resend-to-anki")
def resend_to_anki(card_id: int):
    _need_card(card_id)
    try:
        r = svc.resend_to_anki(card_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {**_card(r["card"]), "anki": r["anki"]}


@app.post("/api/push")
def push():
    try:
        return svc.push()
    except AnkiConnectError as e:
        raise HTTPException(409, str(e))


@app.post("/api/export")
def export():
    try:
        return svc.export_apkg()
    except AnkiConnectError as e:
        raise HTTPException(409, str(e))


@app.post("/api/export/confirm")
def confirm_export():
    return {"confirmed": svc.confirm_exported()}
