"""HTTP client for home-server alias storage."""

from __future__ import annotations

from dataclasses import asdict
from urllib.parse import urlsplit

import httpx

from .diagnostics import storage_failure
from .repository import AliasMapping, RepositoryUnavailableError, parse_alias_rows


class HttpAliasRepository:
    def __init__(self, url: str, token: str, *, transport=None):
        try:
            url = url.strip().rstrip("/")
            if any(ord(character) < 32 or ord(character) == 127 for character in url):
                raise ValueError
            parsed = urlsplit(url)
            parsed.port  # Access validates malformed and out-of-range ports.
            httpx.URL(url)
            local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if (not parsed.hostname or (parsed.scheme != "https" and not local_http)
                    or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment):
                raise ValueError
            if not isinstance(token, str) or not token.strip() or not token.strip().isascii() or any(c.isspace() for c in token.strip()):
                raise ValueError
            self._url = url
            self._token = token.strip()
            self._transport = transport
        except (AttributeError, ValueError, httpx.InvalidURL) as error:
            raise storage_failure(
                "Alias API configuration is invalid; check ALIAS_API_URL (HTTPS) and ALIAS_API_TOKEN",
                backend="home-api", operation="configure", stage="configuration",
                location="company_names.http_repository.HttpAliasRepository.__init__", error=error,
            ) from None

    def _failure(self, message, *, operation, stage, error=None):
        method = "list_aliases" if operation == "read" else "upsert_aliases"
        if operation == "save":
            message += "; changes were not confirmed saved"
        return storage_failure(message, backend="home-api", operation=operation, stage=stage,
                               location=f"company_names.http_repository.HttpAliasRepository.{method}",
                               error=error)

    def _request(self, method, payload=None):
        operation = "read" if method == "GET" else "save"
        try:
            # Short-lived clients avoid stale connections after long idle periods.
            with httpx.Client(timeout=httpx.Timeout(15, connect=5),
                              follow_redirects=False, transport=self._transport) as client:
                response = client.request(method, f"{self._url}/aliases", json=payload,
                                          headers={"Authorization": f"Bearer {self._token}"})
        except httpx.RequestError as error:
            raise self._failure(
                "Home alias server is unreachable; check DNS, server power, and connection, then retry",
                operation=operation, stage="request", error=error,
            ) from None
        status = response.status_code
        if status in {401, 403}:
            raise self._failure(
                f"Alias API authentication/access rejected (HTTP {status}); check ALIAS_API_TOKEN and Cloudflare access rules",
                operation=operation, stage="response")
        if 300 <= status < 400:
            raise self._failure(f"Alias API returned a redirect (HTTP {status}); check ALIAS_API_URL",
                                operation=operation, stage="response")
        if status != 200:
            raise self._failure(f"Alias API unavailable (HTTP {status})",
                                operation=operation, stage="response")
        try:
            return response.json()
        except ValueError as error:
            raise self._failure("Alias API returned an invalid response: expected JSON",
                                operation=operation, stage="decode_json", error=error) from None

    def list_aliases(self) -> list[AliasMapping]:
        payload = self._request("GET")
        if not isinstance(payload, dict) or set(payload) != {"aliases"}:
            raise self._failure("Alias API returned an invalid response: expected an aliases object",
                                operation="read", stage="validate_response")
        return parse_alias_rows(payload["aliases"], backend="home-api",
                                location="company_names.http_repository.HttpAliasRepository.list_aliases")

    def upsert_aliases(self, mappings: list[AliasMapping]) -> None:
        if not mappings:
            return
        payload = self._request("PUT", {"aliases": [asdict(item) for item in mappings]})
        if (not isinstance(payload, dict) or set(payload) != {"saved"}
                or type(payload["saved"]) is not int or payload["saved"] != len(mappings)):
            raise self._failure("Alias API returned an invalid response: saved count does not match submitted rows",
                                operation="save", stage="acknowledge_save")
