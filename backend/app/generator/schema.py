"""JSON schema for OpenRouter structured output (strict mode: all props required, no extras)."""

def _nullable(schema: dict) -> dict:
    # anyOf (not type:[..,"null"]): Anthropic's strict mode rejects the latter in some combinations
    return {"anyOf": [schema, {"type": "null"}]}


DETAILS_OBJECT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["contractions", "words", "construction", "etymology"],
    "properties": {
        "contractions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["form", "parts"],
            "properties": {"form": {"type": "string"}, "parts": {"type": "string", "description": "e.g. que + il"}}}},
        "words": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["form", "base", "note_ru"],
            "properties": {"form": {"type": "string"}, "base": {"type": "string"},
                           "note_ru": {"type": "string", "description": "a few Russian words, or empty"}}}},
        "construction": _nullable({
            "type": "object", "additionalProperties": False, "required": ["explanation_ru"],
            "properties": {"explanation_ru": {"type": "string"}}}),
        "etymology": _nullable({
            "type": "object", "additionalProperties": False, "required": ["text_ru", "confidence"],
            "properties": {"text_ru": {"type": "string"},
                           "confidence": {"type": "string", "enum": ["high", "medium", "low"]}}}),
    },
}

# Standalone schema for `generate_details` (just the details object).
DETAILS_JSON_SCHEMA = {"name": "anki_cloze_details", "strict": True, "schema": DETAILS_OBJECT}

CARD_JSON_SCHEMA = {
    "name": "anki_cloze_card",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["language", "language_confidence", "language_reason", "expression", "form_used",
                     "is_base_form", "grammar", "text", "translation", "hint", "meaning_gloss_ru", "details"],
        "properties": {
            "language": {"type": "string", "description": "ISO 639-1 code, e.g. pl, fr, en"},
            "language_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "language_reason": {"type": "string"},
            "expression": {"type": "string", "description": "base/dictionary form"},
            "form_used": {"type": "string", "description": "exact form clozed in the sentence"},
            "is_base_form": {"type": "boolean"},
            "grammar": {
                "type": "object",
                "additionalProperties": False,
                "required": ["gender", "article", "aspect_pair"],
                "properties": {
                    # anyOf (not type:[..,"null"] + enum): Anthropic's strict mode rejects the latter
                    "gender": {"anyOf": [{"type": "string", "enum": ["m", "f", "n"]}, {"type": "null"}]},
                    "article": {"anyOf": [{"type": "string", "enum": ["le", "la", "l'"]}, {"type": "null"}]},
                    "aspect_pair": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
            },
            "text": {"type": "string", "description": "sentence with {{c1::...}}"},
            "translation": {"type": "string", "description": "Russian sentence with <b>...</b>"},
            "hint": {"type": "string"},
            "meaning_gloss_ru": {"type": "string"},
            "details": _nullable(DETAILS_OBJECT),
        },
    },
}
