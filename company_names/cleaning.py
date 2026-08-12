"""Deterministic cleanup for imported company names."""

import re


_SEPARATOR_RE = re.compile(r"[_|]+")
_WHITESPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)
_SUFFIX_RE = re.compile(
    r"(?<!\w)(?i:"
    r"co\s*\.?\s*,?\s*ltd\.?|"
    r"pte\s+ltd\.?|"
    r"sdn\s+bhd\.?|"
    r"limited\.?|"
    r"gmbh\.?|"
    r"ltd\.?|"
    r"pte\.?|"
    r"co\.?)"
    r"(?=$|[^\w]|[A-Z\u00c0-\u00de])"
)


def clean_company_name(raw_name: str) -> str:
    """Remove an approved legal suffix and any corrupted trailing text."""
    name = _WHITESPACE_RE.sub(" ", _SEPARATOR_RE.sub(" ", raw_name)).strip()
    suffix = _SUFFIX_RE.search(name)
    if suffix:
        name = name[: suffix.start()]

    name = name.strip(" \t\r\n,.;:_-|/\\")
    name = _WHITESPACE_RE.sub(" ", name)
    if not name:
        raise ValueError("company name is empty after cleanup")
    return name


def normalize_lookup_key(name: str) -> str:
    """Return a case-insensitive, punctuation-neutral company lookup key."""
    cleaned = clean_company_name(name).casefold()
    return _WHITESPACE_RE.sub(" ", _NON_WORD_RE.sub(" ", cleaned)).strip()
