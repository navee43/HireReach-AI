"""Small shared helpers: HTTP, retries, text cleaning, JSON parsing, domains."""
from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Tuple, Type
from urllib.parse import urlparse

import requests

from utils import logger

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "HR-Outreach-Generator/1.0 (personal job-search script)"
)
ROBOTS_AGENT = "HR-Outreach-Generator"


# --------------------------------------------------------------------------- HTTP
def http_request(method: str, url: str, *, timeout: float = 30, **kwargs: Any) -> requests.Response:
    """Single place where the program talks to the internet."""
    headers = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("User-Agent", USER_AGENT)
    return requests.request(method, url, headers=headers, timeout=timeout, **kwargs)


def error_detail(response: requests.Response, limit: int = 300) -> str:
    """Best-effort readable error message from an API response."""
    try:
        data = response.json()
    except ValueError:
        return logger.mask(clean_text(response.text or "", limit))
    message = ""
    if isinstance(data, dict):
        err = data.get("error", data.get("detail", data.get("message", "")))
        if isinstance(err, dict):
            message = err.get("message") or err.get("error") or json.dumps(err)
        elif isinstance(err, list):
            message = json.dumps(err)
        else:
            message = str(err or "")
        if not message:
            message = json.dumps(data)
    else:
        message = json.dumps(data)
    return logger.mask(clean_text(message, limit))


def parse_retry_after(response: requests.Response) -> Optional[float]:
    value = response.headers.get("retry-after") or response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


# ------------------------------------------------------------------------- Retries
def retry_call(
    func: Callable[[], Any],
    *,
    attempts: int,
    base_delay: float,
    retryable: Tuple[Type[BaseException], ...],
    label: str = "request",
) -> Any:
    """Call ``func``; retry only temporary errors, with exponential backoff.

    Errors that are not in ``retryable`` (bad API key, wrong model, ...) are
    raised immediately and never retried.
    """
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except retryable as exc:  # type: ignore[misc]
            if attempt >= attempts:
                raise
            wait = base_delay * (2 ** (attempt - 1))
            retry_after = getattr(exc, "retry_after", None)
            if retry_after:
                wait = max(wait, min(float(retry_after), 60.0))
            wait += random.uniform(0, wait * 0.1)
            logger.warn(f"{label}: attempt {attempt} of {attempts} failed ({exc}). "
                        f"Retrying in {wait:.1f}s...")
            time.sleep(wait)
    return None  # unreachable


# ---------------------------------------------------------------------------- Text
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MARKERS = re.compile(r"[<>]{3,}")


def clean_text(text: Any, max_chars: int = 0) -> str:
    """Collapse whitespace, drop control characters, optionally truncate."""
    if text is None:
        return ""
    text = _CONTROL_CHARS.sub(" ", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    if max_chars and len(text) > max_chars:
        cut = text[:max_chars].rsplit(" ", 1)[0]
        text = (cut or text[:max_chars]).rstrip(" ,.;:") + "..."
    return text


def neutralize(text: str) -> str:
    """Remove sequences that could break out of our <<< >>> data fences."""
    return _MARKERS.sub(" ", text or "")


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------- JSON
def extract_json_object(text: str) -> dict:
    """Pull the first JSON object out of an LLM reply.

    Handles ```json fences, <think> blocks and chatter around the JSON.
    Raises ValueError if no valid object is found.
    """
    if not text:
        raise ValueError("empty reply")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        value = json.loads(text, strict=False)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    start = text.find("{")
    while start != -1:
        depth, in_string, escape = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        value = json.loads(candidate, strict=False)
                        if isinstance(value, dict):
                            return value
                    except ValueError:
                        break
                    break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found in reply")


# ------------------------------------------------------------------------- Domains
_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu", "gen", "firm", "ind", "res", "ltd", "plc"}


def registrable_domain(domain: str) -> str:
    """'mail.careers.company.co.in' -> 'company.co.in' (simple, no external list)."""
    labels = [p for p in domain.lower().strip(".").split(".") if p]
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def url_host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def same_site(url: str, domain: str) -> bool:
    host = url_host(url)
    return bool(host) and registrable_domain(host) == registrable_domain(domain)


def is_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
