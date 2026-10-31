"""Loads config.toml (settings) + .env (secrets)."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]


class StoreCfg(BaseModel):
    db_path: str = "./data/anki-cloze.db"
    backup_dir: str = "./backups"
    backup_keep: int = 20


class SyncCfg(BaseModel):
    max_cards_per_sync: int = 50


class ReadwiseCfg(BaseModel):
    pink_filter: str = ""


class ZoteroCfg(BaseModel):
    base_url: str = "http://localhost:23119/api/users/0"
    pink_color: str = ""


class GsheetsCfg(BaseModel):
    service_account_file: str = "./secrets/google_service_account.json"
    range: str = "A:B"
    sheet_name: str = ""
    sheets: dict[str, str] = Field(default_factory=dict)


class GeneratorCfg(BaseModel):
    provider: str = "openrouter"
    model: str = ""
    prompt_version: str = "v1"


class AnkiCfg(BaseModel):
    connect_url: str = "http://localhost:8765"
    note_type: str = "Cloze"
    expected_fields: list[str] = ["Text", "Expression", "Translation", "Picture", "HINT", "NOTE"]
    decks: dict[str, str] = Field(default_factory=dict)


class ApkgCfg(BaseModel):
    model_id: int = 1660407719467
    export_dir: str = "./exports"


class LangCfg(BaseModel):
    native: str = "ru"


class Settings(BaseModel):
    root: Path = ROOT
    store: StoreCfg = StoreCfg()
    sync: SyncCfg = SyncCfg()
    readwise: ReadwiseCfg = ReadwiseCfg()
    zotero: ZoteroCfg = ZoteroCfg()
    gsheets: GsheetsCfg = GsheetsCfg()
    generator: GeneratorCfg = GeneratorCfg()
    anki: AnkiCfg = AnkiCfg()
    apkg: ApkgCfg = ApkgCfg()
    lang: LangCfg = LangCfg()
    openrouter_api_key: str = ""
    readwise_token: str = ""

    def path(self, p: str) -> Path:
        pp = Path(p).expanduser()
        return pp if pp.is_absolute() else (self.root / pp).resolve()

    @property
    def db_path(self) -> Path:
        return self.path(self.store.db_path)

    @property
    def backup_dir(self) -> Path:
        return self.path(self.store.backup_dir)

    @property
    def supported_languages(self) -> set[str]:
        return set(self.anki.decks)


def load_settings(config_path: str | Path | None = None) -> Settings:
    cfg_path = Path(config_path or os.environ.get("ANKI_CLOZE_CONFIG") or ROOT / "config.toml")
    root = cfg_path.resolve().parent
    load_dotenv(root / ".env")
    data = tomllib.loads(cfg_path.read_text("utf-8")) if cfg_path.exists() else {}
    return Settings(
        root=root,
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        readwise_token=os.environ.get("READWISE_TOKEN", ""),
        **data,
    )
