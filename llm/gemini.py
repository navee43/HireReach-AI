"""Google Gemini - native generateContent API. Free key at https://aistudio.google.com"""
from __future__ import annotations

from llm.base import BaseLLMProvider, LLMResponseError


class GeminiProvider(BaseLLMProvider):
    provider_name = "gemini"
    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def complete(self, prompt: str, system_prompt: str = "", temperature: float = 0.7) -> str:
        model = self.model[len("models/"):] if self.model.startswith("models/") else self.model
        url = f"{self.base_url}/models/{model}:generateContent"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature},
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        # Key goes in a header (not the URL) so it never shows up in error messages.
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}

        data = self._post_json(url, headers, payload)
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            raise LLMResponseError("Gemini returned no text" + (f" (blocked: {reason})" if reason else ""))
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
        if not text.strip():
            raise LLMResponseError(f"Gemini returned an empty reply "
                                   f"(finishReason={candidates[0].get('finishReason')})")
        return text.strip()
