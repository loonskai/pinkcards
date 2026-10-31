from __future__ import annotations

import re
import unicodedata


def normalize(s: str) -> str:
    """NFC + lowercase + collapsed/trimmed whitespace."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "")).strip().lower()


def dedup_key(language: str, expression: str) -> tuple[str, str]:
    return (language, normalize(expression))


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
