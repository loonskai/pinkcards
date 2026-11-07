from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Source(StrEnum):
    READWISE = "readwise"
    ZOTERO = "zotero"
    GSHEETS = "gsheets"


# Label used on cards / tags
SOURCE_KIND = {"readwise": "kindle", "zotero": "zotero", "gsheets": "sheets"}


class NoteStatus(StrEnum):
    PENDING = "pending"
    GENERATED = "generated"
    UNSUPPORTED = "unsupported"
    REJECTED = "rejected"
    DONE = "done"


class CardStatus(StrEnum):
    GENERATED = "generated"
    APPROVED = "approved"
    EXPORTED = "exported"
    SENT = "sent"
    FAILED = "failed"
    REJECTED = "rejected"


class CardKind(StrEnum):
    INITIAL = "initial"
    ANOTHER_MEANING = "another_meaning"
    NEW_FORM = "new_form"


class SourceNote(BaseModel):
    source: str
    source_id: str
    text: str
    instruction: str | None = None
    language_hint: str | None = None
    book_title: str | None = None
    author: str | None = None
    source_updated_at: str | None = None
    raw: dict = Field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.source}:{self.source_id}"


class Grammar(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gender: Literal["m", "f", "n"] | None = None
    article: Literal["le", "la", "l'"] | None = None
    aspect_pair: str | None = None


class Contraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    form: str
    parts: str


class DetailWord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    form: str
    base: str = ""
    note_ru: str = ""


class Construction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explanation_ru: str


class Etymology(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text_ru: str
    confidence: Literal["high", "medium", "low"]


class Details(BaseModel):
    """Per-language learning notes about the sentence (rendered into NOTE by generator/note.py)."""

    model_config = ConfigDict(extra="forbid")
    contractions: list[Contraction] = Field(default_factory=list)
    words: list[DetailWord] = Field(default_factory=list)
    construction: Construction | None = None
    etymology: Etymology | None = None

    def is_empty(self) -> bool:
        return not (self.contractions or self.words or self.construction or self.etymology)


class GeneratedCard(BaseModel):
    """Schema the LLM must return (see generator/schema.py)."""

    model_config = ConfigDict(extra="forbid")
    language: str
    language_confidence: Literal["high", "medium", "low"]
    language_reason: str
    expression: str
    form_used: str
    is_base_form: bool
    grammar: Grammar
    text: str
    translation: str
    hint: str = ""
    meaning_gloss_ru: str
    details: Details | None = None
