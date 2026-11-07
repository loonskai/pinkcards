ALTER TABLE cards ADD COLUMN details TEXT;                          -- JSON: contractions/words/construction/etymology
ALTER TABLE cards ADD COLUMN details_stale INTEGER NOT NULL DEFAULT 0;  -- 1 = sentence edited after details were made
