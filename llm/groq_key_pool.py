"""GroqKeyPool - rotates between several Groq API keys from your own account.

Keys come from environment variables GROQ_API_KEY_1 ... GROQ_API_KEY_N (any N,
gaps allowed). If none are set, the single LLM_API_KEY is used, so the program
behaves exactly as before.

Rules:
* Keep using the current key while it works.
* When Groq answers 429, read the documented reset information
  (retry-after header, "Please try again in ..." in the message,
  x-ratelimit-reset-* headers), cool that key down until then, and switch to the
  next available key in round-robin order.
* A key rejected with 401/403 is disabled for the rest of the run.
* A cooled-down key becomes eligible again automatically once its time has passed.
* Keys are only ever identified as KEY_1, KEY_2, ... in logs - never the secret.

The pool only tracks state; llm/groq.py makes the HTTP calls.
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from utils import logger

ENV_PATTERN = re.compile(r"^GROQ_API_KEY_(\d+)$")
DAILY_KINDS = {"RPD", "TPD", "ASD"}


# --------------------------------------------------------------------------- errors
class AllKeysCoolingDown(Exception):
    """Every usable key is rate-limited right now."""

    def __init__(self, wait_seconds: float, key_id: str, total: int):
        super().__init__(f"all keys cooling down; earliest is {key_id} in {wait_seconds:.0f}s")
        self.wait_seconds = wait_seconds
        self.key_id = key_id
        self.total = total


class NoUsableKeys(Exception):
    """Every key was rejected (invalid/revoked/forbidden)."""


# ---------------------------------------------------------------------- key discovery
def discover_groq_keys(env: Optional[Mapping[str, str]] = None, fallback_key: str = "") -> List[Tuple[str, str]]:
    """Return [(key_id, secret)] from GROQ_API_KEY_<n>, sorted by n.

    Empty values and exact duplicates are skipped. If no numbered keys exist,
    fall back to GROQ_API_KEY or the given fallback (LLM_API_KEY) as KEY_1.
    """
    env = os.environ if env is None else env
    numbered = []
    for name, value in env.items():
        match = ENV_PATTERN.match(name)
        if match and value and value.strip():
            numbered.append((int(match.group(1)), value.strip()))
    numbered.sort()
    keys: List[Tuple[str, str]] = []
    seen = set()
    for number, secret in numbered:
        if secret in seen:
            logger.warn(f"[Groq] GROQ_API_KEY_{number} is the same key as an earlier one - ignored")
            continue
        seen.add(secret)
        keys.append((f"KEY_{number}", secret))
    if not keys:
        single = (env.get("GROQ_API_KEY") or "").strip() or (fallback_key or "").strip()
        if single:
            keys.append(("KEY_1", single))
    return keys


# ------------------------------------------------------------------ time parsing
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def parse_duration(text: Optional[str]) -> Optional[float]:
    """Groq duration strings -> seconds: '2m59.56s', '7.66s', '1h2m3s', '850ms', '12'."""
    if text is None:
        return None
    text = str(text).strip().lower()
    if not text:
        return None
    try:
        return max(0.0, float(text))          # plain seconds, e.g. retry-after: 2
    except ValueError:
        pass
    parts = _DURATION_PART.findall(text)
    if not parts or "".join(n + u for n, u in parts) != text.replace(" ", ""):
        return None
    scale = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    return sum(float(n) * scale[u] for n, u in parts)


def format_wait(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


# ------------------------------------------------------------ rate-limit analysis
@dataclass
class RateLimitInfo:
    cooldown: Optional[float]   # seconds, None if Groq gave no timing information
    kind: str                   # TPM / RPM / TPD / RPD / ... or "unknown"
    reason: str                 # human readable
    too_large: bool = False     # the single request is bigger than the whole limit
    limit: int = 0
    requested: int = 0

    @property
    def is_daily(self) -> bool:
        return self.kind in DAILY_KINDS


_KIND_RE = re.compile(r"on (tokens|requests|audio seconds)[^()]*\((TPM|TPD|RPM|RPD|ASH|ASD)\)", re.I)
_TRY_AGAIN_RE = re.compile(r"try again in\s+([0-9hms.]+)", re.I)
_LIMIT_RE = re.compile(r"Limit\s+(\d+),\s*Used\s+(\d+),\s*Requested\s+~?(\d+)", re.I)
_KIND_NAMES = {"TPM": "tokens per minute", "TPD": "tokens per day", "RPM": "requests per minute",
               "RPD": "requests per day", "ASH": "audio seconds per hour", "ASD": "audio seconds per day"}


def analyse_rate_limit(message: str, headers: Mapping[str, str]) -> RateLimitInfo:
    """Read Groq's documented 429 information (headers + error message)."""
    h = {k.lower(): v for k, v in (headers or {}).items()}
    message = message or ""
    kind_match = _KIND_RE.search(message)
    kind = kind_match.group(2).upper() if kind_match else "unknown"
    reason = _KIND_NAMES.get(kind, "rate limit")

    limit_match = _LIMIT_RE.search(message)
    if limit_match:
        limit, used, requested = (int(x) for x in limit_match.groups())
        if requested > limit:
            return RateLimitInfo(None, kind, reason, too_large=True, limit=limit, requested=requested)

    candidates = [parse_duration(h.get("retry-after"))]
    try_again = _TRY_AGAIN_RE.search(message)
    if try_again:
        candidates.append(parse_duration(try_again.group(1).rstrip(".")))
    waits = [c for c in candidates if c is not None]
    if not waits:   # fall back to the matching reset header
        header = "x-ratelimit-reset-requests" if kind in ("RPD", "RPM") else "x-ratelimit-reset-tokens"
        fallback = parse_duration(h.get(header))
        if fallback is not None:
            waits.append(fallback)
    return RateLimitInfo(max(waits) if waits else None, kind, reason)


