from __future__ import annotations

from .base import GenerationRequest

DETAILS_RULES = """  * contractions: EVERY merged or elided form in the sentence, as {form, parts}: au/aux = à + le/les, du/des = de + le/les, j'y, qu'il, l'on, -t-il, and so on ("parts" like "que + il"). Empty list if there are none.
  * words: base forms of the NON-TRIVIAL words, as {form, base, note_ru}: at most ~5, only words a B1 learner may not know, or with a notable form, gender or government. Skip basic function words, articles, pronouns, possessives, common adverbs/time words (trop, tard, mon, demain...) and common forms of être/avoir. EXCLUDE every word that is part of the clozed item or the expression (it already has the base-form line). note_ru is shown in parentheses after the word, so it must be SHORT (a few Russian words) or empty, with no parentheses of its own. For nouns note_ru is just the gender, "m." or "f." (shown as "problème (m.)"), unless a gloss is really needed; then "m., gloss". If the form is already the base form, repeat it in both `form` and `base`.
  * construction: ONE note on the main grammatical construction around the clozed item (mood or tense choice, agreement, a fixed government like "se rendre compte de"), as {explanation_ru}: 1-2 sentences in Russian, keeping French grammar terms in French. null if nothing is notable.
  * etymology: of the clozed item's base form ONLY. A short chain with glosses in Russian, as {text_ru, confidence}. If you are not confident, return null. NEVER invent intermediate forms. Report the confidence honestly (high/medium/low)."""

SYSTEM_V1 = """You create ONE Anki cloze flashcard for a Russian-speaking learner of Polish, French or English.

Rules:
- LANGUAGE: detect it from the expression plus the source metadata, unless a fixed language is given (then do NOT detect; use it, confidence "high"). A word can exist in several languages (e.g. "pain"): use the book's language as evidence and lower the confidence when ambiguous. Use ISO 639-1 codes.
- MEANING: choose the most likely meaning given the source and any instruction. Produce exactly one card.
- FORM: PREFER A NON-BASE FORM (another case, number, person, tense, mood) whenever it reads naturally. Use the base form only if it is the natural choice. Never use a form from the excluded-forms list.
- SENTENCE: short, natural, everyday, in the target language, with enough context to pin down the meaning AND make the required form inferable from the Russian translation (the learner types the answer, so it must be fair).
- CLOZE: only {{c1::...}}. Wrap the exact form as it appears in the sentence. For split expressions (ne ... jamais, put it off, Polish "się") every part gets c1 separately. `form_used` is the cloze content (parts joined by a space).
- EXPRESSION: the dictionary/base form (infinitive, nominative singular, fixed phrase), keeping any fixed object (e.g. "osiągnąć cel").
- TRANSLATION: natural Russian (not word-for-word), with the equivalent of the clozed item wrapped in <b>...</b>.
- GRAMMAR: gender (m/f/n) and, for French nouns, article (le/la/l'); for Polish verbs, aspect_pair as "imperfective / perfective" (e.g. "skakać / skoczyć"); otherwise null.
- HINT: empty unless another common target-language synonym would also fit the Russian prompt; then "не <synonym>".
- meaning_gloss_ru: short Russian gloss of the chosen meaning.
- DETAILS: learning notes about the sentence, written to the `details` object. Produce it ONLY if the card's language is in the "details enabled for" list given in the user message; otherwise `details` is null. All explanations are in Russian; target-language terms, forms and examples stay in the target language.
""" + DETAILS_RULES + """
Return JSON matching the schema only."""


SYSTEM_V2 = SYSTEM_V1  # v2 = v1 + DETAILS rules (kept in one text; details are gated per language in the user prompt)

DETAILS_SYSTEM = """You write learning notes (the `details` object) for ONE existing Anki cloze sentence, for a Russian-speaking learner. Do NOT change or rewrite the sentence; only annotate it.

Rules:
- All explanations are in Russian; target-language terms, forms and examples stay in the target language.
- The sentence is given with the cloze either filled in or marked; the clozed item is the "form used".
""" + DETAILS_RULES + """
Return JSON matching the schema only."""


def details_enabled_line(langs: list[str]) -> str:
    return (f"Details enabled for: [{', '.join(langs)}]. Produce `details` only if the card's language is one of "
            f"[{', '.join(langs)}]; otherwise null.")


def build_details_prompt(c: dict) -> str:
    from ..tts.base import tts_text  # filled-in sentence; the cloze is also shown marked

    return "\n".join([
        f"Language: {c.get('language')}",
        f"Sentence (cloze filled in): {tts_text(c.get('text'))}",
        f"Sentence (cloze marked): {c.get('text')}",
        f"Clozed form used: {c.get('form_used')}",
        f"Expression (base form of the clozed item): {c.get('expression')}",
        f"Russian translation: {c.get('translation')}",
    ])


def build_user_prompt(r: GenerationRequest) -> str:
    L = [f'Expression/highlight: "{r.text}"', f"Source kind: {r.source}"]
    if r.book_title:
        L.append(f"Book/title: {r.book_title}")
    if r.author:
        L.append(f"Author: {r.author}")
    if r.zotero_language:
        L.append(f"Zotero language field (evidence): {r.zotero_language}")
    fixed = r.force_language or r.language_hint
    if fixed:
        L.append(f"FIXED language: {fixed}. Do not detect the language; set language_confidence=high.")
    if r.instruction:
        L.append(f"Instruction from the learner: {r.instruction}")
    if r.lemma:
        L.append(f'The lemma (use as "expression"): "{r.lemma}"')
    if r.excluded_meanings:
        L.append("Meanings ALREADY USED (choose a different meaning): " + "; ".join(r.excluded_meanings))
    if r.excluded_forms:
        L.append("Forms ALREADY USED (do not reuse): " + "; ".join(r.excluded_forms))
    if r.regen_instruction:
        L.append(f"Reviewer's regeneration instruction: {r.regen_instruction}")
    if r.details_languages is not None:
        L.append(details_enabled_line(r.details_languages))
    return "\n".join(L)
