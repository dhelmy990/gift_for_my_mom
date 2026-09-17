# Home-server alias storage

The app can use a small authenticated HTTP API backed by SQLite on Debian 13.
The database has no separate daemon. The API stays available, and the app sends
requests when processing PDFs or saving reviewed aliases. Docker also probes
readiness every five minutes, and a daily job backs up the database.

The home server must be online for reads and saves. There is no offline edit
queue. A failed save keeps the current browser session's edits for retry; a page
reload or app restart may lose those unsaved edits. If the initial read fails,
restore the connection and process the PDFs again.

## Installed service

- SSH: `ssh homeserver`
- Directory: `/home/dhelmy/company-aliases`
- Container project: `company-aliases`
- Local API: `http://127.0.0.1:8091`
- Intended public URL: `https://aliases.dhelmy.stream`
- Database: `data/aliases.sqlite3`
- API token: `secrets/api-token` (mode 600, never committed)
- Backups: `backups/`, retained for 14 days

Existing website and bot containers are separate from this service.

## Finish public access

The existing Cloudflare Tunnel is dashboard-managed. Add a **Published
application** route on that tunnel with these values:

| Setting | Value |
| --- | --- |
| Subdomain | `aliases` |
| Domain | `dhelmy.stream` |
| Path | Empty |
| Service type | HTTP |
| Service URL | `127.0.0.1:8091` |

The combined service URL is `http://127.0.0.1:8091`. Use the existing connector
on homeserver. Keep its other routes. Enable HTTPS for visitors and do not add
a rule that caches API responses. The API sends `Cache-Control: no-store` and
requires a bearer token for all alias reads/writes. An interactive Cloudflare
Access login would block the Streamlit server client; the API already provides
authentication.

No router port forwarding is needed. Check `https://aliases.dhelmy.stream/health`
returns `{"status":"ok"}`; `/aliases` without a token must return HTTP 401.

Reference: [Cloudflare published applications](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/).

## Switch Streamlit

Deploy the updated application code, then configure its server-side secrets:

```toml
ALIAS_API_URL = "https://aliases.dhelmy.stream"
ALIAS_API_TOKEN = "COPY-THE-EXISTING-SERVER-TOKEN-HERE"
ADMIN_PASSWORD = "KEEP-YOUR-EXISTING-APP-PASSWORD"
```

View the token privately in your own terminal:

```bash
ssh homeserver 'cat ~/company-aliases/secrets/api-token'
```

Do not paste it in chat. Reboot Streamlit after updating secrets. The API settings
take precedence over legacy `SUPABASE_URL` / `SUPABASE_SERVICE_KEY`. Both new
settings are required; an API error never silently falls back to Supabase. You
can remove the old settings once the deployed workflow is verified.

The old Supabase adapter/dependency remains only to support rollout rollback.
With the API configured, no Supabase connection is created.

### Reading storage errors

Storage errors now include the backend, operation, failure stage, and code
location. The same sanitized message is written to Streamlit's server logs.
Raw exception messages, credentials, company values, and response bodies are
not included in diagnostics.

- `backend=supabase`: the legacy adapter was selected. Check that
  `ALIAS_API_URL` and `ALIAS_API_TOKEN` are top-level secrets, outside any TOML
  `[section]`, and that the app has deployed the updated code. Reboot and process
  the PDFs again to replace an error retained in the current report session.
- `backend=home-api; stage=request`: the HTTP request failed. The exception
  class distinguishes, for example, `ConnectError` from `ReadTimeout`. No
  specific company is implicated by a connection failure.
- `stage=response`: the server returned an error status. HTTP 401/403 can mean
  a token or Cloudflare access problem; HTTP 502/503 can mean a tunnel or origin
  service problem. The status alone does not establish the root cause.
- `stage=decode_json` / `validate_response`: the response is not the expected
  JSON document, which can happen if a proxy serves an HTML page.
- `stage=validate_rows; row=2; field=canonical_name`: the second **stored alias**
  in the returned list has an invalid field. This is not row 2 of the uploaded
  PDF. Both adapters request rows ordered by `alias_key`.
- `stage=acknowledge_save`: the response did not confirm the submitted row
  count. Do not assume the save succeeded; retrying the same upsert is safe.

Supabase errors preserve standard database error codes such as `PGRST205` when
available, without displaying the provider's potentially sensitive message.

Process a report, verify a known alias, save a reviewed mapping, and process it
again to confirm the saved name. Only the 24 reviewed local CSV mappings were
available for the initial import; later Supabase-only changes are not recovered.

## Service operations

From `~/company-aliases` on the server:

```bash
docker compose -f deploy/compose.yaml ps
docker compose -f deploy/compose.yaml logs --tail=50 api
docker compose -f deploy/compose.yaml restart api
curl --fail http://127.0.0.1:8091/health
sh deploy/backup.sh
```

The container restarts after a host reboot. Stopping it intentionally with Compose
leaves storage in `data/` intact. Keep `data/` and `secrets/` when updating source.
The image runs as UID/GID 1000; these directories must be accessible to that user.

For future updates, copy `alias_server/`, `deploy/`, and `.dockerignore` into the
service directory, then run:

```bash
docker compose -f deploy/compose.yaml up -d --build --wait
```

Do not re-import the seed on upgrades: the importer upserts and would replace
reviewed names for matching keys.

## Backups and restore

A user crontab entry runs `sh deploy/backup.sh` daily at 03:15 in the server's
timezone. It uses SQLite's online backup API, verifies integrity, and retains
14 days. `backups/backup.log` records backup filenames or failures. Check `crontab
-l` to see the installed schedule.

Copy backups to a separate device periodically. These local backups cannot
protect against loss of the home server's disk.

To restore, stop the API, move the current database **and its `-wal`/`-shm`
sidecars if present** into a recovery directory, copy the selected backup to
`data/aliases.sqlite3`, set owner/mode to match the original, and start the API.
Never replace a live SQLite database or leave stale sidecars next to a restore.
Verify `/health` and the alias list before resuming saves.

## API contract

Authenticated calls use `Authorization: Bearer <token>` over HTTPS:

- `GET /aliases` → `{"aliases": [{"cleaned_alias": "Acme", "alias_key": "acme", "canonical_name": "Acme"}]}`
- `PUT /aliases` with the same envelope → `{"saved": 1}`. Upserts by `alias_key`;
  an entire batch either commits or fails. Concurrent edits use last-write-wins,
  matching the previous app's behavior.
- Maximum write body: 1 MiB; maximum batch: 5,000 unique keys; maximum field:
  2,000 characters. Blank values, unexpected fields, and control characters fail.
- `GET /health` is public and checks database readability without returning data.

The client uses a five-second connection timeout and a fifteen-second request
I/O timeout. It refuses redirects and HTTP URLs except loopback test connections.
If a save times out, its outcome is unknown; retrying the same upsert is safe.

For local testing, forward the port with
`ssh -N -L 18091:127.0.0.1:8091 homeserver` and use
`ALIAS_API_URL = "http://127.0.0.1:18091"` in local Streamlit secrets.
