import base64
import json
import sqlite3

import httpx
import pytest

from app.config import Settings
from app.models import SourceNote
from app.service import Service
from app.sinks.ankiconnect import AnkiConnect
from app.sinks.apkg import ApkgSink
from app.store.db import MIGRATIONS, Store, connect
from app.tts.base import TTS, TTSError, audio_filename, text_hash, tts_text
from app.tts.openrouter import OpenRouterTTS, pcm_to_mp3

from .test_anki_openrouter import FakeAnki
from .test_service import FakeGen, FakeSource

MP3 = b"ID3\x04fake-mp3-bytes"


# ---------- helpers ----------
@pytest.fixture
def tsettings(tmp_path):
    return Settings(
        root=tmp_path,
        store={"db_path": str(tmp_path / "t.db"), "backup_dir": str(tmp_path / "bk"), "backup_keep": 3},
        generator={"model": "test/model"},
        anki={"decks": {"pl": "Polish", "fr": "French", "en": "English"}},
        tts={"model": "google/test-tts", "audio_dir": str(tmp_path / "audio"),
             "languages": {"fr": {"enabled": True, "voice": "Kore"}, "pl": {"enabled": False},
                           "en": {"enabled": False}}},
        openrouter_api_key="k",
    )


@pytest.fixture
def tstore(tsettings):
    return Store(connect(tsettings.db_path))


class FakeTTS(TTS):
    model = "fake"
    def __init__(self, fail=None): self.calls, self.fail = [], fail
    def health(self): return True, "ok"
    def synthesize(self, text, language, voice=None):
        self.calls.append((text, language))
        if self.fail:
            raise TTSError(self.fail)
        return MP3 + str(len(self.calls)).encode()


class AudioAnki(FakeAnki):
    """FakeAnki that records every call in order."""
    def __init__(self, **kw):
        super().__init__(**kw); self.calls = []
    def handler(self, req):
        b = json.loads(req.content); self.calls.append((b["action"], b["params"]))
        if b["action"] in ("storeMediaFile", "updateNoteFields", "deleteMediaFile"):
            return httpx.Response(200, json={"result": None, "error": None})
        return super().handler(req)
    def names(self): return [a for a, _ in self.calls]


def make(tsettings, tstore, tts=None, anki=None):
    notes = [SourceNote(source="gsheets", source_id="S:a", text="a", language_hint="fr"),
             SourceNote(source="gsheets", source_id="S:b", text="b", language_hint="pl")]
    svc = Service(tsettings, tstore, {"gsheets": FakeSource(notes)}, FakeGen(), anki=anki,
                  tts=tts if tts is not None else FakeTTS())
    svc.sync()
    return svc


def cards(store):
    return {c["language"]: c for c in store.list_cards()}


def anki_for(tsettings, fake):
    return AnkiConnect(tsettings, httpx.Client(transport=httpx.MockTransport(fake.handler)))


# ---------- tts_text / names / config ----------
@pytest.mark.parametrize("src,out", [
    ("Il ne {{c1::mange}} {{c1::jamais}} de pain.", "Il ne mange jamais de pain."),
    ("Il {{c1::mange::manger}} du pain", "Il mange du pain"),
    ("{{c1::a::h1}} et {{c2::b::h2}}", "a et b"),
    ("Il <b>mange</b>&nbsp;du&amp;pain<br>  ici {{c1::<i>là</i>}}", "Il mange du&pain ici là"),
    ("  multi\n  line \t {{c1::x}} ", "multi line x"),
    ("{{c1::l'été::saison}}", "l'été"),
    (None, ""),
])
def test_tts_text(src, out):
    assert tts_text(src) == out


def test_file_name_and_hash():
    h = text_hash("Il {{c1::mange}}.")
    assert h == text_hash("Il <b>{{c1::mange::x}}</b>.")  # hash is of the spoken text
    assert h != text_hash("Il {{c1::mangeons}}.")
    assert audio_filename(7, h) == f"pinkcards_7_{h[:8]}.mp3"
    import hashlib
    assert h == hashlib.sha1(b"Il mange.").hexdigest()


def test_tts_enabled_logic(tsettings):
    s = tsettings
    assert s.tts_enabled("fr") and not s.tts_enabled("pl") and not s.tts_enabled("en")
    assert not s.tts_enabled("de") and not s.tts_enabled(None)
    s.tts.languages["pl"].enabled = True  # enabled but no voice
    assert not s.tts_enabled("pl")
    s.tts.languages["pl"].voice = "X"
    assert s.tts_enabled("pl")
    s.tts.model = ""  # no model
    assert not s.tts_enabled("fr") and not s.tts_enabled("pl")
    s.tts.model = "m"; s.tts.languages["fr"].enabled = False
    assert not s.tts_enabled("fr")
    assert not Settings().tts_enabled("fr")  # defaults: everything off


