import pytest

from app.config import Settings
from app.store.db import Store, connect


@pytest.fixture
def settings(tmp_path):
    return Settings(
        root=tmp_path,
        store={"db_path": str(tmp_path / "t.db"), "backup_dir": str(tmp_path / "bk"), "backup_keep": 3},
        readwise={"pink_filter": "tag:pink|color:pink"},
        zotero={"pink_color": "#e56eee"},
        gsheets={"sheets": {"pl": "SHEETPL"}},
        generator={"model": "test/model", "prompt_version": "v1"},
        anki={"decks": {"pl": "Polish", "fr": "French", "en": "English"}},
    )


@pytest.fixture
def store(settings):
    return Store(connect(settings.db_path))