# ---------------------------------------------------------------------- key state
@dataclass
class KeyState:
    id: str
    secret: str = field(repr=False)
    status: str = "ready"                 # ready | cooling | disabled
    cooldown_until: float = 0.0           # clock() time when usable again
    cooldown_reason: str = ""
    consecutive_rate_limits: int = 0
    rate_limit_hits: int = 0
    successes: int = 0
    last_used_at: Optional[str] = None    # wall-clock time, for display
    disabled_reason: str = ""

    def public(self) -> Dict[str, object]:
        """Safe snapshot for debugging - never includes the secret."""
        return {k: v for k, v in self.__dict__.items() if k != "secret"}


class GroqKeyPool:
    def __init__(self, keys: List[Tuple[str, str]], *, default_cooldown: float = 20.0,
                 max_cooldown: float = 24 * 3600.0, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        if not keys:
            raise NoUsableKeys("no Groq API keys configured")
        self._keys = [KeyState(id=key_id, secret=secret) for key_id, secret in keys]
        for state in self._keys:
            logger.register_secret(state.secret)          # masked in every log line
        self.default_cooldown = default_cooldown
        self.max_cooldown = max_cooldown
        self.clock = clock
        self.sleep = sleep
        self._lock = threading.RLock()
        self._current: Optional[int] = None
        self._announced: Optional[str] = None

    @classmethod
    def from_environment(cls, fallback_key: str = "", **kwargs) -> "GroqKeyPool":
        return cls(discover_groq_keys(fallback_key=fallback_key), **kwargs)

    @property
    def size(self) -> int:
        return len(self._keys)

    # ----------------------------------------------------------------- selection
    def _refresh(self) -> None:
        now = self.clock()
        for state in self._keys:
            if state.status == "cooling" and now >= state.cooldown_until:
                state.status = "ready"
                state.cooldown_reason = ""
                logger.info(f"[Groq] {state.id} available again")

    def acquire(self) -> KeyState:
        """Pick the key for the next request (thread-safe)."""
        with self._lock:
            self._refresh()
            n = len(self._keys)
            start = self._current if self._current is not None else -1
            if self._current is not None and self._keys[self._current].status == "ready":
                chosen = self._current                        # sticky: keep a working key
            else:
                chosen = next((i % n for i in range(start + 1, start + 1 + n)
                               if self._keys[i % n].status == "ready"), None)
            if chosen is None:
                cooling = [s for s in self._keys if s.status == "cooling"]
                if not cooling:
                    raise NoUsableKeys(f"all {n} Groq key(s) were rejected (invalid, revoked or forbidden)")
                soonest = min(cooling, key=lambda s: s.cooldown_until)
                raise AllKeysCoolingDown(max(0.0, soonest.cooldown_until - self.clock()), soonest.id, n)
            state = self._keys[chosen]
            if self._announced != state.id:
                if self._announced is None:
                    logger.info(f"[Groq] Using {state.id}" + (f" ({n} keys configured)" if n > 1 else ""))
                else:
                    logger.info(f"[Groq] Switching to {state.id}")
                self._announced = state.id
            self._current = chosen
            state.last_used_at = datetime.now().isoformat(timespec="seconds")
            return state

    # ------------------------------------------------------------------ outcomes
    def report_success(self, state: KeyState, headers: Optional[Mapping[str, str]] = None) -> None:
        with self._lock:
            state.successes += 1
            state.consecutive_rate_limits = 0
            h = {k.lower(): v for k, v in (headers or {}).items()}
            # Proactive: Groq says no requests are left today on this key -> rest it now.
            if (h.get("x-ratelimit-remaining-requests") or "").strip() == "0":
                wait = parse_duration(h.get("x-ratelimit-reset-requests"))
                if wait:
                    self._cool(state, wait, "daily request quota used up")

    def report_rate_limited(self, state: KeyState, info: RateLimitInfo) -> None:
        with self._lock:
            state.rate_limit_hits += 1
            state.consecutive_rate_limits += 1
            if info.cooldown is not None:
                wait = info.cooldown + 0.5
            else:   # no timing from Groq: back off exponentially per key
                wait = self.default_cooldown * (2 ** (state.consecutive_rate_limits - 1))
            self._cool(state, wait, info.reason)

    def disable(self, state: KeyState, reason: str) -> None:
        with self._lock:
            if state.status != "disabled":
                state.status = "disabled"
                state.disabled_reason = reason
                logger.warn(f"[Groq] {state.id} disabled for this run: {reason}")

    def _cool(self, state: KeyState, seconds: float, reason: str) -> None:
        seconds = min(max(seconds, 0.5), self.max_cooldown)
        until = self.clock() + seconds
        if state.status == "disabled":
            return
        if state.status == "cooling" and state.cooldown_until >= until:
            return                     # another thread already set a longer cooldown
        state.status = "cooling"
        state.cooldown_until = until
        state.cooldown_reason = reason
        logger.info(f"[Groq] {state.id} rate limited ({reason}) - available again in {format_wait(seconds)}")

    # ----------------------------------------------------------------- reporting
    def snapshot(self) -> List[Dict[str, object]]:
        with self._lock:
            return [s.public() for s in self._keys]

    def summary(self) -> str:
        with self._lock:
            self._refresh()
            ready = sum(s.status == "ready" for s in self._keys)
            cooling = sum(s.status == "cooling" for s in self._keys)
            disabled = sum(s.status == "disabled" for s in self._keys)
            return f"{ready} ready, {cooling} cooling down, {disabled} disabled (of {len(self._keys)})"
