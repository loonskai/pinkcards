import json
import sqlite3

import httpx
import pytest

from app.config import DetailsLangCfg
from app.generator.base import GenerationError
from app.generator.note import build_note
from app.generator.openrouter import OpenRouterGenerator
from app.generator.prompt import build_user_prompt
from app.generator.base import GenerationRequest
from app.generator.schema import CARD_JSON_SCHEMA, DETAILS_JSON_SCHEMA
from app.models import Details, GeneratedCard, Grammar, SourceNote
from app.service import Service
from app.sinks.ankiconnect import AnkiConnect
from app.store.db import MIGRATIONS, connect

from .test_anki_openrouter import FakeAnki, GOOD
from .test_service import FakeGen, FakeSource

DETAILS = {
    "contractions": [{"form": "qu'il", "parts": "que + il"}, {"form": "du", "parts": "de + le"}],
    "words": [{"form": "faut", "base": "falloir", "note_ru": "безличный"},
              {"form": "problème", "base": "problème", "note_ru": "m."}],
    "construction": {"explanation_ru": "после «il faut que» — subjonctif présent."},
    "etymology": {"text_ru": "rendre ← лат. reddere", "confidence": "high"},
}
SRC = '<div class="pc-source">Notes</div>'


def note(details, **kw):
    return build_note("se rendre compte", False, {}, "gsheets", None, None, "fr", details)


# ---------- build_note ----------
def test_no_details_is_identical_to_old_output_except_source_wrapper():
    old = "<br>".join(["Base form: se rendre compte", "Notes"])
    assert note(None) == old.replace("Notes", SRC)
    assert note({"contractions": [], "words": [], "construction": None, "etymology": None}) == note(None)
    assert build_note("dom", True, {}, "gsheets", None, None) == SRC  # no base line: source only


def test_each_section_alone():
    base = "Base form: se rendre compte"
    d = lambda inner: f'<div class="pc-details">{inner}</div>'  # noqa: E731
    L = lambda t: f'<span class="pc-label">{t}</span>'  # noqa: E731
    assert note({"contractions": DETAILS["contractions"]}) == \
        base + d(f"{L('Слияния:')} qu'il = que + il · du = de + le") + SRC
    assert note({"words": DETAILS["words"]}) == \
        base + d(f"{L('Формы:')} faut → falloir (безличный) · problème (m.)") + SRC
    assert note({"construction": DETAILS["construction"]}) == \
        base + d(f"{L('Грамматика:')} после «il faut que» — subjonctif présent.") + SRC
    assert note({"etymology": DETAILS["etymology"]}) == \
        base + d(f"{L('Этимология:')} rendre ← лат. reddere") + SRC


def test_all_sections_together_and_order():
    n = note(DETAILS)
    pos = [n.index(x) for x in ("Base form:", "Слияния:", "Формы:", "Грамматика:", "Этимология:", "pc-source")]
    assert pos == sorted(pos)
    assert n.count('<div class="pc-details">') == 1 and n.endswith(SRC)
    assert n.count("<br>") == 3  # between the four details lines only
    assert n.startswith("Base form: se rendre compte<div")


def test_etymology_confidence():
    e = lambda c: note({"etymology": {"text_ru": "x", "confidence": c}})  # noqa: E731
    assert "Этимология" not in e("low") and e("low") == note(None)
    assert "x (?)</div>" in e("medium")
    assert "x</div>" in e("high") and "(?)" not in e("high")


def test_forms_edge_cases():
    n = note({"words": [{"form": "Faut", "base": "faut", "note_ru": ""}, {"form": "", "base": "mot", "note_ru": "f."},
                        {"form": "a", "base": "b", "note_ru": ""}, {"form": "", "base": "", "note_ru": "x"}]})
    assert "faut · mot (f.) · a → b</div>" in n


def test_html_escaping_of_llm_text():
    n = note({"contractions": [{"form": "<b>x</b>", "parts": "a & b"}],
              "words": [{"form": "<i>", "base": "z", "note_ru": "<script>"}],
              "construction": {"explanation_ru": "1 < 2 & «x»"},
              "etymology": {"text_ru": "<img src=x>", "confidence": "high"}})
    for bad in ("<b>x", "<i>", "<script>", "<img", "1 < 2"):
        assert bad not in n
    assert "&lt;b&gt;x&lt;/b&gt; = a &amp; b" in n and "1 &lt; 2 &amp; «x»" in n


