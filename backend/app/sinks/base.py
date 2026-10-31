from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PushResult:
    card_id: int
    ok: bool
    anki_note_id: int | None = None
    error: str | None = None


class Sink(ABC):
    @abstractmethod
    def health(self) -> tuple[bool, str]: ...

    @abstractmethod
    def push(self, cards: list[dict]) -> list[PushResult]: ...
