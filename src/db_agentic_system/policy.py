from __future__ import annotations

import re


SENSITIVE_COLUMN_PATTERNS = re.compile(
    r"(^|_)(password|passwd|pwd|secret|token|api_key|ssn|credit_card|card_number|cvv|private_key)($|_)",
    re.IGNORECASE,
)

SENSITIVE_QUESTION_PATTERNS = re.compile(
    r"\b(passwords?|secrets?|tokens?|ssn|ssns|credit\s*cards?|private\s*keys?)\b",
    re.IGNORECASE,
)


def is_sensitive_column_name(column_name: str) -> bool:
    return bool(SENSITIVE_COLUMN_PATTERNS.search(column_name))


def is_sensitive_question(question: str) -> bool:
    return bool(SENSITIVE_QUESTION_PATTERNS.search(question))
