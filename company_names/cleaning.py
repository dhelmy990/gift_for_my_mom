"""Deterministic cleanup for imported company names."""

import re
import unicodedata


_SEPARATOR_RE = re.compile(r"[_|]+")
_WHITESPACE_RE = re.compile(r"\s+")
_PARENTHESIZED_RE = re.compile(r"\([^()]*\)")
_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)
_TYPOGRAPHY = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    **{chr(code): "-" for code in range(0x2010, 0x2016)},
    "\u200b": None, "\ufeff": None, "\u2060": None,
})
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


def normalize_company_text(value: str) -> str:
    """Canonical display/storage formatting; retain reviewed legal suffixes."""
    if not isinstance(value, str):
        raise ValueError("company name must be text")
    name = unicodedata.normalize("NFKC", value).translate(_TYPOGRAPHY)
    name = _WHITESPACE_RE.sub(" ", name).strip()
    name = unicodedata.normalize("NFKC", name.upper())
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in name):
        raise ValueError("company name contains unsupported invisible or control characters")
    if not name or not any(char.isalnum() for char in name):
        raise ValueError("company name is empty or contains no letters or numbers")
    if len(name) > 2000:
        raise ValueError("company name exceeds 2,000 characters")
    return name


def final_name_error(value: object) -> str | None:
    """Reject edits that reintroduce formatting differences after population."""
    try:
        expected = normalize_company_text(value)
    except ValueError as error:
        return str(error)
    if value != expected:
        preview = expected if len(expected) <= 120 else expected[:117] + "..."
        return (
            "Use UPPERCASE, single ordinary spaces, no leading/trailing spaces, "
            f"and standard characters. Expected: {preview!r}"
        )
    return None


def clean_company_name(raw_name: str) -> str:
    """Remove an approved legal suffix and any corrupted trailing text."""
    name = _SEPARATOR_RE.sub(" ", normalize_company_text(raw_name))
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
    return normalize_company_text(name)


def normalize_lookup_key(name: str) -> str:
    """Return a case-insensitive, punctuation-neutral company lookup key."""
    cleaned = clean_company_name(name).casefold()
    key = _WHITESPACE_RE.sub(" ", _NON_WORD_RE.sub(" ", cleaned)).strip()
    if not key:
        raise ValueError("company lookup key is empty after normalization")
    return key