# ---------- OpenRouter client ----------
def _client(tsettings, handler):
    return OpenRouterTTS(tsettings, httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)


def test_client_gemini_pcm_converted_to_mp3(tsettings):
    sent = []
    pcm = b"\x00\x01" * 24000
    def handler(req):
        sent.append((json.loads(req.content), req.headers["authorization"]))
        return httpx.Response(200, content=pcm, headers={"content-type": "audio/pcm;rate=24000;channels=1"})
    out = _client(tsettings, handler).synthesize("Il mange.", "fr")
    body, auth = sent[0]
    assert body == {"model": "google/test-tts", "input": "Il mange.", "voice": "Kore", "response_format": "pcm"}
    assert auth == "Bearer k" and len(sent) == 1
    assert out[:3] == b"ID3" or out[0] == 0xFF
    assert out == pcm_to_mp3(pcm)


def test_client_non_google_asks_mp3_and_passes_through(tsettings):
    tsettings.tts.model = "other/tts"
    sent = []
    def handler(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
    assert _client(tsettings, handler).synthesize("x", "fr") == MP3
    assert sent[0]["response_format"] == "mp3"


def test_client_retries_5xx_and_network_errors_but_not_4xx(tsettings):
    seq = iter([503, "net", 502, "ok"])
    n = []
    def handler(req):
        n.append(1); v = next(seq)
        if v == "net":
            raise httpx.ConnectError("boom")
        return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"}) if v == "ok" \
            else httpx.Response(v)
    tsettings.tts.model = "other/tts"
    assert _client(tsettings, handler).synthesize("x", "fr") == MP3 and len(n) == 4

    n.clear()
    def bad(req):
        n.append(1); return httpx.Response(401, json={"error": {"message": "no key"}})
    with pytest.raises(TTSError, match="401: no key"):
        _client(tsettings, bad).synthesize("x", "fr")
    assert len(n) == 1

    def down(req):
        n.append(1); return httpx.Response(500)
    n.clear()
    with pytest.raises(TTSError):
        _client(tsettings, down).synthesize("x", "fr")
    assert len(n) == 4


def test_client_rejects_json_body_and_missing_voice(tsettings):
    c = _client(tsettings, lambda r: httpx.Response(200, json={"oops": 1}))
    with pytest.raises(TTSError, match="no audio"):
        c.synthesize("x", "fr")
    with pytest.raises(TTSError, match="not configured"):
        c.synthesize("x", "pl")


def test_health_is_part_of_openrouter_check_only(tsettings):
    from app.status import collect_status

    models = {"data": [{"id": "google/test-tts", "supported_voices": ["Kore"]}]}
    tts = _client(tsettings, lambda r: httpx.Response(200, json=models))
    assert tts.health() == (True, "ok")
    tsettings.tts.languages["fr"].voice = "Nope"
    tts._health_cache = None
    ok, msg = tts.health()
    assert not ok and "fr" in msg

    class G:
        def health(self): return True, "ok"
    st = collect_status(tsettings, {}, G(), None, tts)
    assert set(st["checks"]) == {"anki", "openrouter"}  # no separate TTS pill
    assert not st["checks"]["openrouter"]["ok"]


# ---------- approve / cache ----------
def test_approve_generates_audio_for_fr_only(tsettings, tstore):
    tts = FakeTTS()
    svc = make(tsettings, tstore, tts)
    cs = cards(tstore)
    r = svc.approve([cs["fr"]["id"], cs["pl"]["id"]])
    assert len(r["approved"]) == 2
    assert [c[1] for c in tts.calls] == ["fr"]
    assert tts.calls[0][0] == "On skoczył."  # filled-in answer, no cloze markup
    fr, pl = cards(tstore)["fr"], cards(tstore)["pl"]
    h = text_hash(fr["text"])
    assert fr["audio_file"] == audio_filename(fr["id"], h) and fr["audio_text_hash"] == h
    assert (tsettings.audio_dir / fr["audio_file"]).read_bytes().startswith(MP3)
    assert fr["audio_model"] == "google/test-tts" and fr["audio_voice"] == "Kore" and fr["audio_at"]
    assert pl["audio_file"] is None and pl["audio_error"] is None
    ev = tstore.conn.execute("SELECT event FROM events WHERE entity='card' AND event LIKE 'audio%'").fetchall()
    assert [e[0] for e in ev] == ["audio_generated"]


def test_tts_failure_does_not_block_approval(tsettings, tstore):
    svc = make(tsettings, tstore, FakeTTS(fail="HTTP 500: kaput"))
    fr = cards(tstore)["fr"]
    r = svc.approve([fr["id"]])
    assert r["approved"] == [fr["id"]] and r["audio_errors"]
    c = tstore.get_card(fr["id"])
    assert c["status"] == "approved" and "kaput" in c["audio_error"] and c["audio_file"] is None
    assert tstore.conn.execute("SELECT count(*) FROM events WHERE event='audio_failed'").fetchone()[0] == 1


def test_hash_cache_and_edit_invalidation(tsettings, tstore):
    tts = FakeTTS()
    svc = make(tsettings, tstore, tts)
    fid = cards(tstore)["fr"]["id"]
    svc.approve([fid])
    first = tstore.get_card(fid)["audio_file"]
    svc.edit(fid, translation="Он <b>прыгнул</b> вчера.")  # text unchanged -> reset to generated
    assert tstore.get_card(fid)["status"] == "generated"
    svc.approve([fid])
    assert len(tts.calls) == 1 and tstore.get_card(fid)["audio_file"] == first

    svc.edit(fid, text="On {{c1::skoczył}} hier.")  # text changed
    svc.approve([fid])
    c = tstore.get_card(fid)
    assert len(tts.calls) == 2 and c["audio_file"] != first and tts.calls[1][0] == "On skoczył hier."
    assert not (tsettings.audio_dir / first).exists()  # stale file removed
    assert (tsettings.audio_dir / c["audio_file"]).exists()


def test_missing_file_is_regenerated_on_approve(tsettings, tstore):
    tts = FakeTTS(); svc = make(tsettings, tstore, tts)
    fid = cards(tstore)["fr"]["id"]
    svc.approve([fid])
    (tsettings.audio_dir / tstore.get_card(fid)["audio_file"]).unlink()
    svc.ensure_audio(tstore.get_card(fid))
    assert len(tts.calls) == 2


def test_disabled_language_clears_audio_error(tsettings, tstore):
    svc = make(tsettings, tstore, FakeTTS(fail="x"))
    fid = cards(tstore)["fr"]["id"]
    svc.approve([fid])
    assert tstore.get_card(fid)["audio_error"]
    tsettings.tts.languages["fr"].enabled = False
    svc.ensure_audio(tstore.get_card(fid))
    assert tstore.get_card(fid)["audio_error"] is None


# ---------- regenerate ----------
def test_regenerate_audio_approved(tsettings, tstore):
    tts = FakeTTS(); svc = make(tsettings, tstore, tts)
    fid = cards(tstore)["fr"]["id"]
    svc.approve([fid])
    old = tstore.get_card(fid)["audio_file"]
    r = svc.regenerate_audio(fid)
    assert len(tts.calls) == 2 and r["anki"] is None  # ignores the cache
    assert r["card"]["audio_file"] == old  # same text -> same name, content replaced
    assert (tsettings.audio_dir / old).read_bytes().endswith(b"2")


def test_regenerate_audio_refused_for_wrong_status_or_language(tsettings, tstore):
    svc = make(tsettings, tstore)
    cs = cards(tstore)
    with pytest.raises(ValueError):
        svc.regenerate_audio(cs["fr"]["id"])  # still "generated"
    svc.approve([cs["pl"]["id"]])
    with pytest.raises(ValueError, match="not enabled"):
        svc.regenerate_audio(cs["pl"]["id"])


def test_regenerate_audio_failure_keeps_old_audio(tsettings, tstore):
    tts = FakeTTS(); svc = make(tsettings, tstore, tts)
    fid = cards(tstore)["fr"]["id"]
    svc.approve([fid])
    tts.fail = "down"
    r = svc.regenerate_audio(fid)
    c = tstore.get_card(fid)
    assert "down" in r["card"]["audio_error"] and c["audio_file"] and svc.audio_valid(c)


def test_regenerate_audio_sent_card_updates_anki(tsettings, tstore):
    tts = FakeTTS(); fake = AudioAnki()
    svc = make(tsettings, tstore, tts, anki_for(tsettings, fake))
    fid = cards(tstore)["fr"]["id"]
    svc.approve([fid]); svc.push()
    sent = tstore.get_card(fid)
    assert sent["status"] == "sent" and sent["anki_note_id"] == 123
    fake.calls.clear()

    # the sentence was edited after sending -> new hash -> new file name
    tstore.update_card(fid, text="On {{c1::skoczył}} hier.")
    old = sent["audio_file"]
    r = svc.regenerate_audio(fid)
    new = r["card"]["audio_file"]
    assert new != old and r["anki"] == {"updated": True}
    assert svc.audio_path(new).exists() and not svc.audio_path(old).exists()
    assert fake.names() == ["storeMediaFile", "updateNoteFields", "deleteMediaFile"]
    store_p, upd, dele = (p for _, p in fake.calls)
    assert store_p["filename"] == new and base64.b64decode(store_p["data"]).startswith(MP3)
    assert upd == {"note": {"id": 123, "fields": {"Audio": f"[sound:{new}]"}}}
    assert dele == {"filename": old}


def test_regenerate_audio_sent_card_anki_down_reports_error(tsettings, tstore):
    fake = AudioAnki()
    svc = make(tsettings, tstore, FakeTTS(), anki_for(tsettings, fake))
    fid = cards(tstore)["fr"]["id"]
    svc.approve([fid]); svc.push()
    svc.anki.client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    r = svc.regenerate_audio(fid)
    assert r["anki"]["updated"] is False and r["anki"]["error"] and r["card"]["audio_file"]


# ---------- push ----------
def test_push_sets_audio_field_and_stores_media_first(tsettings, tstore):
    fake = AudioAnki()
    svc = make(tsettings, tstore, FakeTTS(), anki_for(tsettings, fake))
    cs = cards(tstore)
    svc.approve([cs["fr"]["id"], cs["pl"]["id"]])
    assert svc.push() == {"sent": 2, "failed": 0}
    names = fake.names()
    assert names.count("storeMediaFile") == 1 and names.count("addNote") == 2
    assert names.index("storeMediaFile") < names.index("addNote")
    fname = tstore.get_card(cs["fr"]["id"])["audio_file"]
    media = next(p for a, p in fake.calls if a == "storeMediaFile")
    assert media["filename"] == fname and base64.b64decode(media["data"]).startswith(MP3)
    by_deck = {n["deckName"]: n["fields"] for n in fake.added}
    assert by_deck["French"]["Audio"] == f"[sound:{fname}]"
    assert by_deck["Polish"]["Audio"] == ""
    assert "[sound:" not in by_deck["French"]["Text"]  # never on the front


def test_push_generates_missing_audio_first(tsettings, tstore):
    tts = FakeTTS(); fake = AudioAnki()
    svc = make(tsettings, tstore, tts, anki_for(tsettings, fake))
    fid = cards(tstore)["fr"]["id"]
    tsettings.tts.languages["fr"].enabled = False  # approve while audio is off ...
    svc.approve([fid])
    assert not tts.calls
    tsettings.tts.languages["fr"].enabled = True  # ... then turn it on before pushing
    assert svc.push()["sent"] == 1 and len(tts.calls) == 1
    assert fake.added[0]["fields"]["Audio"].startswith("[sound:pinkcards_")


def test_push_refuses_cards_whose_audio_is_missing(tsettings, tstore):
    tts = FakeTTS(fail="quota"); fake = AudioAnki()
    svc = make(tsettings, tstore, tts, anki_for(tsettings, fake))
    cs = cards(tstore)
    svc.approve([cs["fr"]["id"], cs["pl"]["id"]])
    assert svc.push() == {"sent": 1, "failed": 1}  # pl still goes through
    c = tstore.get_card(cs["fr"]["id"])
    assert c["status"] == "failed" and c["error"].startswith("audio missing: ") and "quota" in c["error"]
    assert [n["deckName"] for n in fake.added] == ["Polish"]

    # the user fixes it with "Regenerate audio" (allowed for failed cards with content) and pushes again
    tts.fail = None
    svc.regenerate_audio(cs["fr"]["id"])
    assert svc.push() == {"sent": 1, "failed": 0}


# ---------- AnkiConnect startup check ----------
def test_check_reports_missing_audio_field(tsettings):
    old = FakeAnki(fields=["Text", "Expression", "Translation", "Picture", "HINT", "NOTE"])
    p = anki_for(tsettings, old).check()
    assert len(p) == 1
    assert p[0] == "add a field named Audio to note type 'Cloze' and put {{Audio}} on the back template -- see README"
    assert anki_for(tsettings, FakeAnki()).check() == []
    assert AnkiConnect.__mro__  # sanity
    assert tsettings.anki.expected_fields[-1] == "Audio" and Settings().anki.expected_fields[-1] == "Audio"


# ---------- apkg ----------
def test_apkg_has_media_and_seven_fields(tsettings, tstore, monkeypatch):
    import genanki

    captured = {}
    orig = genanki.Package.write_to_file
    def spy(self, path, *a, **k):
        captured["media"] = list(self.media_files); captured["decks"] = self.decks
        return orig(self, path, *a, **k)
    monkeypatch.setattr(genanki.Package, "write_to_file", spy)

    svc = make(tsettings, tstore, FakeTTS())
    cs = cards(tstore)
    svc.approve([cs["fr"]["id"], cs["pl"]["id"]])
    sink = ApkgSink(tsettings, {"Cloze": {"Front": "{{cloze:Text}}", "Back": "{{cloze:Text}}{{Audio}}"}}, "")
    out = sink.push([tstore.get_card(cs["fr"]["id"]), tstore.get_card(cs["pl"]["id"])])
    assert all(o.ok for o in out)
    fr = tstore.get_card(cs["fr"]["id"])
    assert captured["media"] == [str(tsettings.audio_dir / fr["audio_file"])]
    notes = [n for d in captured["decks"] for n in d.notes]
    assert all(len(n.fields) == 7 for n in notes)
    assert {n.fields[6] for n in notes} == {f"[sound:{fr['audio_file']}]", ""}
    assert sink.last_path.exists()
    import zipfile
    names = zipfile.ZipFile(sink.last_path).namelist()
    assert "media" in names and "0" in names


# ---------- migration ----------
def test_migration_002_keeps_existing_data(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript((MIGRATIONS / "001_init.sql").read_text("utf-8"))
    conn.execute("PRAGMA user_version=1")
    conn.execute("INSERT INTO source_notes(id,source,source_id,text,fetched_at) VALUES('s:1','s','1','t','now')")
    conn.execute("INSERT INTO cards(source_note_id,language,text,expression,status,created_at,updated_at) "
                 "VALUES('s:1','fr','Il {{c1::mange}}','manger','approved','now','now')")
    conn.commit(); conn.close()

    c = connect(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] >= 2
    row = dict(c.execute("SELECT * FROM cards").fetchone())
    assert row["text"] == "Il {{c1::mange}}" and row["status"] == "approved" and row["expression"] == "manger"
    for col in ("audio_file", "audio_text_hash", "audio_model", "audio_voice", "audio_error", "audio_at"):
        assert row[col] is None
    assert c.execute("SELECT count(*) FROM source_notes").fetchone()[0] == 1
    c.close()
    connect(path).close()  # idempotent


# ---------- exported (.apkg) cards: note looked up in Anki ----------
class LookupAnki(AudioAnki):
    """AudioAnki with a searchable collection: {note_id: Text}."""
    def __init__(self, notes, **kw):
        super().__init__(**kw); self.notes = notes
    def handler(self, req):
        b = json.loads(req.content); a, p = b["action"], b["params"]
        if a in ("findNotes", "notesInfo"):
            self.calls.append((a, p))
            res = list(self.notes) if a == "findNotes" else \
                [{"noteId": i, "fields": {"Text": {"value": self.notes[i]}}} for i in p["notes"]]
            return httpx.Response(200, json={"result": res, "error": None})
        return super().handler(req)


def _exported(tsettings, tstore, notes):
    fake = LookupAnki(notes)
    svc = make(tsettings, tstore, FakeTTS(), anki_for(tsettings, fake))
    fid = cards(tstore)["fr"]["id"]
    svc.approve([fid])
    tstore.update_card(fid, status="exported")  # as after "Export .apkg": no anki_note_id
    return svc, fake, fid


def test_regenerate_audio_exported_finds_note_and_updates_it(tsettings, tstore):
    # Text in Anki has different markup/whitespace and there is an unrelated note too -> only exact text matches
    svc, fake, fid = _exported(tsettings, tstore, {77: "On  <b>{{c1::skoczył}}</b>.", 78: "something else"})
    assert tstore.get_card(fid)["text"] == "On {{c1::skoczył}}."
    fake.calls.clear()  # Sync's duplicate check also calls findNotes
    r = svc.regenerate_audio(fid)
    assert r["anki"] == {"updated": True}
    assert fake.names() == ["findNotes", "notesInfo", "storeMediaFile", "updateNoteFields"]
    q = fake.calls[0][1]["query"]
    assert q == 'deck:"French" "Expression:skoczyć"'
    c = tstore.get_card(fid)
    assert c["anki_note_id"] == 77 and c["status"] == "exported"  # remembered, status untouched
    upd = fake.calls[-1][1]
    assert upd["note"]["id"] == 77 and upd["note"]["fields"]["Audio"] == f"[sound:{c['audio_file']}]"
    # second regenerate: the id is known, no lookup
    fake.calls.clear()
    svc.regenerate_audio(fid)
    assert "findNotes" not in fake.names() and "updateNoteFields" in fake.names()


def test_exported_lookup_no_match_or_ambiguous_does_not_touch_anki(tsettings, tstore):
    svc, fake, fid = _exported(tsettings, tstore, {})
    fake.calls.clear()
    r = svc.regenerate_audio(fid)
    assert r["anki"]["updated"] is False and "no matching note" in r["anki"]["reason"]
    assert r["card"]["audio_file"] and tstore.get_card(fid)["anki_note_id"] is None  # audio still saved locally
    assert "storeMediaFile" not in fake.names() and "updateNoteFields" not in fake.names()

    fake.notes = {1: "On {{c1::skoczył}}.", 2: "On {{c1::skoczył}}."}
    r = svc.regenerate_audio(fid)
    assert r["anki"]["updated"] is False and "2 Anki notes" in r["anki"]["reason"]
    assert tstore.get_card(fid)["anki_note_id"] is None
    assert "updateNoteFields" not in fake.names()


def test_regenerate_notes_exported_uses_same_lookup(tsettings, tstore):
    from app.models import Details

    svc, fake, fid = _exported(tsettings, tstore, {5: "On {{c1::skoczył}}."})
    tsettings.details.languages = {"fr": __import__("app.config", fromlist=["x"]).DetailsLangCfg(enabled=True)}
    svc.gen.generate_details = lambda card: Details.model_validate(
        {"contractions": [{"form": "qu'il", "parts": "que + il"}], "words": [], "construction": None, "etymology": None})
    r = svc.regenerate_details(fid)
    assert r["anki"] == {"updated": True} and r["card"]["status"] == "exported"
    note_upd = [p for a, p in fake.calls if a == "updateNoteFields"][-1]
    assert note_upd["note"]["id"] == 5 and "qu'il = que + il" in note_upd["note"]["fields"]["NOTE"]


# ---------- "note is open in Anki's editor" + resend ----------
class EditorOpenAnki(LookupAnki):
    open_editor = True
    def handler(self, req):
        b = json.loads(req.content)
        if b["action"] == "updateNoteFields" and self.open_editor:
            self.calls.append((b["action"], b["params"]))
            return httpx.Response(200, json={"result": None, "error": "Close the affected editor before this API write, even if its content is saved."})
        return super().handler(req)


def test_editor_open_error_is_actionable_and_resend_recovers(tsettings, tstore):
    fake = EditorOpenAnki({9: "On {{c1::skoczył}}."})
    svc = make(tsettings, tstore, FakeTTS(), anki_for(tsettings, fake))
    fid = cards(tstore)["fr"]["id"]
    svc.approve([fid]); tstore.update_card(fid, status="exported")
    r = svc.regenerate_audio(fid)
    tts_calls = len(svc.tts.calls)  # approve + regenerate
    assert r["anki"]["updated"] is False and "open in Anki's editor" in r["anki"]["error"]
    assert r["card"]["audio_file"] and r["card"]["audio_error"] is None  # local audio is fine

    fake.open_editor = False; fake.calls.clear()
    out = svc.resend_to_anki(fid)  # no TTS call, just NOTE + Audio
    assert out["anki"] == {"updated": True, "audio": True}
    assert fake.names() == ["storeMediaFile", "updateNoteFields"]
    f = fake.calls[-1][1]["note"]["fields"]
    assert set(f) == {"NOTE", "Audio"} and f["Audio"].startswith("[sound:pinkcards_")
    assert len(svc.tts.calls) == tts_calls  # resend makes no TTS call


def test_resend_refused_for_cards_not_in_anki(tsettings, tstore):
    svc = make(tsettings, tstore)
    with pytest.raises(ValueError):
        svc.resend_to_anki(cards(tstore)["fr"]["id"])
