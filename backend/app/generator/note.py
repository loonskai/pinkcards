"""Builds the NOTE field in code (never by the LLM). All wording lives here."""
from __future__ import annotations

from ..models import SOURCE_KIND

BASE_FORM_LABEL = "Base form: "  # DEFAULT wording; change only here


def reference_line(expression: str, is_base_form: bool, grammar: dict | None, language: str | None = None) -> str:
    g = grammar or {}
    gender, article, aspect = g.get("gender"), g.get("article"), (g.get("aspect_pair") or "").strip()
    shown = expression
    if aspect:
        # "osiągnąć cel" + "osiągać / osiągnąć" -> "osiągać / osiągnąć cel"
        words = expression.split()
        tail = " ".join(words[1:]) if len(words) > 1 and words[0] in aspect else ""
        shown = f"{aspect} {tail}".strip()
    elif article in ("le", "la"):
        shown = f"{article} {expression}"
    elif article == "l'":
        shown = f"l'{expression}" + (f" ({gender}.)" if gender else "")
    elif gender:
        shown = f"{expression} ({gender}.)"
    if is_base_form and not (gender or article or aspect):
        return ""
    return f"{BASE_FORM_LABEL}{shown}"


def source_line(source: str, book_title: str | None, author: str | None) -> str:
    kind = SOURCE_KIND.get(source, source)
    if kind == "sheets":
        return "Notes"
    label = "Kindle" if kind == "kindle" else "Zotero"
    who = " — ".join(x for x in (book_title, author) if x)
    return f"{who} · {label}" if who else label


def build_note(expression: str, is_base_form: bool, grammar: dict | None, source: str,
               book_title: str | None, author: str | None, language: str | None = None) -> str:
    lines = [reference_line(expression, is_base_form, grammar, language),
             source_line(source, book_title, author)]
    return "<br>".join(l for l in lines if l)
