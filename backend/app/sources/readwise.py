from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from ..config import Settings
from ..models import SourceNote
from .base import FetchResult, SourceAdapter

BASE = "https://readwise.io/api/v2"


def _matches(highlight: dict, pink_filter: str) -> bool:
    """pink_filter: alternatives joined with '|', each 'tag:<n>' or 'color:<n>'."""
    if not pink_filter:
        return False
    tags = {(t.get("name") or "").lower() for t in highlight.get("tags") or []}
    color = (highlight.get("color") or "").lower()
    for alt in pink_filter.split("|"):
        kind, _, val = alt.strip().partition(":")
        val = val.lower()
        if kind == "tag" and val in tags:
            return True
        if kind == "color" and val == color:
            return True
    return False


class ReadwiseSource(SourceAdapter):
    name = "readwise"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.s = settings
        self.client = client or httpx.Client(timeout=30)

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Token {self.s.readwise_token}"}

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        for _ in range(5):
            r = self.client.get(url, params=params, headers=self._headers)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", "5")))
                continue
            r.raise_for_status()
            return r
        raise RuntimeError("Readwise: rate-limited too many times")

    def health(self) -> tuple[bool, str]:
        if not self.s.readwise_token:
            return False, "READWISE_TOKEN not set"
        try:
            r = self.client.get(f"{BASE}/auth/", headers=self._headers)
            return (r.status_code == 204, "ok" if r.status_code == 204 else f"HTTP {r.status_code}")
        except httpx.HTTPError as e:
            return False, str(e)

    def _pages(self, updated_after: str | None):
        cursor = None
        while True:
            params = {}
            if updated_after:
                params["updatedAfter"] = updated_after
            if cursor:
                params["pageCursor"] = cursor
            data = self._get(f"{BASE}/export/", params).json()
            yield from data.get("results", [])
            cursor = data.get("nextPageCursor")  # VERIFY via `inspect`
            if not cursor:
                return

    def inspect(self, limit: int = 10) -> list[dict]:
        out = []
        for book in self._pages(None):
            out.append(book)
            if len(out) >= limit:
                break
        return out

    def fetch(self, updated_after: str | None) -> FetchResult:
        started = datetime.now(timezone.utc).isoformat()
        notes: list[SourceNote] = []
        for book in self._pages(updated_after):
            for h in book.get("highlights", []):
                if h.get("is_deleted") or not _matches(h, self.s.readwise.pink_filter):
                    continue
                text = (h.get("text") or "").strip()
                if not text:
                    continue
                notes.append(
                    SourceNote(
                        source=self.name,
                        source_id=str(h["id"]),
                        text=text,
                        book_title=book.get("title"),
                        author=book.get("author"),
                        source_updated_at=h.get("updated") or h.get("highlighted_at"),
                        raw={"highlight": h, "book": {k: v for k, v in book.items() if k != "highlights"}},
                    )
                )
        return FetchResult(notes=notes, cursor=started)
