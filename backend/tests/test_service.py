from app.generator.base import GenerationError, Generator
from app.models import GeneratedCard, Grammar, SourceNote
from app.service import Service
from app.sources.base import FetchResult, SourceAdapter


class FakeSource(SourceAdapter):
    name = "gsheets"
    def __init__(self, notes): self.notes = notes
    def fetch(self, u): return FetchResult(self.notes, None)
    def inspect(self, limit=10): return []
    def health(self): return True, "ok"


class FakeGen(Generator):
    model, prompt_version = "fake", "v1"
    def __init__(self): self.calls = []
    def health(self): return True, "ok"
    def generate(self, req):
        self.calls.append(req)
        if req.text == "bad":
            raise GenerationError("nope")
        if req.text == "xx":
            return self._card("de", "xx", "xx")
        form = "skoczył" if "skakać" in req.excluded_forms or not req.excluded_forms else "skoczę"
        if req.excluded_forms and "skoczył" in req.excluded_forms:
            form = "skoczę"
        return self._card(req.language_hint or req.force_language or "pl", "skoczyć", form)
    def _card(self, lang, expr, form):
        return GeneratedCard(language=lang, language_confidence="high", language_reason="r", expression=expr,
                             form_used=form, is_base_form=False, grammar=Grammar(aspect_pair="skakać / skoczyć"),
                             text=f"On {{{{c1::{form}}}}}.", translation="Он <b>прыгнул</b>.", hint="",
                             meaning_gloss_ru="прыгать")


def make(settings, store, notes):
    return Service(settings, store, {"gsheets": FakeSource(notes)}, FakeGen(), anki=None)


def test_sync_flow_and_dedup(settings, store):
    notes = [SourceNote(source="gsheets", source_id="S:skakać", text="skakać", language_hint="pl", instruction="verb"),
             SourceNote(source="gsheets", source_id="S:bad", text="bad", language_hint="pl"),
             SourceNote(source="gsheets", source_id="S:xx", text="xx")]
    svc = make(settings, store, notes)
    r = svc.sync()
    assert (r["generated"], r["failed"], r["unsupported"]) == (1, 1, 1)
    c = store.list_cards("generated")[0]
    assert c["deck"] == "Polish" and c["tags"] == ["auto", "src::sheets"]
    assert c["note"] == "Base form: skakać / skoczyć<br>Notes"
    assert store.get_source_note("gsheets:S:xx")["status"] == "unsupported"

    # second sync creates nothing new
    r2 = svc.sync()
    assert r2["generated"] == 0
    assert store.conn.execute("SELECT count(*) FROM cards").fetchone()[0] == 2

    # approve, then a new-form child excludes the used form
    assert svc.approve([c["id"]])["approved"] == [c["id"]]
    child = svc.add_child(c["id"], "new_form")
    assert child["parent_card_id"] == c["id"] and child["kind"] == "new_form"
    assert "skoczył" in svc.gen.calls[-1].excluded_forms


def test_unsupported_requeued_when_language_added(settings, store):
    svc = make(settings, store, [SourceNote(source="gsheets", source_id="S:xx", text="xx")])
    svc.sync()
    settings.anki.decks["de"] = "German"
    assert store.requeue_unsupported(settings.supported_languages) == 1


def test_edit_rebuilds_note_and_requires_reapproval(settings, store):
    svc = make(settings, store, [SourceNote(source="gsheets", source_id="S:a", text="a", language_hint="pl")])
    svc.sync()
    c = store.list_cards()[0]
    svc.approve([c["id"]])
    out = svc.edit(c["id"], expression="dom", grammar={"gender": "m"}, is_base_form=False)
    assert out["note"] == "Base form: dom (m.)<br>Notes" and out["status"] == "generated"
    bad = svc.edit(c["id"], text="no cloze")
    assert bad["validation_errors"] and svc.approve([c["id"]])["refused"]


def test_known_word_flagged(settings, store):
    svc = make(settings, store, [SourceNote(source="gsheets", source_id="S:a", text="a", language_hint="pl")])
    svc.sync()
    svc.approve([store.list_cards()[0]["id"]])
    store.upsert_source_notes([SourceNote(source="gsheets", source_id="S:b", text="b", language_hint="pl")])
    svc.sync()
    assert [c["already_known"] for c in store.list_cards("generated")] == [1]
