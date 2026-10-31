# pinkcards — Kindle/Zotero/Sheets → Anki cloze cards

A local pipeline. Pink highlights from Kindle (via Readwise) and Zotero, plus Google Sheets word lists, go to an LLM that writes cloze cards. You review each card in a web UI, then push it to Anki through AnkiConnect, or export an `.apkg`.

Everything runs on your machine. Nothing is ever written back to any source.

```
Readwise / Zotero / Google Sheets → SQLite store → generator (OpenRouter) → review UI → AnkiConnect / .apkg
```

## Requirements

| Tool | Needed for |
|---|---|
| [uv](https://docs.astral.sh/uv/) | Python backend (Python ≥ 3.11) |
| Node.js ≥ 18 + npm | UI and the `npm run dev` launcher |
| Zotero 7+ (desktop, running) | Zotero source |
| Anki (desktop, running) + [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on | Push to Anki |

## Quick start

```bash
npm run install:all        # root deps + ui deps + `uv sync`
cp .env.example .env       # then fill in the two keys below
npm run dev                # backend on :8000, UI on http://localhost:5173
npm test                   # backend tests
```

`.env` (git-ignored):

```
OPENROUTER_API_KEY=...     # https://openrouter.ai/keys
READWISE_TOKEN=...         # https://readwise.io/access_token
```

All other settings live in `config.toml`. **`.env` and `config.toml` are read only at startup, so restart `npm run dev` after editing them.**

Open the UI and look at the status bar at the top. Each service (Readwise, Zotero, Google, AnkiConnect, OpenRouter) shows ok or down, and hovering a pill shows the reason. Sync needs OpenRouter plus at least one source, and Push needs AnkiConnect. The sections below walk through making each one green.

## Setup, step by step

### 1. OpenRouter

- Put your key in `.env`.
- `generator.model` in `config.toml` must be an exact model ID that supports **structured outputs**. The default is `anthropic/claude-opus-4.8`. Some models, such as `anthropic/claude-opus-4.1`, don't support it, and OpenRouter answers `404 No endpoints found`. The model list at openrouter.ai/models shows supported parameters and current prices.

### 2. Readwise (Kindle)

```bash
cd backend && uv run anki-cloze inspect readwise --limit 5
```

Look at how a pink highlight arrives, either as a `tags` entry named "pink" or as a `color` field, and set the filter to match:

```toml
[readwise]
pink_filter = "tag:pink"            # or "color:pink", or "tag:pink|color:pink"
```

The export pagination field is assumed to be `nextPageCursor`. If `inspect` shows something different, tell me or change `sources/readwise.py`.

### 3. Zotero

1. In Zotero: Settings → Advanced → tick "Allow other applications on this computer to communicate with Zotero". Keep Zotero running.
2. Run `uv run anki-cloze inspect zotero` and read `annotationColor` on one of your pink annotations.
3. Put that value in the config. Zotero sync is refused while it's empty.

```toml
[zotero]
pink_color = "#e56eee"              # use YOUR value from inspect
```

### 4. Google Sheets (service account)

A service account has no consent screen and no token expiry. It is a robot user that you share your sheets with.

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project and enable the **Google Sheets API**.
2. IAM & Admin → Service Accounts → create one → Keys → Add key → JSON. Save the file as **`secrets/google_service_account.json`**. The `secrets/` folder is git-ignored.
3. Open the JSON and copy `client_email`. It looks like `name@project.iam.gserviceaccount.com`.
4. **Share each spreadsheet with that email as Viewer.**
5. Add the spreadsheet IDs to `config.toml`. The ID is the part of the URL between `/d/` and `/edit`. Leave out any language you don't have a sheet for.

```toml
[gsheets.sheets]
pl = "1AbC..."
fr = "..."
en = "..."
```

**Sheet format** (first sheet of each spreadsheet, or set `gsheets.sheet_name`):

| A: Expression | B: Comment (optional) |
|---|---|
| skakać | |
| pain | the bread meaning |

Row 1 is skipped only if it looks like a header (`Expression`, `Word`, `Phrase`…), so you can start with a word in A1. Blank rows are skipped. Column B is passed to the LLM as an instruction. A word is identified by its column A text, not by its row number, so reordering rows or editing comments doesn't create new cards.

Status messages:
- `service account key not found` → check the file path.
- `key OK, but no spreadsheets configured` → add IDs as above.
- `share this sheet with <email> as Viewer` → a 403 or 404 on that sheet, so share it with that email.

### 5. Anki

1. Start Anki with AnkiConnect installed (it listens on `localhost:8765`).
2. The checks Push needs:
   - A note type named in `anki.note_type` (default `Cloze`) with fields exactly `Text, Expression, Translation, Picture, HINT, NOTE`, in that order.
   - Every deck in `[anki.decks]` exists. By default these are `Polish`, `French` and `English`. Create the missing ones, or edit the names or remove languages in `config.toml`.
3. If "Cloze" turns out to be Anki's built-in note type (different fields), rename your own note type in Anki (for example "Cloze RU") and set `anki.note_type` to it. The app never renames anything for you.
4. Add a `NOTE` field to the note type if it doesn't have one yet. Don't change the card templates, because the back template should already show `{{NOTE}}`.

Languages without a deck in `[anki.decks]` are parked as "Unsupported". They become eligible again on the next Sync once you add the deck mapping.

### 6. Optional: `.apkg` fallback

Use this if you can't use AnkiConnect. The "Export .apkg" button writes a file to `exports/`, which you import into Anki by hand. Then click "Confirm .apkg imported". It needs AnkiConnect once to read your templates and CSS. For the import to land in your existing note type rather than a copy, `apkg.model_id` must be that note type's id. **Test this on a copy of your collection first.**

## Daily use

1. Add words: pink highlights in Kindle or Zotero, or rows in your sheets.
2. **Sync** backs up the database, pulls new items, and generates up to `sync.max_cards_per_sync` (50) cards. Words from Google Sheets are generated first, then Kindle/Zotero, oldest first. Repeat Sync to work through a backlog.
3. Review in the queue. You can approve, edit, regenerate (with an optional instruction), change the language, add another meaning, add a new form, or reject. Cards flagged "already known" default to skip. "Add new form" gives you another inflection of the same word.
4. **Push to Anki** sends approved cards. Failed ones stay queued for retry.

Card format: `Text` is the cloze sentence, `Expression` is the base form, and `Translation` is Russian with the answer in `<b>`. `NOTE` holds the base form (when it differs) and the source line.

Tags added to every note: `auto`, `src::kindle|zotero|sheets`, and `book::<title-slug>` for Kindle and Zotero.

## CLI

Run from `backend/`:

```bash
uv run anki-cloze inspect readwise|zotero|sheets [--limit 10] [--save file.json]
uv run anki-cloze backup
uv run anki-cloze restore <backup-file>     # backs up the current DB first
```

`inspect` prints raw, unfiltered items. Sanitize anything you save before committing it as a test fixture under `backend/tests/fixtures/`.

## Backups

The database (`data/anki-cloze.db`) is backed up automatically before every Sync, Push and export, using SQLite's online backup API. The last 20 are kept in `store.backup_dir`. Point that at a synced or off-device folder. Your cards live in Anki, so losing the database loses review state and duplicate history, not cards.

## Configuration reference

`config.toml` sections: `[store]`, `[sync]`, `[readwise]`, `[zotero]`, `[gsheets]` and `[gsheets.sheets]`, `[generator]`, `[anki]` and `[anki.decks]`, `[apkg]`, `[lang]`. Every key has a comment or a sensible default. Secrets never go in it.

## Troubleshooting

| Symptom | Fix |
|---|---|
| OpenRouter: `HTTP 404 No endpoints found` | The model doesn't support structured outputs. Pick another ID in `generator.model`. |
| OpenRouter: `OPENROUTER_API_KEY not set` | Set it in `.env` and restart. |
| Readwise down | Check `READWISE_TOKEN`. |
| Readwise syncs nothing | `pink_filter` doesn't match. Re-run `inspect readwise`. |
| Zotero down | Zotero isn't running, or the "allow other applications" setting is off. |
| Zotero: `pink_color is empty` | Fill `zotero.pink_color` (step 3). |
| Google down | See the status messages in step 4. |
| AnkiConnect down | Anki isn't running, or AnkiConnect isn't installed. |
| AnkiConnect: fields mismatch or deck missing | See step 5. |
| Config change has no effect | Restart `npm run dev`. |

## Extending

Sources (`sources/base.py`), the generator (`generator/base.py`) and sinks (`sinks/base.py`) are separate interfaces, so a new source, LLM provider or output doesn't touch the other layers. The `NOTE` wording lives only in `generator/note.py`.
