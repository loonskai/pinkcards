"""JSON schema for OpenRouter structured output (strict mode: all props required, no extras)."""

CARD_JSON_SCHEMA = {
    "name": "anki_cloze_card",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["language", "language_confidence", "language_reason", "expression", "form_used",
                     "is_base_form", "grammar", "text", "translation", "hint", "meaning_gloss_ru"],
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
        },
    },
}
