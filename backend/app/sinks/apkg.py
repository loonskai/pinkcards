"""genanki fallback. The model id/fields/templates must match the owner's note type so the import lands in it."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from ..config import Settings
from .base import PushResult, Sink


class ApkgSink(Sink):
    def __init__(self, settings: Settings, templates: list[dict] | None = None, css: str = ""):
        """templates: AnkiConnect `modelTemplates` output ({name: {Front, Back}}) -> converted below."""
        self.s = settings
        self.templates = templates
        self.css = css

    def health(self) -> tuple[bool, str]:
        return (bool(self.templates), "ok" if self.templates else "templates not loaded (fetch via AnkiConnect)")

    def _model(self):
        import genanki

        tmpls = [{"name": n, "qfmt": t["Front"], "afmt": t["Back"]} for n, t in dict(self.templates).items()]
        return genanki.Model(
            self.s.apkg.model_id, self.s.anki.note_type,
            fields=[{"name": f} for f in self.s.anki.expected_fields],
            templates=tmpls, css=self.css, model_type=genanki.Model.CLOZE,
        )

    @staticmethod
    def _deck_id(name: str) -> int:
        return int(hashlib.sha1(name.encode()).hexdigest()[:8], 16)

    def push(self, cards: list[dict]) -> list[PushResult]:
        import genanki

        if not self.templates:
            raise RuntimeError("templates not loaded")
        model = self._model()
        decks: dict[str, genanki.Deck] = {}
        for c in cards:
            d = decks.setdefault(c["deck"], genanki.Deck(self._deck_id(c["deck"]), c["deck"]))
            d.add_note(genanki.Note(
                model=model,
                fields=[c["text"], c["expression"], c["translation"], "", c.get("hint") or "", c.get("note") or ""],
                tags=[t.replace(" ", "_") for t in (c.get("tags") or [])],
                guid=genanki.guid_for(c["text"], c["expression"]),
            ))
        out_dir = self.s.path(self.s.apkg.export_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"cards-{datetime.now():%Y%m%d-%H%M%S}.apkg"
        genanki.Package(list(decks.values())).write_to_file(str(path))
        self.last_path = path
        return [PushResult(c["id"], True) for c in cards]
