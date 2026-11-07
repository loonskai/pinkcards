import json

import httpx
import pytest

from app.generator.base import GenerationError, GenerationRequest
from app.generator.openrouter import OpenRouterGenerator
from app.sinks.ankiconnect import AnkiConnect, AnkiConnectError

GOOD = {"language": "pl", "language_confidence": "high", "language_reason": "r", "expression": "dom",
        "form_used": "domu", "is_base_form": False, "grammar": {"gender": "m", "article": None, "aspect_pair": None},
        "text": "Wracam do {{c1::domu}}.", "translation": "Я возвращаюсь <b>домой</b>.", "hint": "",
        "meaning_gloss_ru": "дом"}


def _gen(settings, contents):
    it = iter(contents); sent = []
    def handler(req):
        sent.append(json.loads(req.content))
        c = next(it)
        if isinstance(c, int):
            return httpx.Response(c)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(c)}}]})
    g = OpenRouterGenerator(settings, httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    return g, sent


def test_request_shape_and_success(settings):
    g, sent = _gen(settings, [GOOD])
    card = g.generate(GenerationRequest(text="dom", source="gsheets", language_hint="pl"))
    assert card.form_used == "domu"
    b = sent[0]
    assert b["response_format"]["json_schema"]["strict"] is True
    assert b["provider"] == {"require_parameters": True} and b["model"] == "test/model"


def test_validation_failure_retries_once_then_fails(settings):
    bad = {**GOOD, "text": "no cloze"}
    g, sent = _gen(settings, [bad, GOOD])
    assert g.generate(GenerationRequest(text="dom", source="gsheets")).expression == "dom"
    assert len(sent) == 2
    g, sent = _gen(settings, [bad, bad])
    with pytest.raises(GenerationError):
        g.generate(GenerationRequest(text="dom", source="gsheets"))
    assert len(sent) == 2


def test_404_error_shows_openrouter_message(settings):
    def handler(req):
        return httpx.Response(404, json={"error": {"message": "No endpoints found that can handle the requested parameters."}})
    g = OpenRouterGenerator(settings, httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    with pytest.raises(GenerationError, match="No endpoints found.*test/model"):
        g.generate(GenerationRequest(text="dom", source="gsheets"))


def test_5xx_retried(settings):
    g, sent = _gen(settings, [503, 502, GOOD])
    assert g.generate(GenerationRequest(text="dom", source="gsheets")).language == "pl"
    assert len(sent) == 3


class FakeAnki:
    def __init__(self, fields=None, decks=("Polish", "French", "English")):
        self.fields = fields or ["Text", "Expression", "Translation", "Picture", "HINT", "NOTE", "Audio"]
        self.decks, self.added = decks, []
    def handler(self, req):
        b = json.loads(req.content); a = b["action"]; p = b["params"]
        res = {"version": 6, "modelNames": ["Cloze"], "modelFieldNames": self.fields,
               "deckNames": list(self.decks)}.get(a)
        if a == "addNote":
            self.added.append(p["note"]); res = 123
        return httpx.Response(200, json={"result": res, "error": None})


def _anki(settings, fake):
    return AnkiConnect(settings, httpx.Client(transport=httpx.MockTransport(fake.handler)))


def test_startup_checks(settings):
    assert _anki(settings, FakeAnki()).check() == []
    p = _anki(settings, FakeAnki(fields=["Text", "Back Extra"])).check()
    assert any("clash" in x for x in p)
    assert any("French" in x for x in _anki(settings, FakeAnki(decks=("Polish", "English"))).check())


def test_push_blocked_on_mismatch_and_validates_cloze(settings):
    with pytest.raises(AnkiConnectError):
        _anki(settings, FakeAnki(fields=["Text"])).push([])
    fake = FakeAnki()
    card = {"id": 1, "text": "Wracam do {{c1::domu}}.", "expression": "dom", "translation": "<b>x</b>",
            "hint": "", "note": "n", "deck": "Polish", "tags": ["auto"]}
    res = _anki(settings, fake).push([card, {**card, "id": 2, "text": "{{c2::x}}"}])
    assert res[0].ok and res[0].anki_note_id == 123 and not res[1].ok
    assert len(fake.added) == 1 and fake.added[0]["fields"]["Picture"] == ""


def test_find_query_escaping():
    assert AnkiConnect.find_query("Polish", 'a"b*c_d:e') == 'deck:"Polish" "Expression:a\\"b\\*c\\_d\\:e"'


def test_schema_has_no_typelist_enum_with_null():
    """Anthropic strict mode 400s on {"type": ["string","null"], "enum": [...]}."""
    from app.generator.schema import CARD_JSON_SCHEMA

    def walk(n):
        if isinstance(n, dict):
            if isinstance(n.get("type"), list) and "enum" in n:
                raise AssertionError(n)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(CARD_JSON_SCHEMA["schema"])
