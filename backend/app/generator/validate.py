"""Code-side validation of generated cards. Cards failing here are never shown as ready to approve."""
from __future__ import annotations

import re

from ..dedup import normalize

_CLOZE_RE = re.compile(r"\{\{c(\d+)::(.*?)\}\}", re.S)
_ELLIPSIS_RE = re.compile(r"…|\.\.\.")
_LANG_RE = re.compile(r"^[a-z]{2}$")


def cloze_parts(text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in _CLOZE_RE.finditer(text)]


def _squash(s: str) -> str:
    return normalize(_ELLIPSIS_RE.sub(" ", s))


def validate_cloze_text(text: str) -> list[str]:
    """Cloze-only checks; reused by the Anki sink before each addNote."""
    errs: list[str] = []
    parts = cloze_parts(text or "")
    if not parts:
        errs.append("text has no {{c1::…}} cloze")
        return errs
    if any(n != "1" for n, _ in parts):
        errs.append("only c1 is allowed")
    if any(not c.strip() for _, c in parts):
        errs.append("empty cloze")
    leftover = _CLOZE_RE.sub("", text)
    if "{{" in leftover or "}}" in leftover:
        errs.append("unbalanced braces")
    return errs


def validate_card(c: dict, supported_or_any_lang: bool = True) -> list[str]:
    """`c` has keys text, translation, expression, form_used, is_base_form, language."""
    errs = validate_cloze_text(c.get("text") or "")
    parts = cloze_parts(c.get("text") or "")
    if parts and not errs:
        joined = " ".join(p for _, p in parts)
        if _squash(joined) != _squash(c.get("form_used") or ""):
            errs.append(f"cloze content {joined!r} != form_used {c.get('form_used')!r}")
    if not re.search(r"<b>.+?</b>", c.get("translation") or "", re.S):
        errs.append("translation has no <b>…</b>")
    if not (c.get("expression") or "").strip():
        errs.append("expression is empty")
    if not _LANG_RE.match(c.get("language") or ""):
        errs.append(f"invalid language code {c.get('language')!r}")
    if c.get("is_base_form") and _squash(c.get("form_used") or "") != _squash(c.get("expression") or ""):
        errs.append("is_base_form is true but form_used != expression")
    return errs
