"""HTTP client for home-server alias storage."""

from __future__ import annotations

from dataclasses import asdict
from urllib.parse import urlsplit

import httpx

from .repository import AliasMapping, RepositoryUnavailableError


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
        except (AttributeError, ValueError, httpx.InvalidURL):
            raise RepositoryUnavailableError(
                "Alias API configuration is invalid; check ALIAS_API_URL (HTTPS) and ALIAS_API_TOKEN"
            ) from None

    def _request(self, method, payload=None):
        try:
            # Short-lived clients avoid stale connections after long idle periods.
            with httpx.Client(timeout=httpx.Timeout(15, connect=5),
                              follow_redirects=False, transport=self._transport) as client:
                response = client.request(method, f"{self._url}/aliases", json=payload,
                                          headers={"Authorization": f"Bearer {self._token}"})
            if response.status_code in {401, 403}:
                raise RepositoryUnavailableError("Alias API authentication failed; check ALIAS_API_TOKEN")
            if 300 <= response.status_code < 400:
                raise RepositoryUnavailableError("Alias API returned a redirect; check ALIAS_API_URL")
            if response.status_code != 200:
                raise RepositoryUnavailableError(
                    f"Alias API unavailable (HTTP {response.status_code}); changes were not confirmed saved"
                )
            return response.json()
        except httpx.RequestError:
            raise RepositoryUnavailableError(
                "Home alias server is unreachable; check its power and connection, then retry"
            ) from None
        except ValueError:
            raise RepositoryUnavailableError("Alias API returned an invalid response") from None

    def list_aliases(self) -> list[AliasMapping]:
        payload = self._request("GET")
        try:
            if not isinstance(payload, dict) or set(payload) != {"aliases"} or not isinstance(payload["aliases"], list):
                raise ValueError
            mappings = []
            keys = set()
            for row in payload["aliases"]:
                if not isinstance(row, dict) or set(row) != {"cleaned_alias", "alias_key", "canonical_name"}:
                    raise ValueError
                if any(not isinstance(value, str) or not value.strip() for value in row.values()):
                    raise ValueError
                if row["alias_key"] in keys:
                    raise ValueError
                keys.add(row["alias_key"])
                mappings.append(AliasMapping(**row))
            return mappings
        except (ValueError, TypeError):
            raise RepositoryUnavailableError("Alias API returned an invalid response") from None

    def upsert_aliases(self, mappings: list[AliasMapping]) -> None:
        if not mappings:
            return
        payload = self._request("PUT", {"aliases": [asdict(item) for item in mappings]})
        if (not isinstance(payload, dict) or set(payload) != {"saved"}
                or type(payload["saved"]) is not int or payload["saved"] != len(mappings)):
            raise RepositoryUnavailableError("Alias API returned an invalid response; save was not confirmed")
