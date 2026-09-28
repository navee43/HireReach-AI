"""Adapter for any provider with an OpenAI-style /chat/completions API.

Works for Groq, OpenRouter, Hugging Face, Ollama, LM Studio, OpenAI and many
others - only the base URL, key and model change.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from llm.base import BaseLLMProvider, LLMModelError, LLMResponseError


class OpenAICompatibleProvider(BaseLLMProvider):
    provider_name = "openai_compatible"

    def __init__(self, api_key: str, model: str, base_url: str = "", timeout: float = 60,
                 extra_headers: Optional[Dict[str, str]] = None, provider_name: str = ""):
        super().__init__(api_key, model, base_url, timeout)
        self.extra_headers = extra_headers or {}
        if provider_name:
            self.provider_name = provider_name

    def _endpoint(self) -> str:
        if not self.base_url:
            raise LLMModelError("LLM_BASE_URL is empty. Set it in .env for this provider.")
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _build_request(self, prompt: str, system_prompt: str, temperature: float) -> Tuple[Dict[str, str], Dict[str, Any]]:
        """Headers (without the API key) and JSON body for one chat request."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        headers = {"Content-Type": "application/json", **self.extra_headers}
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        return headers, payload

    def _parse_reply(self, data: Dict[str, Any]) -> str:
        """Turn a /chat/completions JSON response into the reply text."""
        # Some providers (e.g. OpenRouter) send errors inside a 200 response.
        if "error" in data and not data.get("choices"):
            err = data["error"] if isinstance(data["error"], dict) else {"message": str(data["error"])}
            code = err.get("code")
            code = int(code) if isinstance(code, int) or (isinstance(code, str) and code.isdigit()) else 500
            self._raise_for_code(code, str(err.get("message", "")))
        try:
            content = data["choices"][0]["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LLMResponseError(f"{self.provider_name} returned an unexpected response shape")
        if isinstance(content, list):  # some providers return content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.S | re.I).strip()
        if not content:
            raise LLMResponseError(f"{self.provider_name} returned an empty reply")
        return content

    def complete(self, prompt: str, system_prompt: str = "", temperature: float = 0.7) -> str:
        headers, payload = self._build_request(prompt, system_prompt, temperature)
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = self._post_json(self._endpoint(), headers, payload)
        return self._parse_reply(data)