# ---------- schema / prompt / client ----------
def _walk_strict(n):
    if isinstance(n, dict):
        if n.get("type") == "object":
            assert n["additionalProperties"] is False
            assert set(n["required"]) == set(n["properties"]), n
        assert not (isinstance(n.get("type"), list) and "enum" in n)
        for v in n.values():
            _walk_strict(v)
    elif isinstance(n, list):
        for v in n:
            _walk_strict(v)


def test_schemas_are_strict_and_details_nullable():
    _walk_strict(CARD_JSON_SCHEMA["schema"]); _walk_strict(DETAILS_JSON_SCHEMA["schema"])
    top = CARD_JSON_SCHEMA["schema"]
    assert "details" in top["required"]
    any_of = top["properties"]["details"]["anyOf"]
    assert any_of[1] == {"type": "null"} and any_of[0]["type"] == "object"
    assert DETAILS_JSON_SCHEMA["schema"]["properties"]["etymology"]["anyOf"][1] == {"type": "null"}


def test_user_prompt_lists_enabled_languages(settings):
    p = build_user_prompt(GenerationRequest(text="x", source="gsheets", details_languages=["fr"]))
    assert "Produce `details` only if the card's language is one of [fr]; otherwise null." in p
    assert "Details enabled" not in build_user_prompt(GenerationRequest(text="x", source="gsheets"))
    assert settings.generator.prompt_version == "v1"  # conftest pins its own; real default is v2
    from app.config import Settings
    assert Settings().generator.prompt_version == "v2"


def _gen(settings, contents):
    it = iter(contents); sent = []
    def handler(req):
        sent.append(json.loads(req.content))
        c = next(it)
        if isinstance(c, int):
            return httpx.Response(c)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(c)}}]})
    return OpenRouterGenerator(settings, httpx.Client(transport=httpx.MockTransport(handler)),
                               sleep=lambda s: None), sent


def test_generate_parses_details_and_sends_v2_prompt(settings):
    settings.details.languages["fr"] = DetailsLangCfg(enabled=True)
    g, sent = _gen(settings, [{**GOOD, "language": "fr", "details": DETAILS}])
    card = g.generate(GenerationRequest(text="dom", source="gsheets", details_languages=["fr"]))
    assert card.details.words[0].base == "falloir" and card.details.etymology.confidence == "high"
    assert "DETAILS" in sent[0]["messages"][0]["content"] and "[fr]" in sent[0]["messages"][1]["content"]
    g, _ = _gen(settings, [{**GOOD, "details": None}])
    assert g.generate(GenerationRequest(text="dom", source="gsheets")).details is None


def test_generate_details_separate_call_and_retries(settings):
    c = {"language": "fr", "text": "Il faut qu'il {{c1::rende}} ça.", "form_used": "rende", "expression": "rendre",
         "translation": "x <b>y</b>"}
    g, sent = _gen(settings, [503, {"bad": 1}, DETAILS])
    d = g.generate_details(c)
    assert isinstance(d, Details) and d.contractions[0].form == "qu'il"
    assert len(sent) == 3  # 5xx retried, then schema error retried once
    body = sent[-1]
    assert body["response_format"]["json_schema"]["name"] == "anki_cloze_details"
    user = body["messages"][-2]["content"] if "invalid" in body["messages"][-1]["content"] else body["messages"][-1]["content"]
    assert "Il faut qu'il rende ça." in user and "rendre" in user
    # an all-empty answer means "nothing to say"
    g, _ = _gen(settings, [{"contractions": [], "words": [], "construction": None, "etymology": None}])
    assert g.generate_details(c) is None
    g, _ = _gen(settings, [{"bad": 1}, {"bad": 1}])
    with pytest.raises(GenerationError):
        g.generate_details(c)


# ---------- service ----------
class DetGen(FakeGen):
    """FakeGen that always returns details (also for non-enabled languages, like a disobedient model)."""
    def __init__(self, det=DETAILS):
        super().__init__(); self.det, self.details_calls = det, []
    def _card(self, lang, expr, form):
        c = super()._card(lang, expr, form)
        return GeneratedCard(**{**c.model_dump(), "details": self.det})
    def generate(self, req):
        c = super().generate(req)
        if req.force_language:  # FakeGen prefers language_hint; "change language" must win here
            c = GeneratedCard(**{**c.model_dump(), "language": req.force_language})
        return c
    def generate_details(self, card):
        self.details_calls.append(card["id"])
        if self.det == "boom":
            raise GenerationError("llm down")
        return Details.model_validate({**DETAILS, "etymology": {"text_ru": "NEW", "confidence": "high"}})


