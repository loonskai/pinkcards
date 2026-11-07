from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..models import Details, GeneratedCard


@dataclass
class GenerationRequest:
    text: str
    source: str
    book_title: str | None = None
    author: str | None = None
    zotero_language: str | None = None
    language_hint: str | None = None   # fixed for Sheets
    instruction: str | None = None
    excluded_meanings: list[str] = field(default_factory=list)
    excluded_forms: list[str] = field(default_factory=list)
    regen_instruction: str | None = None
    force_language: str | None = None  # "change language"
    lemma: str | None = None           # for new_form / another_meaning children
    details_languages: list[str] | None = None  # languages with `details` enabled (None = feature off)


class GenerationError(Exception):
    pass


class Generator(ABC):
    model: str
    prompt_version: str

    @abstractmethod
    def generate(self, req: GenerationRequest) -> GeneratedCard:
        """Return a schema-valid card or raise GenerationError."""

    def generate_details(self, card: dict) -> Details | None:
        """Notes for an existing card (never changes the sentence). Raise GenerationError on failure."""
        raise NotImplementedError

    @abstractmethod
    def health(self) -> tuple[bool, str]: ...
