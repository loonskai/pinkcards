from __future__ import annotations

import json
from typing import Callable

from ..config import Settings
from ..dedup import normalize
from ..models import SourceNote
from .base import FetchResult, SourceAdapter

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


class SheetsAccessError(RuntimeError):
    """Human-readable problem reading a sheet / the key file."""


# Row 1 is skipped only if column A looks like a header, so a word typed into A1 isn't silently dropped.
HEADER_NAMES = {"expression", "expressions", "word", "words", "phrase", "słowo", "mot", "term", "vocabulary"}


def _is_header(row: list[str]) -> bool:
    return bool(row) and row[0].strip().lower() in HEADER_NAMES


def rows_to_notes(rows: list[list[str]], spreadsheet_id: str, language: str) -> list[SourceNote]:
    notes: dict[str, SourceNote] = {}
    for i, row in enumerate(rows):
        if i == 0 and _is_header(row):
            continue
        expr = (row[0] if row else "").strip()
        if not expr:
            continue
        comment = (row[1].strip() if len(row) > 1 else "") or None
        sid = f"{spreadsheet_id}:{normalize(expr)}"
        if sid in notes:  # duplicate row for same word -> first wins
            continue
        notes[sid] = SourceNote(
            source="gsheets", source_id=sid, text=expr, instruction=comment,
            language_hint=language, raw={"row": row, "spreadsheet_id": spreadsheet_id},
        )
    return list(notes.values())


class GsheetsSource(SourceAdapter):
    name = "gsheets"

    def __init__(self, settings: Settings, service_factory: Callable[[], object] | None = None):
        """service_factory: returns a Sheets API resource (injectable for tests)."""
        self.s = settings
        self._factory = service_factory
        self._service = None

    # ---- credentials ----
    @property
    def key_path(self):
        return self.s.path(self.s.gsheets.service_account_file)

    def client_email(self) -> str:
        try:
            return json.loads(self.key_path.read_text("utf-8")).get("client_email", "<service account email>")
        except (OSError, ValueError):
            return "<service account email>"

    def _build(self):
        if self._factory:
            return self._factory()
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        if not self.key_path.exists():
            raise SheetsAccessError(f"service account key not found: {self.key_path}")
        try:
            creds = service_account.Credentials.from_service_account_file(str(self.key_path), scopes=SCOPES)
        except (ValueError, KeyError) as e:
            raise SheetsAccessError(f"invalid service account key file: {e}") from e
        return build("sheets", "v4", credentials=creds)

    @property
    def service(self):
        if self._service is None:
            self._service = self._build()
        return self._service

    # ---- reading ----
    def _range(self, rng: str | None = None) -> str:
        rng = rng or self.s.gsheets.range
        name = self.s.gsheets.sheet_name
        return f"'{name}'!{rng}" if name else rng

    def _values(self, spreadsheet_id: str, rng: str | None = None) -> list[list[str]]:
        from googleapiclient.errors import HttpError

        try:
            res = self.service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id, range=self._range(rng)).execute()
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            if status in (403, 404):
                raise SheetsAccessError(
                    f"share this sheet with {self.client_email()} as Viewer "
                    f"(HTTP {status} for spreadsheet {spreadsheet_id})") from e
            raise SheetsAccessError(f"Sheets API error {status} for {spreadsheet_id}: {e}") from e
        return res.get("values", [])

    def health(self) -> tuple[bool, str]:
        if not self.key_path.exists():
            return False, f"service account key not found: {self.key_path}"
        if not self.s.gsheets.sheets:
            return False, (f"key OK, but no spreadsheets configured: add ids under [gsheets.sheets] in config.toml "
                           f"and share each sheet with {self.client_email()} as Viewer")
        problems = []
        for lang, sid in self.s.gsheets.sheets.items():
            try:
                self._values(sid, "A1:B1")
            except SheetsAccessError as e:
                problems.append(f"{lang}: {e}")
            except Exception as e:  # noqa: BLE001  (network etc.)
                problems.append(f"{lang}: {e}")
        return (not problems, "; ".join(problems) if problems else "ok")

    def inspect(self, limit: int = 10) -> list[dict]:
        out = []
        for lang, sid in self.s.gsheets.sheets.items():
            out.append({"language": lang, "spreadsheet_id": sid, "rows": self._values(sid)[: limit + 1]})
        return out

    def fetch(self, updated_after: str | None = None) -> FetchResult:
        notes: list[SourceNote] = []
        for lang, sid in self.s.gsheets.sheets.items():
            notes += rows_to_notes(self._values(sid), sid, lang)
        return FetchResult(notes=notes, cursor=None)
