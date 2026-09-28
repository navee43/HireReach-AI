"""Guess a greeting name from an email address - never pretend it's verified.

aman@amazon.com          -> "Aman"          (inferred_from_email)
rahul.sharma@company.com -> "Rahul Sharma"  (inferred_from_email)
careers@company.com      -> "Hiring Team"   (generic_email)
a.sharma@company.com     -> "Hiring Team"   (unclear_email - only a surname)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

RECRUITMENT_WORDS = ("recruit", "talent", "campus", "university", "graduate", "internship", "staffing")
RECRUITMENT_TOKENS = {"ta", "grad", "grads", "intern", "interns", "freshers", "fresher"}
HIRING_WORDS = ("career", "hiring", "opportunit", "joinus", "workwithus", "vacanc", "resume", "applicat")
HIRING_TOKENS = {"jobs", "job", "hire", "apply", "join", "work", "cv", "cvs"}
HR_WORDS = ("humanresource", "peopleops", "peopleteam", "hrteam", "hrdept", "hrdesk", "hrops")
HR_TOKENS = {"hr", "hrd", "people", "hrm", "personnel"}
SHARED_TOKENS = {"info", "contact", "contactus", "hello", "hi", "hey", "admin", "office", "team", "support",
                 "mail", "email", "enquiry", "enquiries", "inquiry", "inquiries", "sales", "business",
                 "noreply", "no", "reply", "general", "help", "connect", "query", "queries", "india"}


@dataclass
class NameGuess:
    name: str          # full greeting name, e.g. "Rahul Sharma" or "Hiring Team"
    first_name: str    # used in "Hi {hr_first_name},"
    source: str        # inferred_from_email | generic_email | unclear_email

    @property
    def is_person(self) -> bool:
        return self.source == "inferred_from_email"


def _team(name: str, source: str = "generic_email") -> NameGuess:
    return NameGuess(name=name, first_name=name, source=source)


def guess_name(email: str) -> NameGuess:
    local = email.split("@", 1)[0].lower()
    local = local.split("+", 1)[0]                      # drop +tags
    compact = re.sub(r"[^a-z]", "", local)
    tokens = [t for t in re.split(r"[._\-]+|\d+", local) if t]

    # 1. Shared / team inboxes
    if any(w in compact for w in RECRUITMENT_WORDS) or RECRUITMENT_TOKENS & set(tokens):
        return _team("Recruitment Team")
    if any(w in compact for w in HR_WORDS) or HR_TOKENS & set(tokens):
        return _team("HR Team")
    if any(w in compact for w in HIRING_WORDS) or HIRING_TOKENS & set(tokens):
        return _team("Hiring Team")
    if tokens and all(t in SHARED_TOKENS for t in tokens):
        return _team("Hiring Team")

    # 2. Personal addresses
    alpha = [t for t in tokens if t.isalpha()]
    words = [t for t in alpha if len(t) >= 2]
    had_initial = any(len(t) == 1 for t in alpha)
    if not words:
        return _team("Hiring Team", "unclear_email")
    if len(words) == 1:
        word = words[0]
        # "a.sharma" -> only a surname is left; one long blob is probably two names glued together
        if had_initial and alpha[0] != word or len(word) > 12 or word in SHARED_TOKENS:
            return _team("Hiring Team", "unclear_email")
        return NameGuess(word.capitalize(), word.capitalize(), "inferred_from_email")
    words = words[:3]
    full = " ".join(w.capitalize() for w in words)
    return NameGuess(full, words[0].capitalize(), "inferred_from_email")
