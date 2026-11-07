ALTER TABLE cards ADD COLUMN audio_file TEXT;       -- file name only (inside tts.audio_dir)
ALTER TABLE cards ADD COLUMN audio_text_hash TEXT;  -- sha1 of tts_text(text)
ALTER TABLE cards ADD COLUMN audio_model TEXT;
ALTER TABLE cards ADD COLUMN audio_voice TEXT;
ALTER TABLE cards ADD COLUMN audio_error TEXT;
ALTER TABLE cards ADD COLUMN audio_at TEXT;
