from __future__ import annotations

import httpx

from ..config import Settings
from ..models import SourceNote
from .base import FetchResult, SourceAdapter


class ZoteroSource(SourceAdapter):
    name = "zotero"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.s = settings
        self.base = settings.zotero.base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)

    def health(self) -> tuple[bool, str]:
        try:
            r = self.client.get(f"{self.base}/items", params={"limit": 1})
            return (r.status_code == 200, "ok" if r.status_code == 200 else f"HTTP {r.status_code}")
        except httpx.HTTPError as e:
            return False, "Zotero not reachable (is it running with the local API enabled?)"

    def _get_json(self, path: str, params: dict | None = None):
        r = self.client.get(f"{self.base}{path}", params=params)
        r.raise_for_status()
        return r.json()

    def _annotations(self, limit: int | None = None, since: str | None = None):
        start = 0
        page = 100
        while True:
            params = {"itemType": "annotation", "limit": page, "start": start,
                      "sort": "dateModified", "direction": "desc"}
            items = self._get_json("/items", params)
            if not items:
                return
            for it in items:
                if since and (it["data"].get("dateModified") or "") <= since:
                    return  # sorted desc: everything after is older
                yield it
            start += page

    def inspect(self, limit: int = 10) -> list[dict]:
        out = []
        for it in self._annotations():
            out.append(it)
            if len(out) >= limit:
                break
        return out

    def _parent_item(self, attachment_key: str, cache: dict) -> dict:
        if attachment_key in cache:
            return cache[attachment_key]
        info: dict = {}
        try:
            att = self._get_json(f"/items/{attachment_key}")
            parent_key = att["data"].get("parentItem")
            if parent_key:
                p = self._get_json(f"/items/{parent_key}")["data"]
                creators = p.get("creators") or []
                names = [c.get("name") or f'{c.get("firstName", "")} {c.get("lastName", "")}'.strip()
                         for c in creators]
                info = {"title": p.get("title"), "author": ", ".join(n for n in names if n),
                        "language": p.get("language")}
            else:
                info = {"title": att["data"].get("title")}
        except httpx.HTTPError:
            pass
        cache[attachment_key] = info
        return info

    def fetch(self, updated_after: str | None) -> FetchResult:
        color = self.s.zotero.pink_color.lower()
        if not color:
            raise RuntimeError("zotero.pink_color is empty in config.toml; run `inspect zotero` first")
        cache: dict = {}
        notes: list[SourceNote] = []
        newest = updated_after
        for it in self._annotations(since=updated_after):
            d = it["data"]
            modified = d.get("dateModified")
            if modified and (newest is None or modified > newest):
                newest = modified
            if (d.get("annotationColor") or "").lower() != color:
                continue
            text = (d.get("annotationText") or "").strip()
            if not text:
                continue
            parent = self._parent_item(d.get("parentItem", ""), cache)
            notes.append(
                SourceNote(
                    source=self.name,
                    source_id=d["key"],
                    text=text,
                    instruction=(d.get("annotationComment") or "").strip() or None,
                    language_hint=None,  # evidence only; passed via raw["parent"]
                    book_title=parent.get("title"),
                    author=parent.get("author") or None,
                    source_updated_at=modified,
                    raw={"annotation": d, "parent": parent},
                )
            )
        return FetchResult(notes=notes, cursor=newest)
