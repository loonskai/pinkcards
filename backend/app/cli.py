from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_settings
from .store.backup import make_backup, restore_backup


SAMPLES = {
    "fr": "Il ne mange jamais de pain, mais elle a mangé une pomme hier soir.",
    "pl": "Wczoraj wieczorem skoczył do wody, bo było bardzo gorąco.",
    "en": "She hadn't expected him to arrive so early on a Sunday morning.",
}


def inspect_tts(s, voices: list[str] | None = None) -> int:
    """Step 1: list speech models + voices. Step 2: check the output format. Then write samples per language."""
    import httpx

    from .tts.base import TTSError
    from .tts.openrouter import OpenRouterTTS

    if not s.openrouter_api_key:
        print("OPENROUTER_API_KEY not set (.env)")
        return 1
    tts = OpenRouterTTS(s)
    models = tts.speech_models()
    print(f"{len(models)} OpenRouter speech models. Google ones:")
    for m in models:
        if m["id"].startswith("google/"):
            pr = m.get("pricing") or {}
            per = lambda k: f"${float(pr.get(k) or 0) * 1e6:g}/1M"  # noqa: E731
            print(f"\n  {m['id']}  ({m.get('name')})  input {per('prompt')}, output {per('completion')}")
            print(f"    voices: {', '.join(m.get('supported_voices') or [])}")
    cur = next((m for m in models if m["id"] == s.tts.model), None)
    print(f"\nconfigured: tts.model={s.tts.model!r}" + ("" if cur or not s.tts.model else "  <-- NOT a speech model"))
    langs = [l for l in s.tts.languages if s.tts_enabled(l)]
    if not langs:
        print("no language has audio enabled (needs tts.model + tts.languages.<lang>.enabled + voice)")
        return 1

    lang = langs[0]
    voice = tts.voice_for(lang)
    print(f"\nformat check with {s.tts.model} / {voice}:")
    for fmt in ("mp3", "wav", "pcm"):
        try:
            r = tts.synthesize_raw(SAMPLES.get(lang, "Bonjour."), voice, fmt)
            print(f"  {fmt}: HTTP {r.status_code}, content-type {r.headers.get('content-type')}, {len(r.content)} bytes")
        except TTSError as e:
            print(f"  {fmt}: {str(e).split(' (model')[0]}")
        except httpx.HTTPError as e:
            print(f"  {fmt}: {e}")

    out = s.audio_dir / "_samples"
    out.mkdir(parents=True, exist_ok=True)
    print(f"\nsamples -> {out}")
    for lang in langs:
        for v in voices or [tts.voice_for(lang)]:
            try:
                mp3 = tts.synthesize(SAMPLES.get(lang, SAMPLES["en"]), lang, voice=v)
            except TTSError as e:
                print(f"  {lang}/{v}: FAILED {e}")
                continue
            path = out / f"{lang}_{s.tts.model.replace('/', '_')}_{v}.mp3"
            path.write_bytes(mp3)
            print(f"  {path.name}  ({len(mp3)} bytes)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="anki-cloze")
    sub = p.add_subparsers(dest="cmd", required=True)
    ins = sub.add_parser("inspect", help="print raw JSON from a source, unfiltered")
    ins.add_argument("source", choices=["readwise", "zotero", "sheets", "tts"])
    ins.add_argument("--limit", type=int, default=10)
    ins.add_argument("--voices", help="tts: comma-separated voices to sample for each enabled language "
                                      "(default: the configured voice)")
    ins.add_argument("--save", help="write output to this file (sanitize before committing!)")
    sub.add_parser("backup", help="back up the DB now")
    rs = sub.add_parser("restore", help="restore the DB from a backup file")
    rs.add_argument("file")
    args = p.parse_args(argv)
    s = load_settings()

    if args.cmd == "inspect" and args.source == "tts":
        return inspect_tts(s, args.voices.split(",") if args.voices else None)
    if args.cmd == "inspect":
        from .sources.gsheets import GsheetsSource
        from .sources.readwise import ReadwiseSource
        from .sources.zotero import ZoteroSource

        src = {"readwise": ReadwiseSource, "zotero": ZoteroSource, "sheets": GsheetsSource}[args.source](s)
        out = json.dumps(src.inspect(args.limit), indent=2, ensure_ascii=False)
        if args.save:
            Path(args.save).write_text(out, "utf-8")
        print(out)
    elif args.cmd == "backup":
        print(make_backup(s.db_path, s.backup_dir, s.store.backup_keep))
    elif args.cmd == "restore":
        pre = restore_backup(s.db_path, s.backup_dir, Path(args.file), s.store.backup_keep)
        print("restored; previous DB saved at", pre)
    return 0


if __name__ == "__main__":
    sys.exit(main())
