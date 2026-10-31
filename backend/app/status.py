from __future__ import annotations

from .config import Settings


def collect_status(settings: Settings, sources: dict, generator, anki) -> dict:
    checks = {}
    names = {"readwise": "Readwise", "zotero": "Zotero", "gsheets": "Google"}
    for k, src in sources.items():
        ok, msg = src.health()
        checks[k] = {"label": names.get(k, k), "ok": ok, "message": msg, "action": None}
    ok, msg = anki.health() if anki else (False, "not configured")
    checks["anki"] = {"label": "AnkiConnect", "ok": ok, "message": msg, "action": None}
    ok, msg = generator.health()
    checks["openrouter"] = {"label": "OpenRouter", "ok": ok, "message": msg, "action": None}
    sources_up = [k for k in sources if checks[k]["ok"]]
    sync_ok = checks["openrouter"]["ok"] and bool(sources_up)
    sync_reason = None if sync_ok else ("OpenRouter down" if not checks["openrouter"]["ok"] else "no source reachable")
    push_ok = checks["anki"]["ok"]
    return {"checks": checks,
            "sync": {"enabled": sync_ok, "reason": sync_reason},
            "push": {"enabled": push_ok, "reason": None if push_ok else checks["anki"]["message"]}}
