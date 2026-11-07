from __future__ import annotations

import base64
from pathlib import Path

import httpx

from ..config import Settings
from ..generator.validate import validate_cloze_text
from .base import PushResult, Sink


class AnkiConnectError(Exception):
    pass


class AnkiConnect(Sink):
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.s = settings
        self.url = settings.anki.connect_url
        self.client = client or httpx.Client(timeout=30)

    def invoke(self, action: str, **params):
        try:
            r = self.client.post(self.url, json={"action": action, "version": 6, "params": params})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise AnkiConnectError(f"AnkiConnect unreachable: {e}") from e
        data = r.json()
        if data.get("error"):
            raise AnkiConnectError(data["error"])
        return data["result"]

    def check(self) -> list[str]:
        """Startup checks; returns a list of problems (empty = OK). Push is refused if non-empty."""
        a = self.s.anki
        try:
            self.invoke("version")
        except AnkiConnectError as e:
            return [str(e)]
        problems: list[str] = []
        models = self.invoke("modelNames")
        if a.note_type not in models:
            problems.append(f"note type {a.note_type!r} not found")
        else:
            fields = self.invoke("modelFieldNames", modelName=a.note_type)
            if "Audio" in a.expected_fields and "Audio" not in fields:
                problems.append(f"add a field named Audio to note type {a.note_type!r} and put {{{{Audio}}}} on the "
                                "back template -- see README")
                fields = fields + ["Audio"]  # compare the rest below without reporting Audio twice
            if fields != a.expected_fields:
                hint = ""
                if a.note_type == "Cloze":
                    hint = (" — this may be a clash with Anki's built-in 'Cloze'; rename your note type "
                            "(e.g. 'Cloze RU') in Anki and update anki.note_type in config.toml")
                problems.append(f"note type {a.note_type!r} fields {fields} != expected {a.expected_fields}{hint}")
        decks = set(self.invoke("deckNames"))
        for lang, deck in a.decks.items():
            if deck not in decks:
                problems.append(f"deck {deck!r} ({lang}) does not exist")
        return problems

    def health(self) -> tuple[bool, str]:
        problems = self.check()
        return (not problems, "ok" if not problems else "; ".join(problems))

    def model_templates(self) -> dict:
        n = self.s.anki.note_type
        return {"templates": self.invoke("modelTemplates", modelName=n),
                "css": self.invoke("modelStyling", modelName=n)["css"]}

    @staticmethod
    def find_query(deck: str, expression: str) -> str:
        """Field search with escaping of \\ " * _ : per Anki search syntax. VERIFY against live Anki."""
        esc = expression
        for ch in ("\\", '"', "*", "_"):
            esc = esc.replace(ch, "\\" + ch)
        esc = esc.replace(":", "\\:")
        return f'deck:"{deck}" "Expression:{esc}"'

    def known_in_anki(self, deck: str, expression: str) -> bool:
        try:
            return bool(self.invoke("findNotes", query=self.find_query(deck, expression)))
        except AnkiConnectError:
            return False

    @staticmethod
    def _plain(html_text: str) -> str:
        import html as _h
        import re
        t = re.sub(r"<\s*(br|/p|/div|/li)\s*/?>", " ", html_text or "", flags=re.I)  # line breaks -> space
        t = re.sub(r"<[^>]*>", "", t)  # inline tags (<b>, <i>) vanish without leaving a gap
        return re.sub(r"\s+", " ", _h.unescape(t)).strip()

    def find_note_ids(self, deck: str, expression: str, text: str) -> list[int]:
        """Notes in `deck` whose Expression was searched and whose Text field equals `text` (markup/whitespace
        insensitive). The search is only a pre-filter; the Text comparison is what decides."""
        ids = self.invoke("findNotes", query=self.find_query(deck, expression))
        if not ids:
            return []
        want = self._plain(text)
        out = []
        for n in self.invoke("notesInfo", notes=ids):
            if self._plain((n.get("fields", {}).get("Text") or {}).get("value", "")) == want:
                out.append(n["noteId"])
        return out

    def _fields(self, c: dict) -> dict:
        return {"Text": c["text"], "Expression": c["expression"], "Translation": c["translation"],
                "Picture": "", "HINT": c.get("hint") or "", "NOTE": c.get("note") or "",
                "Audio": f"[sound:{c['audio_file']}]" if c.get("audio_file") else ""}

    def push(self, cards: list[dict]) -> list[PushResult]:
        problems = self.check()
        if problems:
            raise AnkiConnectError("; ".join(problems))
        out: list[PushResult] = []
        for c in cards:  # one addNote per card: addNotes hides per-item errors
            errs = validate_cloze_text(c["text"])
            if errs:
                out.append(PushResult(c["id"], False, error="invalid cloze: " + "; ".join(errs)))
                continue
            try:
                if c.get("audio_file"):  # explicit storeMediaFile gives clearer errors than addNote's `audio`
                    path = self.s.audio_dir / Path(c["audio_file"]).name
                    self.invoke("storeMediaFile", filename=c["audio_file"],
                                data=base64.b64encode(path.read_bytes()).decode())
                nid = self.invoke("addNote", note={
                    "deckName": c["deck"], "modelName": self.s.anki.note_type,
                    "fields": self._fields(c), "tags": c.get("tags") or [],
                    "options": {"allowDuplicate": False, "duplicateScope": "deck"},
                })
                out.append(PushResult(c["id"], True, anki_note_id=nid))
            except (AnkiConnectError, OSError) as e:
                out.append(PushResult(c["id"], False, error=str(e)))
        return out
