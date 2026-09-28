"""Read, validate, de-duplicate and group email addresses."""
from __future__ import annotations

import csv
import re
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Tuple

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
                      r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}$")

# Free/personal mailbox providers - the domain says nothing about the employer.
PERSONAL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.in", "ymail.com",
    "rocketmail.com", "outlook.com", "outlook.in", "hotmail.com", "live.com", "live.in", "msn.com",
    "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me",
    "rediffmail.com", "gmx.com", "gmx.net", "mail.com", "yandex.com", "yandex.ru", "zohomail.in",
    "tutanota.com", "hey.com",
}


class InputFileError(Exception):
    pass


def read_emails(path: Path) -> List[str]:
    """Read raw email strings from a .txt (one per line) or .csv (an 'email' column)."""
    if not path.exists():
        raise InputFileError(f"Input file not found: {path}")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        raise InputFileError(f"Input file is empty: {path}")

    if path.suffix.lower() == ".csv":
        rows = list(csv.reader(text.splitlines()))
        rows = [r for r in rows if any(cell.strip() for cell in r)]
        if not rows:
            raise InputFileError(f"Input file is empty: {path}")
        header = [h.strip().lower() for h in rows[0]]
        col = next((i for i, h in enumerate(header) if h in ("email", "emails", "e-mail", "email address",
                                                            "hr_email", "mail")), None)
        if col is not None:
            data = rows[1:]
        else:  # no recognised header: use the first column that contains an '@'
            data = rows
            col = next((i for i, cell in enumerate(rows[0]) if "@" in cell), 0)
        return [r[col].strip() for r in data if len(r) > col and r[col].strip()]

    emails: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        emails.extend(part.strip() for part in re.split(r"[,;\s]+", line) if part.strip())
    return emails


def clean_email(raw: str) -> str:
    value = raw.strip().strip("<>\"'()[]").strip()
    if value.lower().startswith("mailto:"):
        value = value[7:]
    return value.lower()


def validate_and_dedupe(raw_emails: List[str]) -> Tuple[List[str], List[str], int]:
    """Returns (valid unique emails in original order, invalid entries, duplicates removed)."""
    valid: List[str] = []
    invalid: List[str] = []
    seen = set()
    duplicates = 0
    for raw in raw_emails:
        email = clean_email(raw)
        if not EMAIL_RE.match(email) or ".." in email:
            invalid.append(raw.strip())
            continue
        if email in seen:
            duplicates += 1
            continue
        seen.add(email)
        valid.append(email)
    return valid, invalid, duplicates


def get_domain(email: str) -> str:
    return email.rsplit("@", 1)[1].lower()


def group_by_domain(emails: List[str]) -> "OrderedDict[str, List[str]]":
    groups: Dict[str, List[str]] = OrderedDict()
    for email in emails:
        groups.setdefault(get_domain(email), []).append(email)
    return groups  # type: ignore[return-value]


def is_personal_domain(domain: str) -> bool:
    return domain.lower() in PERSONAL_DOMAINS
