CREATE TABLE source_notes (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  text TEXT NOT NULL,
  instruction TEXT,
  language_hint TEXT,
  book_title TEXT,
  author TEXT,
  raw TEXT,
  source_updated_at TEXT,
  fetched_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  status_reason TEXT
);
CREATE INDEX idx_source_notes_status ON source_notes(status);

CREATE TABLE cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_note_id TEXT NOT NULL REFERENCES source_notes(id),
  parent_card_id INTEGER REFERENCES cards(id),
  kind TEXT NOT NULL DEFAULT 'initial',
  language TEXT,
  language_confidence TEXT,
  language_reason TEXT,
  text TEXT,
  expression TEXT,
  form_used TEXT,
  is_base_form INTEGER,
  grammar TEXT,
  translation TEXT,
  hint TEXT,
  note TEXT,
  tags TEXT,
  deck TEXT,
  meaning_gloss_ru TEXT,
  already_known INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'generated',
  anki_note_id INTEGER,
  error TEXT,
  model TEXT,
  prompt_version TEXT,
  regen_instruction TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_cards_status ON cards(status);
CREATE INDEX idx_cards_lemma ON cards(language, expression);

CREATE TABLE sync_state (
  source TEXT PRIMARY KEY,
  updated_after TEXT,
  last_run_at TEXT
);

CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  entity TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  event TEXT NOT NULL,
  old TEXT,
  new TEXT,
  detail TEXT
);
