from __future__ import annotations

import hashlib
import html
import re
from abc import ABC, abstractmethod

_CLOZE_RE = re.compile(r"\{\{c\d+::(.*?)\}\}", re.S)
_TAG_RE = re.compile(r"<[^>]*>")
_BR_RE = re.compile(r"<\s*(br|/p|/div|/li)\s*/?>", re.I)


class TTSError(Exception):
    pass


def tts_text(card_text: str | None) -> str:
    """Cloze markup -> the plain sentence with every cloze filled in (answer shown), as it should be spoken.

    `{{c1::form}}` and `{{c1::form::hint}}` both become `form`; HTML is stripped; whitespace is collapsed.
    """
    t = _CLOZE_RE.sub(lambda m: m.group(1).split("::", 1)[0], card_text or "")
    t = _TAG_RE.sub("", _BR_RE.sub(" ", t))
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def text_hash(card_text: str | None) -> str:
    return hashlib.sha1(tts_text(card_text).encode("utf-8")).hexdigest()


def audio_filename(card_id: int, text_hash_: str) -> str:
    """Unique in Anki's shared media folder, and a different sentence never reuses it."""
    return f"pinkcards_{card_id}_{text_hash_[:8]}.mp3"


class TTS(ABC):
    model: str

    @abstractmethod
    def synthesize(self, text: str, language: str, voice: str | None = None) -> bytes:
        """Return mp3 bytes for `text` read in `language`. Raises TTSError."""

    @abstractmethod
    def health(self) -> tuple[bool, str]: ...
