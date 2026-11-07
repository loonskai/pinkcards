"""Builds the NOTE field in code (never by the LLM). All wording lives here."""
from __future__ import annotations

import html

from ..models import SOURCE_KIND

BASE_FORM_LABEL = "Base form: "  # DEFAULT wording; change only here
CONTRACTIONS_LABEL = "Слияния:"
WORDS_LABEL = "Формы:"
CONSTRUCTION_LABEL = "Грамматика:"
ETYMOLOGY_LABEL = "Этимология:"
SEP = " · "


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


def _e(x) -> str:
    return html.escape((x or "").strip(), quote=False)


def _label(text: str) -> str:
    return f'<span class="pc-label">{text}</span>'


def details_lines(details: dict | None) -> list[str]:
    """Contractions / forms / grammar / etymology lines (HTML, LLM text escaped). Empty sections are omitted."""
    d = details or {}
    lines: list[str] = []
    contr = [f"{_e(c.get('form'))} = {_e(c.get('parts'))}" for c in d.get("contractions") or []
             if (c.get("form") or "").strip() and (c.get("parts") or "").strip()]
    if contr:
        lines.append(f"{_label(CONTRACTIONS_LABEL)} {SEP.join(contr)}")
    words = []
    for w in d.get("words") or []:
        form, base, note = (w.get("form") or "").strip(), (w.get("base") or "").strip(), (w.get("note_ru") or "").strip()
        if not (form or base):
            continue
        shown = _e(base or form) if (not base or not form or form.casefold() == base.casefold()) \
            else f"{_e(form)} → {_e(base)}"
        words.append(shown + (f" ({_e(note)})" if note else ""))
    if words:
        lines.append(f"{_label(WORDS_LABEL)} {SEP.join(words)}")
    cons = ((d.get("construction") or {}).get("explanation_ru") or "").strip()
    if cons:
        lines.append(f"{_label(CONSTRUCTION_LABEL)} {_e(cons)}")
    ety = d.get("etymology") or {}
    text, conf = (ety.get("text_ru") or "").strip(), ety.get("confidence")
    if text and conf in ("high", "medium"):  # low confidence = not shown at all
        lines.append(f"{_label(ETYMOLOGY_LABEL)} {_e(text)}" + (" (?)" if conf == "medium" else ""))
    return lines


def build_note(expression: str, is_base_form: bool, grammar: dict | None, source: str,
               book_title: str | None, author: str | None, language: str | None = None,
               details: dict | None = None) -> str:
    """base-form line, then the details block, then the source line (last).

    Without details the output equals the old `<br>`-joined note, except that the source is wrapped in
    `pc-source`. With details, the blocks are <div>s (no <br> needed between them)."""
    out = reference_line(expression, is_base_form, grammar, language)
    dl = details_lines(details)
    if dl:
        out += '<div class="pc-details">' + "<br>".join(dl) + "</div>"
    src = source_line(source, book_title, author)
    if src:
        if out and not dl:
            out += "<br>"
        out += f'<div class="pc-source">{src}</div>'
    return out
