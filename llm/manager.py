"""LLMManager - the only LLM object the rest of the program talks to.

    llm = LLMManager(provider="groq", api_key=..., model=..., base_url="")
    text = llm.generate(prompt, system_prompt="...", temperature=0.7)

To add a provider with an OpenAI-compatible API, add one line to PROVIDERS.
"""
from __future__ import annotations

import time
from typing import Any, Dict

from llm.base import (LLMAuthError, LLMModelError, LLMRateLimitError, LLMUnavailableError)
from llm.gemini import GeminiProvider
from llm.groq import GroqProvider
from llm.groq_key_pool import discover_groq_keys
from llm.huggingface import HuggingFaceProvider
from llm.openai_compatible import OpenAICompatibleProvider
from utils.helpers import retry_call

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "groq":        {"cls": GroqProvider, "label": "Groq", "key_pool": True},   # GROQ_API_KEY_1..N supported
    "openrouter":  {"cls": OpenAICompatibleProvider, "label": "OpenRouter",
                    "base_url": "https://openrouter.ai/api/v1",
                    "headers": {"X-OpenRouter-Title": "HR Outreach Generator"}},
    "huggingface": {"cls": HuggingFaceProvider, "label": "Hugging Face"},
    "gemini":      {"cls": GeminiProvider, "label": "Google Gemini"},
    "ollama":      {"cls": OpenAICompatibleProvider, "label": "Ollama (local)",
                    "base_url": "http://localhost:11434/v1", "needs_key": False},
    "lmstudio":    {"cls": OpenAICompatibleProvider, "label": "LM Studio (local)",
                    "base_url": "http://localhost:1234/v1", "needs_key": False},
    "openai":      {"cls": OpenAICompatibleProvider, "label": "OpenAI",
                    "base_url": "https://api.openai.com/v1"},
    "openai_compatible": {"cls": OpenAICompatibleProvider, "label": "OpenAI-compatible",
                          "needs_base_url": True, "needs_key": False},
}


class LLMManager:
    def __init__(self, provider: str, api_key: str, model: str, base_url: str = "",
                 timeout: float = 60, max_retries: int = 3, retry_base_delay: float = 2.0,
                 min_interval: float = 0.0):
        name = (provider or "").strip().lower()
        if name not in PROVIDERS:
            raise LLMModelError(f"Unknown LLM_PROVIDER '{provider}'. Choose one of: {', '.join(PROVIDERS)}")
        spec = PROVIDERS[name]
        has_pool_keys = bool(spec.get("key_pool") and discover_groq_keys(fallback_key=api_key))
        if spec.get("needs_key", True) and not api_key and not has_pool_keys:
            raise LLMAuthError("LLM_API_KEY is empty. Add your key to the .env file (see README)."
                               + (" For several Groq keys use GROQ_API_KEY_1, GROQ_API_KEY_2, ..."
                                  if spec.get("key_pool") else ""))
        if not model:
            raise LLMModelError("LLM_MODEL is empty. Set a model name in .env or config.py.")
        base = base_url or spec.get("base_url", "")
        if spec.get("needs_base_url") and not base:
            raise LLMModelError("LLM_BASE_URL is required when LLM_PROVIDER is 'openai_compatible'.")

        cls = spec["cls"]
        if issubclass(cls, OpenAICompatibleProvider):
            self._provider = cls(api_key, model, base, timeout,
                                 extra_headers=spec.get("headers"), provider_name=name)
        else:
            self._provider = cls(api_key, model, base, timeout)

        self.provider_name = name
        self.label = spec["label"]
        self.model = model
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.min_interval = min_interval
        self.calls = 0
        self._last_call = 0.0

    def generate(self, prompt: str, system_prompt: str = "", temperature: float = 0.7) -> str:
        """Send a prompt to the configured provider and return its text reply."""
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)  # stay under free-tier requests-per-minute limits
        try:
            return retry_call(
                lambda: self._provider.complete(prompt, system_prompt, temperature),
                attempts=self.max_retries,
                base_delay=self.retry_base_delay,
                retryable=(LLMRateLimitError, LLMUnavailableError),
                label=f"LLM ({self.label})",
            )
        finally:
            self.calls += 1
            self._last_call = time.monotonic()

    def test(self) -> str:
        """Quick check that the key and model work."""
        return self.generate("Reply with exactly one word: OK", temperature=0)

    def describe(self) -> str:
        pool = getattr(self._provider, "pool", None)
        keys = f", {pool.size} keys" if pool is not None and pool.size > 1 else ""
        return f"{self.label} ({self.model}{keys})"
