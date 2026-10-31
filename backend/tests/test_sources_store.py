import pytest
import sqlite3

import httpx

from app.models import SourceNote
from app.sources.gsheets import rows_to_notes
from app.sources.readwise import ReadwiseSource
from app.sources.zotero import ZoteroSource
from app.store.backup import make_backup, restore_backup


def test_sheets_identity_stable():
    a = rows_to_notes([["Expression", "Comment"], ["Skakać", "x"], [], ["dom"]], "S", "pl")
    b = rows_to_notes([["Expression", "Comment"], ["dom", "new"], ["  skakać ", "other"], ["", "z"]], "S", "pl")
    assert {n.source_id for n in a} == {n.source_id for n in b} == {"S:skakać", "S:dom"}
    assert all(n.language_hint == "pl" for n in a)


def test_upsert_idempotent_and_status_preserved(store):
    n = SourceNote(source="gsheets", source_id="S:dom", text="dom", language_hint="pl")
    assert store.upsert_source_notes([n]) == 1
    store.set_note_status(n.id, "rejected")
    n2 = n.model_copy(update={"instruction": "changed comment"})
    assert store.upsert_source_notes([n2]) == 0
    row = store.get_source_note(n.id)
    assert row["status"] == "rejected" and row["instruction"] == "changed comment"
    assert store.conn.execute("SELECT count(*) FROM source_notes").fetchone()[0] == 1


def _rw_client(pages):
    def handler(req):
        cur = req.url.params.get("pageCursor")
        return httpx.Response(200, json=pages[cur])
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_readwise_pink_filter_and_pagination(settings):
    h = lambda i, tags=(), color="": {"id": i, "text": f"w{i}", "tags": [{"name": t} for t in tags],
                                       "color": color, "updated": "2024-01-01T00:00:00Z"}
    pages = {None: {"results": [{"title": "B", "author": "A", "highlights": [h(1, ["pink"]), h(2)]}],
                    "nextPageCursor": "c2"},
             "c2": {"results": [{"title": "B2", "author": "A2", "highlights": [h(3, color="pink"), h(4, ["blue"])]}],
                    "nextPageCursor": None}}
    settings.readwise_token = "t"
    res = ReadwiseSource(settings, _rw_client(pages)).fetch(None)
    assert [n.source_id for n in res.notes] == ["1", "3"]
    assert res.notes[0].book_title == "B" and res.cursor


def test_zotero_filter_and_cursor(settings):
    ann = lambda k, color, mod: {"key": k, "data": {"key": k, "itemType": "annotation", "annotationText": f"t{k}",
                                                    "annotationColor": color, "dateModified": mod, "parentItem": "ATT"}}
    items = [ann("A", "#e56eee", "2024-03-02T00:00:00Z"), ann("B", "#ffd400", "2024-03-01T00:00:00Z")]

    def handler(req):
        p = req.url.path
        if p.endswith("/items/ATT"):
            return httpx.Response(200, json={"data": {"parentItem": "BOOK"}})
        if p.endswith("/items/BOOK"):
            return httpx.Response(200, json={"data": {"title": "Book", "language": "pl",
                                                      "creators": [{"firstName": "Jan", "lastName": "Kowalski"}]}})
        return httpx.Response(200, json=items if req.url.params.get("start") == "0" else [])

    src = ZoteroSource(settings, httpx.Client(transport=httpx.MockTransport(handler)))
    res = src.fetch(None)
    assert [n.source_id for n in res.notes] == ["A"]
    assert res.notes[0].book_title == "Book" and res.notes[0].author == "Jan Kowalski"
    assert res.cursor == "2024-03-02T00:00:00Z"
    assert src.fetch(res.cursor).notes == []  # nothing newer


def test_backup_restore_roundtrip_and_rotation(settings, store):
    store.upsert_source_notes([SourceNote(source="gsheets", source_id="S:a", text="a")])
    files = [make_backup(settings.db_path, settings.backup_dir, 3) for _ in range(5)]
    assert len(list(settings.backup_dir.glob("*.db"))) == 3
    store.conn.execute("DELETE FROM source_notes"); store.conn.commit(); store.conn.close()
    restore_backup(settings.db_path, settings.backup_dir, files[-1], 10)
    c = sqlite3.connect(settings.db_path)
    assert c.execute("SELECT count(*) FROM source_notes").fetchone()[0] == 1
    assert any("pre-restore" in f.name for f in settings.backup_dir.glob("*.db"))


# ---- Google Sheets via service account ----
import json as _json

from googleapiclient.errors import HttpError

from app.sources.gsheets import GsheetsSource, SheetsAccessError


class _Resp(dict):
    def __init__(self, status): super().__init__(); self.status = status; self.reason = "x"


class _FakeSheets:
    def __init__(self, by_id): self.by_id, self.calls = by_id, []
    def spreadsheets(self): return self
    def values(self): return self
    def get(self, spreadsheetId, range):
        self.calls.append((spreadsheetId, range)); v = self.by_id[spreadsheetId]; fs = self
        class Ex:
            def execute(_):
                if isinstance(v, int):
                    raise HttpError(_Resp(v), b"{}")
                return {"values": v}
        return Ex()


def _key(settings, email="bot@proj.iam.gserviceaccount.com"):
    p = settings.path(settings.gsheets.service_account_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps({"client_email": email}))


def test_gsheets_health_missing_key(settings):
    ok, msg = GsheetsSource(settings, lambda: _FakeSheets({})).health()
    assert not ok and "not found" in msg


def test_gsheets_health_ok_and_fetch(settings):
    _key(settings)
    fake = _FakeSheets({"SHEETPL": [["Expression", "Comment"], ["dom", "x"]]})
    src = GsheetsSource(settings, lambda: fake)
    assert src.health() == (True, "ok")
    assert [n.source_id for n in src.fetch().notes] == ["SHEETPL:dom"]


@pytest.mark.parametrize("code", [403, 404])
def test_gsheets_health_share_hint(settings, code):
    _key(settings)
    ok, msg = GsheetsSource(settings, lambda: _FakeSheets({"SHEETPL": code})).health()
    assert not ok and "share this sheet with bot@proj.iam.gserviceaccount.com as Viewer" in msg


def test_gsheets_fetch_error_is_readable(settings):
    _key(settings)
    with pytest.raises(SheetsAccessError, match="as Viewer"):
        GsheetsSource(settings, lambda: _FakeSheets({"SHEETPL": 403})).fetch()
