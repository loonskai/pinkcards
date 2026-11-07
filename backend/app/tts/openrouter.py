"""OpenRouter speech client (POST /api/v1/audio/speech -> raw audio bytes, not JSON).

VERIFIED against the live API: Google Gemini TTS models accept only response_format="pcm" (24 kHz, 16-bit, mono;
`mp3` -> HTTP 400, `wav` is rejected by OpenRouter's schema). PCM is encoded to mp3 locally with `lameenc`.
"""
from __future__ import annotations

import re
import time

import httpx

from ..config import Settings
from .base import TTS, TTSError

SPEECH_URL = "https://openrouter.ai/api/v1/audio/speech"
MODELS_URL = "https://openrouter.ai/api/v1/models?output_modalities=speech"


def pcm_to_mp3(pcm: bytes, rate: int = 24000, channels: int = 1, bitrate: int = 64) -> bytes:
    import lameenc

    enc = lameenc.Encoder()
    enc.set_bit_rate(bitrate)
    enc.set_in_sample_rate(rate)
    enc.set_channels(channels)
    enc.set_quality(2)
    return bytes(enc.encode(pcm[: len(pcm) - len(pcm) % (2 * channels)]) + enc.flush())


def _looks_like_mp3(b: bytes) -> bool:
    return b[:3] == b"ID3" or (len(b) > 2 and b[0] == 0xFF and b[1] & 0xE0 == 0xE0)


class OpenRouterTTS(TTS):
    def __init__(self, settings: Settings, client: httpx.Client | None = None, sleep=time.sleep):
        self.s = settings
        self.client = client or httpx.Client(timeout=120)
        self._sleep = sleep
        self._health_cache: tuple[float, tuple[bool, str]] | None = None

    @property
    def model(self) -> str:  # type: ignore[override]
        return self.s.tts.model

    @property
    def _headers(self):
        return {"Authorization": f"Bearer {self.s.openrouter_api_key}", "Content-Type": "application/json"}

    def voice_for(self, language: str) -> str:
        cfg = self.s.tts.languages.get(language)
        return cfg.voice if cfg else ""

    def _format(self) -> str:
        # Gemini only does pcm; everything else gets the configured format (mp3).
        return "pcm" if self.model.startswith("google/") else self.s.tts.format

    @staticmethod
    def _describe(e: Exception | None) -> str:
        if isinstance(e, httpx.HTTPStatusError):
            try:
                msg = e.response.json()["error"]["message"]
            except Exception:  # noqa: BLE001
                msg = e.response.text[:300]
            return f"HTTP {e.response.status_code}: {msg}"
        return str(e)

    def _post(self, body: dict) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(4):  # retry network / 5xx / 429 with backoff, like the generator
            try:
                r = self.client.post(SPEECH_URL, json=body, headers=self._headers)
                if r.status_code >= 500 or r.status_code == 429:
                    raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                r.raise_for_status()
                return r
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last = e
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500 \
                        and e.response.status_code != 429:
                    break
                self._sleep(2 ** attempt)
        raise TTSError(f"OpenRouter TTS failed: {self._describe(last)} (model {self.model})")

    def synthesize_raw(self, text: str, voice: str, fmt: str) -> httpx.Response:
        return self._post({"model": self.model, "input": text, "voice": voice, "response_format": fmt})

    def synthesize(self, text: str, language: str, voice: str | None = None) -> bytes:
        # `language` is not sent: speech models detect it from the text.
        voice = voice or self.voice_for(language)
        if not self.model or not voice:
            raise TTSError(f"TTS model/voice not configured for {language!r}")
        if not text.strip():
            raise TTSError("nothing to read")
        fmt = self._format()
        try:
            r = self.synthesize_raw(text, voice, fmt)
        except TTSError as e:
            # Unknown model that only does pcm: retry once with pcm.
            if fmt != "pcm" and "HTTP 400" in str(e) and "pcm" in str(e):
                r = self.synthesize_raw(text, voice, "pcm")
            else:
                raise
        ctype = r.headers.get("content-type", "")
        data = r.content
        if "json" in ctype or not data:
            raise TTSError(f"OpenRouter TTS returned no audio: {data[:200]!r} (model {self.model})")
        if "pcm" in ctype or (fmt == "pcm" and not _looks_like_mp3(data)):
            m = re.search(r"rate=(\d+)", ctype)
            c = re.search(r"channels=(\d+)", ctype)
            return pcm_to_mp3(data, int(m.group(1)) if m else 24000, int(c.group(1)) if c else 1)
        if _looks_like_mp3(data):
            return data
        raise TTSError(f"unexpected audio content-type {ctype!r} (model {self.model})")

    # ---- status / inspect ----
    def speech_models(self) -> list[dict]:
        r = self.client.get(MODELS_URL, headers=self._headers)
        r.raise_for_status()
        return r.json()["data"]

    def health(self) -> tuple[bool, str]:
        """Config sanity (model exists, voices are valid) for the enabled languages. Cached for 5 min."""
        langs = [l for l in self.s.tts.languages if self.s.tts_enabled(l)]
        if not langs:
            return True, "tts off"
        if self._health_cache and time.monotonic() - self._health_cache[0] < 300:
            return self._health_cache[1]
        try:
            models = {m["id"]: m for m in self.speech_models()}
            m = models.get(self.model)
            if not m:
                res = (False, f"TTS model {self.model!r} is not an OpenRouter speech model")
            else:
                voices = m.get("supported_voices")
                bad = [l for l in langs if voices and self.voice_for(l) not in voices]
                res = (False, f"TTS voice for {','.join(bad)} not in {self.model} voices") if bad else (True, "ok")
        except (httpx.HTTPError, KeyError, ValueError) as e:
            return False, f"TTS check failed: {e}"
        self._health_cache = (time.monotonic(), res)
        return res
