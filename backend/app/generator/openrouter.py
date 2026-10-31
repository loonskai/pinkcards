from __future__ import annotations

import json
import time

import httpx
from pydantic import ValidationError

from ..config import Settings
from ..models import GeneratedCard
from .base import GenerationError, GenerationRequest, Generator
from .prompt import SYSTEM_V1, build_user_prompt
from .schema import CARD_JSON_SCHEMA
from .validate import validate_card

URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterGenerator(Generator):
    def __init__(self, settings: Settings, client: httpx.Client | None = None, sleep=time.sleep):
        self.s = settings
        self.model = settings.generator.model
        self.prompt_version = settings.generator.prompt_version
        self.client = client or httpx.Client(timeout=120)
        self._sleep = sleep

    @property
    def _headers(self):
        return {"Authorization": f"Bearer {self.s.openrouter_api_key}", "Content-Type": "application/json"}

    def health(self) -> tuple[bool, str]:
        if not self.s.openrouter_api_key:
            return False, "OPENROUTER_API_KEY not set"
        try:
            r = self.client.get("https://openrouter.ai/api/v1/auth/key", headers=self._headers)
            return (r.status_code == 200, "ok" if r.status_code == 200 else f"HTTP {r.status_code}")
        except httpx.HTTPError as e:
            return False, str(e)

    def _call(self, messages: list[dict]) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_schema", "json_schema": CARD_JSON_SCHEMA},
            "provider": {"require_parameters": True},
        }
        last: Exception | None = None
        for attempt in range(4):  # retry network / 5xx with backoff
            try:
                r = self.client.post(URL, json=body, headers=self._headers)
                if r.status_code >= 500 or r.status_code == 429:
                    raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last = e
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500 \
                        and e.response.status_code != 429:
                    break
                self._sleep(2 ** attempt)
        raise GenerationError(f"OpenRouter request failed: {self._describe(last)} (model {self.model})")

    @staticmethod
    def _describe(e: Exception | None) -> str:
        if isinstance(e, httpx.HTTPStatusError):
            try:
                msg = e.response.json()["error"]["message"]
            except Exception:  # noqa: BLE001
                msg = e.response.text[:300]
            return f"HTTP {e.response.status_code}: {msg}"
        return str(e)

    def generate(self, req: GenerationRequest) -> GeneratedCard:
        messages = [{"role": "system", "content": SYSTEM_V1},
                    {"role": "user", "content": build_user_prompt(req)}]
        error = ""
        for attempt in range(2):  # schema/validation failure = one automatic retry
            msgs = messages if not error else messages + [
                {"role": "user", "content": f"Your previous answer was invalid: {error}. Fix it and answer again."}]
            raw = self._call(msgs)
            try:
                card = GeneratedCard.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValidationError) as e:
                error = f"schema error: {e}"
                continue
            errs = validate_card(card.model_dump())
            if not errs:
                return card
            error = "; ".join(errs)
        raise GenerationError(error)
