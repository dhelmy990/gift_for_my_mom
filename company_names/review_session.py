"""Pure session-boundary helpers for Streamlit name reviews."""

from __future__ import annotations

import hashlib
import json
from collections.abc import MutableMapping, Sequence
from typing import Any


def _value(upload: object, field: str) -> object | None:
    if isinstance(upload, dict):
        return upload.get(field)
    return getattr(upload, field, None)


def _content_digest(upload: object) -> str | None:
    getvalue = getattr(upload, "getvalue", None)
    if callable(getvalue):
        value = getvalue()
        if isinstance(value, (bytes, bytearray, memoryview)):
            return hashlib.sha256(bytes(value)).hexdigest()
    tell = getattr(upload, "tell", None)
    seek = getattr(upload, "seek", None)
    read = getattr(upload, "read", None)
    if not all(callable(method) for method in (tell, seek, read)):
        return None
    position = tell()
    try:
        seek(0)
        value = read()
    finally:
        seek(position)
    return hashlib.sha256(value).hexdigest() if isinstance(value, bytes) else None


def compute_upload_fingerprint(mode: bool, uploaded_files: Sequence[object]) -> str:
    """Identify the current mode/files without changing upload read positions."""
    descriptors = []
    for upload in uploaded_files:
        file_id = _value(upload, "file_id")
        descriptors.append(
            {
                "name": _value(upload, "name"),
                "size": _value(upload, "size"),
                "file_id": file_id,
                "content": None if file_id is not None else _content_digest(upload),
            }
        )
    payload = json.dumps(
        {"collation_mode": bool(mode), "uploads": descriptors},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def reconcile_prepared_review(
    state: MutableMapping[str, Any], mode: bool, current_fingerprint: str
) -> object | None:
    """Return a current review, or atomically discard stale review-owned state."""
    prepared = state.get("prepared_name_review")
    stored = state.get("prepared_name_review_fingerprint")
    if mode and prepared is not None and stored == current_fingerprint:
        return prepared
    for key in list(state):
        if key in {
            "prepared_name_review",
            "prepared_name_review_fingerprint",
            "final_results",
        } or key.startswith(("name_review:", "name_search_", "name_board_", "group_title_")):
            state.pop(key, None)
    return None


def merge_custom_excluded_agent(
    options: Sequence[str], selected: Sequence[str], new_agent: str
) -> tuple[list[str], list[str]]:
    """Persist one trimmed custom exclusion without duplicates."""
    merged_options = list(dict.fromkeys(options))
    merged_selected = list(dict.fromkeys(selected))
    candidate = new_agent.strip()
    if candidate and candidate not in merged_options:
        merged_options.append(candidate)
    if candidate and candidate not in merged_selected:
        merged_selected.append(candidate)
    return merged_options, merged_selected
