from __future__ import annotations

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

    def _fields(self, c: dict) -> dict:
        return {"Text": c["text"], "Expression": c["expression"], "Translation": c["translation"],
                "Picture": "", "HINT": c.get("hint") or "", "NOTE": c.get("note") or ""}

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
                nid = self.invoke("addNote", note={
                    "deckName": c["deck"], "modelName": self.s.anki.note_type,
                    "fields": self._fields(c), "tags": c.get("tags") or [],
                    "options": {"allowDuplicate": False, "duplicateScope": "deck"},
                })
                out.append(PushResult(c["id"], True, anki_note_id=nid))
            except AnkiConnectError as e:
                out.append(PushResult(c["id"], False, error=str(e)))
        return out
