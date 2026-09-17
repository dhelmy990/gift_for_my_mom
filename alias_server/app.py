"""Authenticated alias HTTP API."""

from __future__ import annotations

import hmac
from http import HTTPStatus
import json
import logging
import os
from pathlib import Path
import sqlite3

from .storage import AliasStore

MAX_BODY = 1_048_576
FIELDS = {"alias_key", "cleaned_alias", "canonical_name"}
logger = logging.getLogger(__name__)


def validate_rows(payload: object) -> list[dict[str, str]]:
    if not isinstance(payload, dict) or set(payload) != {"aliases"}:
        raise ValueError("Expected aliases object")
    rows = payload["aliases"]
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ValueError("Invalid batch")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError("Invalid fields")
        for value in row.values():
            if not isinstance(value, str) or not value.strip() or len(value) > 2000:
                raise ValueError("Invalid field value")
            if any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value):
                raise ValueError("Invalid control character")
        if row["alias_key"] in seen:
            raise ValueError("Duplicate alias key")
        seen.add(row["alias_key"])
    return rows


def create_app(database_path: Path, token: str):
    if not isinstance(token, str) or len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError("API token must contain at least 32 ASCII characters without whitespace")
    store = AliasStore(database_path)
    expected_auth = f"Bearer {token}".encode("ascii")

    def application(environ, start_response):
        def respond(status, payload, extra=()):
            body = json.dumps(payload).encode("utf-8")
            start_response(f"{status} {HTTPStatus(status).phrase}", [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff"),
                *extra,
            ])
            return [body]

        method, path = environ["REQUEST_METHOD"], environ["PATH_INFO"]
        if not (method == "GET" and path == "/health"):
            auth = environ.get("HTTP_AUTHORIZATION", "").encode("utf-8")
            if not hmac.compare_digest(auth, expected_auth):
                return respond(401, {"error": "Authentication required"}, [("WWW-Authenticate", "Bearer")])
        try:
            if method == "GET" and path == "/health":
                store.check()
                return respond(200, {"status": "ok"})
            if path != "/aliases":
                return respond(404, {"error": "Not found"})
            if method == "GET":
                return respond(200, {"aliases": store.list_aliases()})
            if method != "PUT":
                return respond(405, {"error": "Method not allowed"}, [("Allow", "GET, PUT")])
            if environ.get("CONTENT_TYPE", "").split(";")[0].strip().lower() != "application/json":
                return respond(415, {"error": "Expected application/json"})
            try:
                length = int(environ.get("CONTENT_LENGTH", ""))
            except ValueError:
                return respond(411, {"error": "Content-Length required"})
            if length < 0:
                return respond(400, {"error": "Invalid request length"})
            if length > MAX_BODY:
                return respond(413, {"error": "Request too large"})
            body = environ["wsgi.input"].read(length)
            if len(body) != length:
                return respond(400, {"error": "Incomplete request"})
            rows = validate_rows(json.loads(body))
            store.upsert_aliases(rows)
            return respond(200, {"saved": len(rows)})
        except (ValueError, UnicodeError, RecursionError):
            return respond(400, {"error": "Invalid alias batch"})
        except (sqlite3.Error, OSError) as error:
            logger.error("Alias storage failure: %s", type(error).__name__)
            return respond(503, {"error": "Alias storage unavailable"})

    return application


def from_environment():
    token = Path(os.environ["ALIAS_API_TOKEN_FILE"]).read_text().strip()
    return create_app(Path(os.environ.get("ALIAS_DATABASE_PATH", "/data/aliases.sqlite3")), token)
