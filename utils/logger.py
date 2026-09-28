"""Tiny terminal logger.

Plain ASCII output so it works in any VS Code / Windows terminal.
Anything registered with ``register_secret`` (API keys) is masked before it is
printed or written to the log file.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

_secrets: List[str] = []
_log_file: Optional[Path] = None


def register_secret(value: str) -> None:
    """Never print this value (used for API keys)."""
    if value and len(value) >= 6 and value not in _secrets:
        _secrets.append(value)


def mask(text: str) -> str:
    text = str(text)
    for secret in _secrets:
        text = text.replace(secret, "***")
    return text


def set_log_file(path: Path) -> None:
    global _log_file
    _log_file = path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n===== Run started {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
    except OSError:
        _log_file = None


def _write_log(line: str) -> None:
    if _log_file is None:
        return
    try:
        with _log_file.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%H:%M:%S} {line}\n")
    except OSError:
        pass


def info(message: str = "") -> None:
    message = mask(message)
    print(message, flush=True)
    _write_log(message)


def warn(message: str) -> None:
    message = mask(message)
    print(f"  ! {message}", flush=True)
    _write_log(f"WARNING {message}")


def error(message: str) -> None:
    message = mask(message)
    print(f"  x {message}", file=sys.stderr, flush=True)
    _write_log(f"ERROR {message}")


def debug(message: str) -> None:
    """Only goes to the log file, not the terminal."""
    _write_log(f"DEBUG {mask(message)}")


def progress(index: int, total: int, text: str) -> None:
    info(f"[{index}/{total}] {text}")


def banner(title: str) -> None:
    line = "=" * 40
    info(line)
    info(title.center(40).rstrip())
    info(line)
