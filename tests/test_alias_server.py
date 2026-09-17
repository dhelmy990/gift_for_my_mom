from pathlib import Path

import httpx
import pytest

from alias_server import app as server


TOKEN = "test-token-with-at-least-32-characters"
ROW = {"alias_key": "alias", "cleaned_alias": "Alias", "canonical_name": "Canonical"}


def client_for(path: Path, token=TOKEN):
    assert hasattr(server, "create_app"), "The authenticated alias API is not implemented"
    return httpx.Client(
        transport=httpx.WSGITransport(app=server.create_app(path, token)),
        base_url="http://localhost",
        headers={"Authorization": f"Bearer {token}"},
    )


def test_round_trip_upsert_and_persistence(tmp_path):
    path = tmp_path / "aliases.sqlite3"
    with client_for(path) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/aliases").json() == {"aliases": []}
        assert client.put("/aliases", json={"aliases": [ROW]}).json() == {"saved": 1}
        changed = {**ROW, "canonical_name": "Updated"}
        assert client.put("/aliases", json={"aliases": [changed]}).status_code == 200
    with client_for(path) as restarted:
        response = restarted.get("/aliases")
        assert response.json() == {"aliases": [changed]}
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("method", ["GET", "PUT"])
@pytest.mark.parametrize("authorization", ["", "Bearer incorrect", "Basic abc"])
def test_authentication_required(tmp_path, method, authorization):
    with client_for(tmp_path / "db") as client:
        response = client.request(method, "/aliases", headers={"Authorization": authorization})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("invalid", [
    {}, None, {**ROW, "canonical_name": " "}, {**ROW, "alias_key": 42},
    {**ROW, "extra": "field"}, {**ROW, "canonical_name": "a" * 2001},
])
def test_invalid_batch_writes_nothing(tmp_path, invalid):
    with client_for(tmp_path / "db") as client:
        assert client.put("/aliases", json={"aliases": [ROW, invalid]}).status_code == 400
        assert client.get("/aliases").json() == {"aliases": []}


def test_conflicting_duplicates_rejected(tmp_path):
    with client_for(tmp_path / "db") as client:
        response = client.put("/aliases", json={"aliases": [ROW, {**ROW, "canonical_name": "Other"}]})
        assert response.status_code == 400
        assert client.get("/aliases").json() == {"aliases": []}


def test_bad_json_and_request_limits(tmp_path):
    with client_for(tmp_path / "db") as client:
        assert client.put("/aliases", content="{", headers={"Content-Type": "application/json"}).status_code == 400
        assert client.put("/aliases", content="x").status_code == 415
        assert client.put("/aliases", json={"aliases": [ROW] * 5001}).status_code == 400
        assert client.put("/aliases", content="x" * 1_048_577,
                          headers={"Content-Type": "application/json"}).status_code == 413


def test_short_token_fails_startup(tmp_path):
    assert hasattr(server, "create_app")
    with pytest.raises(ValueError, match="32"):
        server.create_app(tmp_path / "db", "short")


def test_backup_is_readable_and_preserves_rows(tmp_path):
    path = tmp_path / "db"
    with client_for(path) as client:
        client.put("/aliases", json={"aliases": [ROW]})
    from alias_server.storage import backup_database
    backup = backup_database(path, tmp_path / "backups")
    with client_for(backup) as restored:
        assert restored.get("/aliases").json() == {"aliases": [ROW]}
