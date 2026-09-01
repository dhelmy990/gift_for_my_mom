"""Deterministic cleanup for imported company names."""

import re


_SEPARATOR_RE = re.compile(r"[_|]+")
_WHITESPACE_RE = re.compile(r"\s+")
_PARENTHESIZED_RE = re.compile(r"\([^()]*\)")
_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)
_SUFFIX_RE = re.compile(
    r"(?<!\w)(?:"
    r"(?i:co\s*\.?\s*,?\s*ltd\.?|pte\s+ltd\.?|sdn\s+bhd\.?|"
    r"limited\.?|gmbh\.?|ltd\.?|pte\.?)|"
    r"(?i:co\.?)(?=$|[^\w-]))"
)


def _remove_parenthesized_segments(name: str) -> str:
    """Remove every complete parenthesized segment, including nested ones."""
    while True:
        stripped = _PARENTHESIZED_RE.sub(" ", name)
        if stripped == name:
            return name
        name = stripped


def clean_company_name(raw_name: str) -> str:
    """Remove an approved legal suffix and any corrupted trailing text."""
    name = _SEPARATOR_RE.sub(" ", raw_name)
    name = _remove_parenthesized_segments(name)
    name = _WHITESPACE_RE.sub(" ", name).strip()
    suffix = _SUFFIX_RE.search(name)
    if suffix:
        name = name[: suffix.start()]
        name = re.sub(r"[([{]\s*$", "", name)

    name = name.strip(" \t\r\n,.;:_-|/\\")
    name = _WHITESPACE_RE.sub(" ", name)
    if not name:
        raise ValueError("company name is empty after cleanup")
    return name


def normalize_lookup_key(name: str) -> str:
    """Return a case-insensitive, punctuation-neutral company lookup key."""
    cleaned = clean_company_name(name).casefold()
    key = _WHITESPACE_RE.sub(" ", _NON_WORD_RE.sub(" ", cleaned)).strip()
    if not key:
        raise ValueError("company lookup key is empty after normalization")
    return key
