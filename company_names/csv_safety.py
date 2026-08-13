"""Reversible spreadsheet-safe encoding for exported CSV cells."""

from __future__ import annotations

import base64
import binascii


CSV_SAFE_PREFIX = "'_CNM1_"
_FORMULA_MARKERS = ("=", "+", "-", "@")


def csv_safe_cell(value: str) -> str:
    """Encode formula-like or reserved-prefix values without losing their text."""
    if value.startswith(CSV_SAFE_PREFIX) or value.lstrip().startswith(_FORMULA_MARKERS):
        payload = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")
        return CSV_SAFE_PREFIX + payload.rstrip("=")
    return value


def csv_unsafe_cell(value: str) -> str:
    """Decode only canonical values created by :func:`csv_safe_cell`."""
    if not value.startswith(CSV_SAFE_PREFIX):
        return value
    payload = value[len(CSV_SAFE_PREFIX) :]
    if not payload:
        return value
    padded = payload + "=" * (-len(payload) % 4)
    try:
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return value
    return decoded if csv_safe_cell(decoded) == value else value
