"""Groq - OpenAI-compatible API, with automatic rotation across your own keys.

Put GROQ_API_KEY_1 ... GROQ_API_KEY_N in .env to use several keys from your
Groq account. With no numbered keys, LLM_API_KEY is used as the only key and
the provider behaves like before.

Retrying is safe here: a chat completion has no side effects, so the same
request can be re-sent with another key.
"""
from __future__ import annotations

from typing import Optional

import requests

from llm.base import (LLMAuthError, LLMError, LLMModelError, LLMQuotaError, LLMResponseError,
                      LLMUnavailableError)
from llm.groq_key_pool import (AllKeysCoolingDown, GroqKeyPool, NoUsableKeys, analyse_rate_limit,
                               format_wait)
from llm.openai_compatible import OpenAICompatibleProvider
from utils import logger
from utils.helpers import error_detail, http_request

try:                      # settings are optional - sensible defaults if missing
    import config as _config
except ImportError:       # pragma: no cover
    _config = None


def _setting(name: str, default):
    return getattr(_config, name, default) if _config is not None else default


class GroqProvider(OpenAICompatibleProvider):
    provider_name = "groq"
    DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"

    TRANSIENT_CODES = {408, 498, 500, 502, 503, 504}   # 498 = Flex tier at capacity (Groq docs)

    def __init__(self, api_key: str, model: str, base_url: str = "", timeout: float = 60,
                 extra_headers=None, provider_name: str = "", pool: Optional[GroqKeyPool] = None):
        super().__init__(api_key, model, base_url, timeout, extra_headers, provider_name)
        try:
            self.pool = pool or GroqKeyPool.from_environment(fallback_key=api_key)
        except NoUsableKeys:
            raise LLMAuthError("No Groq API key found. Add GROQ_API_KEY_1, GROQ_API_KEY_2, ... "
                               "(or LLM_API_KEY) to your .env file.")
        self.max_wait = float(_setting("GROQ_MAX_WAIT_SECONDS", 120))
        attempts = int(_setting("GROQ_MAX_ATTEMPTS_PER_REQUEST", 0) or 0)
        self.max_attempts = attempts if attempts > 0 else self.pool.size * 2 + 2
        self.max_transient = 3
        self.backoff_base = float(_setting("RETRY_BASE_DELAY_SECONDS", 2.0))

    def complete(self, prompt: str, system_prompt: str = "", temperature: float = 0.7) -> str:
        headers, payload = self._build_request(prompt, system_prompt, temperature)
        url = self._endpoint()
        attempts = transient = waits = 0

        while True:
            attempts += 1
            if attempts > self.max_attempts:      # hard stop - never loop forever
                raise LLMUnavailableError(f"Groq request gave up after {self.max_attempts} attempts "
                                          f"({self.pool.summary()})")
            # ---------------------------------------------------- pick a key
            try:
                key = self.pool.acquire()
            except NoUsableKeys as exc:
                raise LLMAuthError(f"No usable Groq keys: {exc}. Check your Groq key(s) in .env "
                                   "(GROQ_API_KEY_1..N, or LLM_API_KEY).")
            except AllKeysCoolingDown as exc:
                if exc.wait_seconds <= self.max_wait and waits < 3:
                    waits += 1
                    attempts -= 1                 # waiting is not an attempt
                    logger.warn(f"[Groq] All keys temporarily unavailable - earliest is {exc.key_id} "
                                f"in {format_wait(exc.wait_seconds)}. Waiting...")
                    self.pool.sleep(exc.wait_seconds + 0.5)
                    continue
                raise LLMQuotaError(
                    f"All {exc.total} Groq key(s) are rate-limited. Earliest available: {exc.key_id} in "
                    f"{format_wait(exc.wait_seconds)}. Run again after that (company research is cached), "
                    f"or raise GROQ_MAX_WAIT_SECONDS in config.py to wait automatically.")

            # ---------------------------------------------------- send the request
            send_headers = dict(headers)
            send_headers["Authorization"] = f"Bearer {key.secret}"   # never logged
            try:
                response = http_request("POST", url, headers=send_headers, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                transient += 1
                if transient >= self.max_transient:
                    raise LLMUnavailableError(f"could not reach Groq ({exc.__class__.__name__})")
                self._backoff(transient, f"network problem ({exc.__class__.__name__})")
                continue
            finally:
                send_headers.pop("Authorization", None)

            code = response.status_code
            if code < 400:
                self.pool.report_success(key, response.headers)
                try:
                    data = response.json()
                except ValueError:
                    raise LLMResponseError("Groq returned a non-JSON response")
                if not isinstance(data, dict):
                    raise LLMResponseError("Groq returned an unexpected response")
                return self._parse_reply(data)

            detail = error_detail(response)
            low = detail.lower()

            if code == 429:
                info = analyse_rate_limit(detail, response.headers)
                if info.too_large:
                    # Same limit on every key - rotating cannot help.
                    raise LLMError(f"This request needs about {info.requested} tokens but your Groq limit is "
                                   f"{info.limit} {info.reason}. Lower MAX_RESEARCH_CHARS in config.py "
                                   f"or use a model with a higher limit.")
                self.pool.report_rate_limited(key, info)
                continue                                   # next available key

            if code == 401:
                self.pool.disable(key, "rejected by Groq (401) - invalid or revoked key")
                continue
            if code == 403:
                if "model" in low:
                    raise LLMModelError(f"groq: no access to model '{self.model}' (HTTP 403). {detail}")
                self.pool.disable(key, "refused by Groq (403) - permission restricted")
                continue
            if code in self.TRANSIENT_CODES:
                transient += 1
                if transient >= self.max_transient:
                    raise LLMUnavailableError(f"Groq server problem (HTTP {code})")
                self._backoff(transient, f"server problem (HTTP {code})")
                continue

            # 400 / 404 / 413 / 422 / 424 ...: a problem with the request itself,
            # the same on every key - report it instead of rotating.
            self._raise_for_code(code, detail)

    def _backoff(self, n: int, why: str) -> None:
        wait = self.backoff_base * (2 ** (n - 1))
        logger.warn(f"[Groq] {why} - retrying in {wait:.1f}s")
        self.pool.sleep(wait)
