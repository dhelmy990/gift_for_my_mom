# Home server alias API implementation plan

**Goal:** Replace hosted alias database calls with a small service on homeserver.
**Architecture:** Streamlit → authenticated HTTPS → Gunicorn WSGI API → SQLite.
**Tech stack:** Python, stdlib sqlite3, Gunicorn 23.0.0, httpx 0.28.1, Docker Compose.

The user requested independent execution; implement inline on the feature branch.

## Constraints

- Preserve existing alias normalization, review, and save behavior.
- No API token in tracked files, logs, URLs, or chat.
- Keep other home-server services and public routes unchanged.
- Do not claim offline writes or recovery of aliases absent from the local CSV.

## Tasks

1. Write failing API integration tests in tests/test_alias_server.py. Use
   httpx.WSGITransport against a real temporary SQLite file. Exercise authenticated
   reads/writes, rejection without auth, atomic validation, persistence, request
   size limits, and health checks. Run `.venv/bin/python -m pytest
   tests/test_alias_server.py -q`, implement alias_server/app.py and storage.py,
   and rerun until green.
2. Write failing HTTP repository tests in tests/test_http_repository.py. Exercise
   the actual API using WSGITransport and error cases using MockTransport.
   Implement company_names/http_repository.py with list_aliases/upsert_aliases,
   integrate ALIAS_API_URL/TOKEN selection in app.py, and add a matching importer
   option. Preserve legacy configuration fallback; validate API config before use.
3. Add a Dockerfile, Compose service, SQLite backup command, and operational
   documentation under deploy/ and docs/HOME_SERVER_SETUP.md. Validate Compose,
   run the complete pytest suite, and inspect diff for secrets and scope.
4. Deploy files to ~/company-aliases on homeserver, generate a private token,
   create persistent storage, start Compose, seed only an empty database, and
   verify authorized/unauthorized calls and persistence across restart.
5. Run and verify a consistent backup, configure the daily backup job, and record
   exact Cloudflare route and Streamlit settings. Test public HTTPS if reachable;
   otherwise clearly report that the route and hosted settings are outstanding.

## Execution evidence

- API, client, configuration, importer, and real app integration tests pass.
- Independent code review found malformed URL validation could escape the error
  boundary; regression tests reproduced this and the URL validation was fixed.
- Docker service deployed at `/home/dhelmy/company-aliases`, healthy, bound only
  to 127.0.0.1:8091. Initial empty database imported 24 mappings; every mapping
  compared with the reviewed CSV after import.
- Authenticated reads and an unchanged upsert verified; unauthenticated read 401.
- Restart retained all 24 rows. First backup passed SQLite integrity_check and
  contained 24 rows. Daily user cron installed at 03:15, invoking the script via sh.
- Application HTTP client, alias preparation, save, and totals exercised against
  the live API over an SSH forward. Final mappings equal the original 24 rows.
- Public Cloudflare route and deployed Streamlit code/secrets are outstanding;
  `aliases.dhelmy.stream` did not resolve during initial verification. User was
  given the exact route settings; no Cloudflare dashboard credentials available.
