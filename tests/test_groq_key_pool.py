"""Offline tests for Groq key rotation - no real API calls, no real waiting.

Run from the project folder:
    python tests/test_groq_key_pool.py

A fake Groq server answers each request based on which key was used, and a fake
clock replaces real sleeping, so rate limits and cooldowns can be simulated safely.
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from llm.base import LLMAuthError, LLMError, LLMModelError, LLMQuotaError  # noqa: E402
from llm.groq_key_pool import analyse_rate_limit, discover_groq_keys, parse_duration  # noqa: E402
from llm.manager import LLMManager  # noqa: E402

SECRETS = {f"KEY_{i}": f"gsk_FAKE_SECRET_{i}_{'x' * 20}" for i in range(1, 5)}
SECRET_TO_ID = {v: k for k, v in SECRETS.items()}


# ------------------------------------------------------------------ fake world
class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class Resp:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)
        self.headers = headers or {}

    def json(self):
        return self._body


def ok(text="Hello!", headers=None):
    return Resp(200, {"choices": [{"message": {"role": "assistant", "content": text}}]},
                headers or {"x-ratelimit-remaining-requests": "999"})


def rate_limited(kind="TPM", wait="8s", retry_after="8", limit=6000, requested=500):
    names = {"TPM": "tokens per minute", "TPD": "tokens per day", "RPM": "requests per minute",
             "RPD": "requests per day"}
    unit = "tokens" if kind.startswith("T") else "requests"
    msg = (f"Rate limit reached for model `openai/gpt-oss-120b` in organization `org_test` service tier "
           f"`on_demand` on {unit} per {names[kind].split()[-1]} ({kind}): Limit {limit}, Used {limit - 1}, "
           f"Requested {requested}. Please try again in {wait}. Need more tokens? Upgrade.")
    headers = {"retry-after": retry_after} if retry_after else {}
    return Resp(429, {"error": {"message": msg, "type": unit, "code": "rate_limit_exceeded"}}, headers)


def unauthorized():
    return Resp(401, {"error": {"message": "Invalid API Key", "type": "invalid_request_error"}})


class FakeGroq:
    """behaviour[key_id] = function(call_number_for_that_key) -> Resp"""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = []
        self.per_key = {}
        self.lock = threading.Lock()

    def __call__(self, method, url, headers=None, json=None, timeout=None, **kw):
        auth = (headers or {}).get("Authorization", "")
        key_id = SECRET_TO_ID.get(auth.replace("Bearer ", ""), "UNKNOWN")
        with self.lock:
            self.calls.append(key_id)
            n = self.per_key.get(key_id, 0) + 1
            self.per_key[key_id] = n
        return self.behaviour.get(key_id, lambda n: ok())(n)


def make_manager(num_keys=4, fake=None):
    for name in list(os.environ):
        if name.startswith("GROQ_API_KEY"):
            del os.environ[name]
    for i in range(1, num_keys + 1):
        os.environ[f"GROQ_API_KEY_{i}"] = SECRETS[f"KEY_{i}"]
    requests.request = fake
    llm = LLMManager("groq", "", "openai/gpt-oss-120b", max_retries=1, retry_base_delay=0.001)
    clock = FakeClock()
    provider = llm._provider
    provider.pool.clock = clock
    provider.pool.sleep = clock.sleep
    provider.backoff_base = 0.01
    return llm, provider, clock


def run(func):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        result = func()
    logs = out.getvalue() + err.getvalue()
    for secret in SECRETS.values():
        assert secret not in logs, "a secret key appeared in the logs!"
    return result, logs


# ------------------------------------------------------------------------ tests
def test_parse_durations():
    assert parse_duration("2m59.56s") == 179.56
    assert parse_duration("7.66s") == 7.66
    assert abs(parse_duration("1h2m3.5s") - 3723.5) < 1e-9
    assert parse_duration("850ms") == 0.85
    assert parse_duration("2") == 2.0
    assert parse_duration("soon") is None


def test_discovery_order_duplicates_and_fallback():
    env = {"GROQ_API_KEY_10": "k10", "GROQ_API_KEY_2": "k2", "GROQ_API_KEY_1": "k1", "GROQ_API_KEY_3": "k1",
           "GROQ_API_KEY_4": " ", "OTHER": "x"}
    keys, _ = run(lambda: discover_groq_keys(env))
    assert [k for k, _ in keys] == ["KEY_1", "KEY_2", "KEY_10"], keys   # numeric order, dup + blank skipped
    assert discover_groq_keys({}, fallback_key="single") == [("KEY_1", "single")]


def test_429_rotates_to_next_key_and_sticks():
    fake = FakeGroq({"KEY_1": lambda n: rate_limited("TPM", "8s", "8")})
    llm, provider, clock = make_manager(fake=fake)
    (r1, r2), logs = run(lambda: (llm.generate("hi"), llm.generate("hi again")))
    assert r1 == r2 == "Hello!"
    assert fake.calls == ["KEY_1", "KEY_2", "KEY_2"], fake.calls          # sticky on the working key
    assert "[Groq] KEY_1 rate limited (tokens per minute)" in logs and "[Groq] Switching to KEY_2" in logs


def test_invalid_key_disabled_and_skipped():
    fake = FakeGroq({"KEY_1": lambda n: unauthorized()})
    llm, provider, clock = make_manager(fake=fake)
    _, logs = run(lambda: [llm.generate("x") for _ in range(3)])
    assert fake.calls == ["KEY_1", "KEY_2", "KEY_2", "KEY_2"], fake.calls
    assert "KEY_1 disabled for this run" in logs


def test_cooldown_expires_and_key_returns():
    fake = FakeGroq({"KEY_1": lambda n: rate_limited("RPM", "5s", "5") if n == 1 else ok(),
                     "KEY_2": lambda n: ok() if n == 1 else rate_limited("RPM", "60s", "60")})
    llm, provider, clock = make_manager(num_keys=2, fake=fake)

    def scenario():
        llm.generate("a")          # KEY_1 429 -> KEY_2 ok
        clock.now += 10            # KEY_1's 5s cooldown is over
        return llm.generate("b")   # KEY_2 429 -> KEY_1 is available again
    result, logs = run(scenario)
    assert result == "Hello!" and fake.calls == ["KEY_1", "KEY_2", "KEY_2", "KEY_1"], fake.calls
    assert "[Groq] KEY_1 available again" in logs


def test_daily_limit_uses_long_cooldown_from_message():
    fake = FakeGroq({"KEY_1": lambda n: rate_limited("TPD", "1h2m3.5s", retry_after=None)})
    llm, provider, clock = make_manager(fake=fake)
    run(lambda: llm.generate("x"))
    k1 = provider.pool.snapshot()[0]
    assert k1["status"] == "cooling" and 3723 <= k1["cooldown_until"] - clock.now <= 3725, k1


def test_all_keys_cooling_short_wait_then_success():
    fake = FakeGroq({k: (lambda n: rate_limited("RPM", "3s", "3") if n == 1 else ok()) for k in SECRETS})
    llm, provider, clock = make_manager(fake=fake)
    result, logs = run(lambda: llm.generate("x"))
    assert result == "Hello!" and "All keys temporarily unavailable" in logs
    assert any(3 <= s <= 5 for s in clock.slept), clock.slept               # waited for the earliest key
    assert len(fake.calls) == 5


def test_all_keys_cooling_long_wait_stops_cleanly():
    fake = FakeGroq({k: (lambda n: rate_limited("RPD", "2h0m0s", None)) for k in SECRETS})
    llm, provider, clock = make_manager(fake=fake)
    try:
        run(lambda: llm.generate("x"))
        raise AssertionError("expected LLMQuotaError")
    except LLMQuotaError as exc:
        assert "All 4 Groq key(s) are rate-limited" in str(exc) and "2h 00m" in str(exc), exc
    assert len(fake.calls) == 4 and not clock.slept                          # one try per key, no hammering


def test_all_keys_invalid():
    fake = FakeGroq({k: (lambda n: unauthorized()) for k in SECRETS})
    llm, provider, clock = make_manager(fake=fake)
    try:
        run(lambda: llm.generate("x"))
        raise AssertionError("expected LLMAuthError")
    except LLMAuthError as exc:
        assert "No usable Groq keys" in str(exc)
    assert len(fake.calls) == 4


def test_request_bigger_than_limit_is_not_rotated():
    fake = FakeGroq({"KEY_1": lambda n: rate_limited("TPM", "50s", "50", limit=7000, requested=12903)})
    llm, provider, clock = make_manager(fake=fake)
    try:
        run(lambda: llm.generate("x"))
        raise AssertionError("expected LLMError")
    except LLMError as exc:
        assert "about 12903 tokens" in str(exc)
    assert fake.calls == ["KEY_1"]


def test_server_error_backs_off_on_same_key():
    fake = FakeGroq({"KEY_1": lambda n: Resp(503, {"error": {"message": "busy"}}) if n < 3 else ok()})
    llm, provider, clock = make_manager(fake=fake)
    result, logs = run(lambda: llm.generate("x"))
    assert result == "Hello!" and fake.calls == ["KEY_1", "KEY_1", "KEY_1"] and len(clock.slept) == 2


def test_wrong_model_is_not_rotated():
    fake = FakeGroq({"KEY_1": lambda n: Resp(404, {"error": {"message": "The model `x` does not exist"}})})
    llm, provider, clock = make_manager(fake=fake)
    try:
        run(lambda: llm.generate("x"))
        raise AssertionError("expected LLMModelError")
    except LLMModelError:
        pass
    assert fake.calls == ["KEY_1"]


def test_proactive_rest_when_daily_requests_hit_zero():
    headers = {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "3m0s"}
    fake = FakeGroq({"KEY_1": lambda n: ok(headers=headers)})
    llm, provider, clock = make_manager(fake=fake)
    run(lambda: (llm.generate("a"), llm.generate("b")))
    assert fake.calls == ["KEY_1", "KEY_2"], fake.calls                      # no 429 needed


def test_concurrent_requests_avoid_exhausted_key():
    fake = FakeGroq({"KEY_1": lambda n: rate_limited("TPM", "30s", "30")})
    llm, provider, clock = make_manager(fake=fake)
    llm.min_interval = 0
    results, errors = [], []

    def worker():
        try:
            for _ in range(5):
                results.append(llm.generate("x"))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def scenario():
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    run(scenario)
    assert not errors and len(results) == 40, errors
    assert fake.calls.count("KEY_1") <= 8, fake.calls.count("KEY_1")        # at most one hit per thread race
    assert provider.pool.snapshot()[0]["status"] == "cooling"


def test_single_key_fallback_keeps_old_behaviour():
    for name in list(os.environ):
        if name.startswith("GROQ_API_KEY"):
            del os.environ[name]
    fake = FakeGroq({})
    requests.request = fake
    llm = LLMManager("groq", SECRETS["KEY_1"], "openai/gpt-oss-120b", max_retries=1, retry_base_delay=0.001)
    result, _ = run(lambda: llm.generate("x"))
    assert result == "Hello!" and fake.calls == ["KEY_1"] and llm._provider.pool.size == 1


def test_analyse_rate_limit_headers_only():
    info = analyse_rate_limit("rate limited", {"x-ratelimit-reset-tokens": "7.66s"})
    assert info.cooldown == 7.66


# ------------------------------------------------------------------------ runner
if __name__ == "__main__":
    original = requests.request
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {exc!r}")
        finally:
            requests.request = original
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
