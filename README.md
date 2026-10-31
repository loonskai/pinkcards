# pinkcards — Kindle/Zotero/Sheets → Anki cloze cards

Local pipeline: pink highlights (Readwise/Kindle, Zotero) and Google Sheets word lists → LLM-generated cloze cards → human review → AnkiConnect (or `.apkg`).
The CLI is called `anki-cloze`. Full design: see the spec in the original handoff.

## Setup
```
npm run install:all          # root deps, ui deps, uv sync
cp .env.example .env         # OPENROUTER_API_KEY, READWISE_TOKEN
npm run dev                  # backend :8000 + UI :5173
npm test
```
Needs Zotero 7+ (Settings → Advanced → allow other applications) and Anki + AnkiConnect running for Sync/Push.

## First-run checklist (the ⚠ VERIFY items)
1. `cd backend && uv run anki-cloze inspect readwise --limit 5` → check how pink arrives (tag vs `color`) and the pagination field (`nextPageCursor`). Set `readwise.pink_filter` (`tag:pink`, `color:pink`, or `tag:pink|color:pink`).
2. `uv run anki-cloze inspect zotero` → read `annotationColor` of a pink annotation; set `zotero.pink_color`. Zotero sync is refused while it's empty.
3. Google Sheets (service account — no OAuth, no token expiry): create a Cloud project, enable the Sheets API, create a service account, download its JSON key to `secrets/google_service_account.json` (git-ignored), then share each spreadsheet with the key's `client_email` as **Viewer** and fill `[gsheets.sheets]`. The status bar reports "share this sheet with <client_email> as Viewer" on a 403/404. The old `secrets/google_client.json` is no longer used and can be deleted.
4. Pin `generator.model` to the exact OpenRouter ID/price from openrouter.ai/models (the value in `config.toml` is a placeholder).
5. Anki: confirm decks `French`/`English` exist, and that note type "Cloze" has fields `Text, Expression, Translation, Picture, HINT, NOTE`. If it clashes with the built-in, rename yours (e.g. "Cloze RU") and update `anki.note_type`. Push is refused until checks pass.
6. `.apkg` import into the existing note type (genanki model id `apkg.model_id`) must be tested on a copy of the collection first.
7. Save sanitized `inspect` output under `backend/tests/fixtures/`.

## Backups
Automatic (SQLite online backup API) before every Sync/Push/export, last 20 kept in `store.backup_dir` (point it at a synced folder).
`uv run anki-cloze backup` / `uv run anki-cloze restore <file>` (restore backs up the current DB first). Run these from `backend/`.

## Extending
Sources (`sources/base.py`), generator (`generator/base.py`) and sinks (`sinks/base.py`) are separate interfaces; the NOTE wording lives only in `generator/note.py`.
