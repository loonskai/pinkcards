from __future__ import annotations

from .base import GenerationRequest

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
Return JSON matching the schema only."""


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
    return "\n".join(L)
