# Home server alias storage

The user authorized independent design and deployment to Debian 13 via `ssh
homeserver`. Use SQLite behind an authenticated HTTP API in a dedicated Docker
Compose project. SQLite has no background service: queries run only on requests.
The host and API must be reachable during reads and saves; this migration does
not promise operation while the host is powered off or queue offline writes.

Use GET /aliases and PUT /aliases with bearer authentication, bounded request
bodies, strict row validation, atomic batch upserts, and generic public errors.
GET /health reports readiness without company data. Bind the container's host
port to 127.0.0.1:8091 and publish through the existing Cloudflare Tunnel as
aliases.dhelmy.stream when dashboard access is available. Do not change existing
website or bot routes. Store the API token in a restricted file outside Git.

Persist the SQLite file on the server, run as a non-root user, and create daily
SQLite-consistent backups with 14-day retention. Backups on the same disk protect
against accidental edits, not disk loss. Seed the 24 reviewed local mappings
only into an empty database; never overwrite later edits during deployment.

The app prefers ALIAS_API_URL and ALIAS_API_TOKEN when either is configured and
fails clearly on incomplete configuration. Retain the existing Supabase adapter
as a rollout fallback only when neither new setting exists. The HTTP client
requires HTTPS except for loopback testing, uses timeouts, refuses redirects,
validates responses, and never includes token/response bodies in user errors.

Verify authentication, malformed requests, atomic writes, persistence across
restart, HTTP client errors, app selection, and the complete existing suite.
Deploy, read all seeded rows, save an unchanged known row, restart, read again,
and check the first backup. Public HTTPS and hosted Streamlit activation remain
explicitly incomplete until the route and hosted secrets are configured.
