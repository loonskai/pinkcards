from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..models import SourceNote


@dataclass
class FetchResult:
    notes: list[SourceNote] = field(default_factory=list)
    cursor: str | None = None  # new updated_after cursor (None = no cursor concept)


class SourceAdapter(ABC):
    name: str

    @abstractmethod
    def fetch(self, updated_after: str | None) -> FetchResult: ...

    @abstractmethod
    def inspect(self, limit: int = 10) -> list[dict]:
        """Raw, unfiltered JSON for a few recent items."""

    @abstractmethod
    def health(self) -> tuple[bool, str]: ...
