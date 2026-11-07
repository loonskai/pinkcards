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
    prompt_version: str = "v2"


class AnkiCfg(BaseModel):
    connect_url: str = "http://localhost:8765"
    note_type: str = "Cloze"
    expected_fields: list[str] = ["Text", "Expression", "Translation", "Picture", "HINT", "NOTE", "Audio"]
    decks: dict[str, str] = Field(default_factory=dict)


class ApkgCfg(BaseModel):
    model_id: int = 1660407719467
    export_dir: str = "./exports"


class TtsLangCfg(BaseModel):
    enabled: bool = False
    voice: str = ""


class TtsCfg(BaseModel):
    model: str = ""                 # exact OpenRouter speech model id
    format: str = "mp3"             # stored/served format (only mp3 is supported)
    audio_dir: str = "./data/audio"
    languages: dict[str, TtsLangCfg] = Field(default_factory=dict)


class DetailsLangCfg(BaseModel):
    enabled: bool = False


class DetailsCfg(BaseModel):
    languages: dict[str, DetailsLangCfg] = Field(default_factory=dict)


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
    tts: TtsCfg = TtsCfg()
    details: DetailsCfg = DetailsCfg()
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
    def audio_dir(self) -> Path:
        return self.path(self.tts.audio_dir)

    def tts_enabled(self, lang: str | None) -> bool:
        """Audio is generated only if the language is enabled AND a model and a voice are configured."""
        cfg = self.tts.languages.get(lang or "")
        return bool(cfg and cfg.enabled and self.tts.model and cfg.voice)

    def details_enabled(self, lang: str | None) -> bool:
        cfg = self.details.languages.get(lang or "")
        return bool(cfg and cfg.enabled)

    @property
    def details_languages(self) -> list[str]:
        return sorted(l for l in self.details.languages if self.details_enabled(l))

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
