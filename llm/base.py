"""Common interface and error types for every LLM provider."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import requests

from utils.helpers import error_detail, http_request, parse_retry_after


class LLMError(Exception):
    """Base class for LLM problems."""


class LLMAuthError(LLMError):
    """API key missing or rejected. Never retried."""


class LLMModelError(LLMError):
    """Model name or endpoint is wrong. Never retried."""


class LLMQuotaError(LLMError):
    """Free credits / daily quota used up. Never retried, so you are never charged."""


class LLMRateLimitError(LLMError):
    """Too many requests right now. Retried with backoff."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class LLMUnavailableError(LLMError):
    """Temporary network or server problem. Retried with backoff."""


class LLMResponseError(LLMError):
    """The provider answered, but not with usable text."""


# Errors that will fail every following call too, so the run should stop.
FATAL_LLM_ERRORS = (LLMAuthError, LLMModelError, LLMQuotaError)

_DAILY_WORDS = ("per day", "perday", "daily", "insufficient", "billing", "credits", "credit balance")


class BaseLLMProvider(ABC):
    """Every provider adapter implements ``complete``."""

    provider_name = "base"
    DEFAULT_BASE_URL = ""

    def __init__(self, api_key: str, model: str, base_url: str = "", timeout: float = 60):
        self.api_key = api_key or ""
        self.model = model
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    @abstractmethod
    def complete(self, prompt: str, system_prompt: str = "", temperature: float = 0.7) -> str:
        """Return the model's text reply."""

    # ------------------------------------------------------------------ helpers
    def _post_json(self, url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = http_request("POST", url, headers=headers, json=payload, timeout=self.timeout)
        except requests.Timeout:
            raise LLMUnavailableError(f"{self.provider_name} did not answer within {self.timeout:.0f}s")
        except requests.ConnectionError:
            raise LLMUnavailableError(
                f"could not connect to {self.provider_name} at {self.base_url} "
                "(check your internet connection, or that the local model server is running)")
        except requests.RequestException as exc:
            raise LLMUnavailableError(f"request failed: {exc.__class__.__name__}")

        if response.status_code >= 400:
            self._raise_for_code(response.status_code, error_detail(response), parse_retry_after(response))
        try:
            data = response.json()
        except ValueError:
            raise LLMResponseError(f"{self.provider_name} returned a non-JSON response")
        if not isinstance(data, dict):
            raise LLMResponseError(f"{self.provider_name} returned an unexpected response")
        return data

    def _raise_for_code(self, code: int, detail: str, retry_after: Optional[float] = None) -> None:
        """Turn an HTTP error into one of the error types above."""
        name = self.provider_name
        low = (detail or "").lower()
        if code == 401 or (code == 400 and "api key" in low):
            raise LLMAuthError(f"{name} rejected the API key (HTTP {code}). Check LLM_API_KEY in .env. {detail}")
        if code == 403:
            if "model" in low:
                raise LLMModelError(f"{name}: no access to model '{self.model}' (HTTP 403). {detail}")
            raise LLMAuthError(f"{name} refused access (HTTP 403). Check LLM_API_KEY. {detail}")
        if code == 402:
            raise LLMQuotaError(f"{name} says credits/payment are required (HTTP 402). "
                                f"Your free credits may be used up - stopping so you are never charged. {detail}")
        if code == 404:
            raise LLMModelError(f"{name}: model or endpoint not found (HTTP 404). "
                                f"Check LLM_MODEL ('{self.model}') and LLM_BASE_URL. {detail}")
        if code == 429:
            if any(w in low for w in _DAILY_WORDS) or (retry_after or 0) > 120:
                raise LLMQuotaError(f"{name}: free-tier limit reached (HTTP 429). "
                                    f"Try again later or switch provider/model. {detail}")
            reason = f" - provider says: {detail}" if detail else ""
            raise LLMRateLimitError(f"{name} rate limit (HTTP 429){reason}", retry_after)
        if code == 400 and "model" in low and any(
                w in low for w in ("not found", "does not exist", "invalid", "decommissioned",
                                   "not supported", "unknown", "no endpoints")):
            raise LLMModelError(f"{name}: model '{self.model}' is not available. {detail}")
        if code in (408, 409) or code >= 500:
            raise LLMUnavailableError(f"{name} server problem (HTTP {code})")
        raise LLMError(f"{name} error (HTTP {code}): {detail}")