@pytest.fixture
def dsettings(settings):
    settings.details.languages = {"fr": DetailsLangCfg(enabled=True), "pl": DetailsLangCfg(enabled=False)}
    return settings


def make(settings, store, anki=None, gen=None):
    notes = [SourceNote(source="gsheets", source_id="S:f", text="f", language_hint="fr"),
             SourceNote(source="gsheets", source_id="S:p", text="p", language_hint="pl")]
    svc = Service(settings, store, {"gsheets": FakeSource(notes)}, gen or DetGen(), anki=anki)
    svc.sync()
    return svc, {c["language"]: c for c in store.list_cards()}


def test_details_enabled_helper(dsettings):
    assert dsettings.details_enabled("fr") and not dsettings.details_enabled("pl")
    assert not dsettings.details_enabled("en") and not dsettings.details_enabled(None)
    from app.config import Settings
    assert not Settings().details_enabled("fr") and Settings().details_languages == []
    assert dsettings.details_languages == ["fr"]


def test_gating_drops_details_for_disabled_language_keeps_for_enabled(dsettings, store):
    svc, cs = make(dsettings, store)
    assert svc.gen.calls[0].details_languages == ["fr"]
    fr, pl = cs["fr"], cs["pl"]
    assert fr["details"]["words"][0]["base"] == "falloir" and "Формы:" in fr["note"]
    assert pl["details"] is None and "pc-details" not in pl["note"]
    assert pl["note"] == 'Base form: skakać / skoczyć<br><div class="pc-source">Notes</div>'
    assert fr["details_stale"] == 0


def test_edit_text_marks_stale_keeps_details_through_note_rebuild(dsettings, store):
    svc, cs = make(dsettings, store)
    fid = cs["fr"]["id"]
    svc.approve([fid])
    svc.gen.details_calls.clear()
    out = svc.edit(fid, translation="Он <b>прыгнул</b> иначе.")
    assert out["details_stale"] == 0
    out = svc.edit(fid, expression="sauter", grammar={"gender": None, "article": None, "aspect_pair": None})
    assert out["details_stale"] == 0 and "Формы:" in out["note"] and "Base form: sauter" in out["note"]
    old_details = store.get_card(fid)["details"]
    out = svc.edit(fid, text="Ça {{c1::skoczył}} vite.")
    assert out["details_stale"] == 1 and out["status"] == "generated"
    assert store.get_card(fid)["details"] == old_details and not svc.gen.details_calls  # not rebuilt automatically
    # saving the same text again does not mark anything
    other = svc.edit(cs["pl"]["id"], text=cs["pl"]["text"])
    assert other["details_stale"] == 0


def test_approving_stale_card_regenerates_details_first(dsettings, store):
    svc, cs = make(dsettings, store)
    fid = cs["fr"]["id"]
    svc.edit(fid, text="Ça {{c1::skoczył}} vite.")
    assert svc.approve([fid])["approved"] == [fid]
    assert svc.gen.details_calls == [fid]
    c = store.get_card(fid)
    assert c["status"] == "approved" and c["details_stale"] == 0 and "NEW" in c["note"]


def test_approving_stale_card_refused_if_details_fail(dsettings, store):
    svc, cs = make(dsettings, store, gen=DetGen())
    fid = cs["fr"]["id"]
    svc.edit(fid, text="Ça {{c1::skoczył}} vite.")
    svc.gen.det = "boom"
    r = svc.approve([fid])
    assert r["approved"] == [] and "stale" in r["refused"][fid]
    assert store.get_card(fid)["status"] == "generated"


def test_approving_stale_card_in_disabled_language_skips_details(dsettings, store):
    svc, cs = make(dsettings, store)
    pid = cs["pl"]["id"]
    store.update_card(pid, details_stale=1)
    assert svc.approve([pid])["approved"] == [pid] and not svc.gen.details_calls


def test_regenerate_details_approved_keeps_status_and_rebuilds_note(dsettings, store):
    svc, cs = make(dsettings, store)
    fid = cs["fr"]["id"]
    svc.approve([fid])
    store.update_card(fid, details_stale=1)
    before = store.get_card(fid)
    r = svc.regenerate_details(fid)
    c = r["card"]
    assert r["anki"] is None and c["status"] == "approved" and c["text"] == before["text"]
    assert c["details_stale"] == 0 and c["details"]["etymology"]["text_ru"] == "NEW"
    assert "NEW" in c["note"] and c["note"] != before["note"] and c["note"].startswith("Base form")
    assert store.conn.execute("SELECT count(*) FROM events WHERE event='details_regenerated'").fetchone()[0] == 1


def test_regenerate_details_refusals_and_failure(dsettings, store):
    svc, cs = make(dsettings, store)
    with pytest.raises(ValueError, match="not enabled"):
        svc.regenerate_details(cs["pl"]["id"])
    store.update_card(cs["fr"]["id"], status="rejected")
    with pytest.raises(ValueError):
        svc.regenerate_details(cs["fr"]["id"])
    store.update_card(cs["fr"]["id"], status="generated")
    before = store.get_card(cs["fr"]["id"])["note"]
    svc.gen.det = "boom"
    r = svc.regenerate_details(cs["fr"]["id"])
    assert r["error"] == "llm down" and store.get_card(cs["fr"]["id"])["note"] == before


class NoteAnki(FakeAnki):
    def __init__(self, **kw):
        super().__init__(**kw); self.calls = []
    def handler(self, req):
        b = json.loads(req.content); self.calls.append((b["action"], b["params"]))
        if b["action"] in ("storeMediaFile", "updateNoteFields"):
            return httpx.Response(200, json={"result": None, "error": None})
        return super().handler(req)


def _anki(settings, fake):
    return AnkiConnect(settings, httpx.Client(transport=httpx.MockTransport(fake.handler)))


def test_regenerate_details_sent_card_updates_anki_note_field(dsettings, store):
    fake = NoteAnki()
    svc, cs = make(dsettings, store, anki=_anki(dsettings, fake))
    fid = cs["fr"]["id"]
    svc.approve([fid]); svc.push()
    sent = store.get_card(fid)
    assert sent["status"] == "sent" and sent["anki_note_id"] == 123
    assert "Формы:" in fake.added[0]["fields"]["NOTE"]  # NOTE with details goes out on push
    fake.calls.clear()
    r = svc.regenerate_details(fid)
    assert r["anki"] == {"updated": True} and r["card"]["status"] == "sent"
    assert fake.calls == [("updateNoteFields", {"note": {"id": 123, "fields": {"NOTE": r["card"]["note"]}}})]
    assert "NEW" in r["card"]["note"]
    # Anki down: card is still updated locally, result says so
    svc.anki.client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    r = svc.regenerate_details(fid)
    assert r["anki"]["updated"] is False and r["anki"]["error"] and r["card"]["details_stale"] == 0


def test_full_regenerate_replaces_details(dsettings, store):
    svc, cs = make(dsettings, store)
    fid = cs["fr"]["id"]
    svc.gen.det = {**DETAILS, "words": [], "etymology": None}
    out = svc.regenerate(fid)
    assert out["details"]["words"] == [] and "Формы" not in out["note"] and out["details_stale"] == 0


def test_change_language_to_disabled_drops_details(dsettings, store):
    svc, cs = make(dsettings, store)
    out = svc.change_language(cs["fr"]["id"], "pl")
    assert out["language"] == "pl" and out["details"] is None and "pc-details" not in out["note"]


# ---------- migration ----------
def test_migration_003_keeps_existing_data(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    for n in ("001_init.sql", "002_audio.sql"):
        conn.executescript((MIGRATIONS / n).read_text("utf-8"))
    conn.execute("PRAGMA user_version=2")
    conn.execute("INSERT INTO source_notes(id,source,source_id,text,fetched_at) VALUES('s:1','s','1','t','now')")
    conn.execute("INSERT INTO cards(source_note_id,language,text,expression,note,status,audio_file,created_at,updated_at)"
                 " VALUES('s:1','fr','Il {{c1::mange}}','manger','Base form: x<br>Notes','sent','a.mp3','now','now')")
    conn.commit(); conn.close()
    c = connect(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 3
    row = dict(c.execute("SELECT * FROM cards").fetchone())
    assert row["note"] == "Base form: x<br>Notes" and row["audio_file"] == "a.mp3" and row["status"] == "sent"
    assert row["details"] is None and row["details_stale"] == 0
    c.close(); connect(path).close()  # idempotent
