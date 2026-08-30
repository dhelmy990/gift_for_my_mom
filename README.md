# Company Report Plumber

A Streamlit app for cleaning company reports. Single-report mode produces a cleaned,
downloadable spreadsheet. Collation mode combines multiple reports and saves reviewed
company-name aliases in Supabase.

## How collation works

1. Upload one or more reports and process them.
2. The app cleans company names and combines duplicate cleaned rows.
3. An exact saved normalized alias is applied automatically.
4. An unmatched name defaults to its cleaned name. RapidFuzz may offer a similar saved
   alias as an optional suggestion, which can be accepted, replaced with a manual edit,
   or ignored so the unchanged cleaned name is saved.
5. After logging in with the app password, select **Save all changes and update totals**.
6. The aliases are upserted to Supabase, then room nights and revenue are summed under
   each final company name.

Supabase persistence uses one table, `company_aliases`. The current app does not use
the retired grouping, embedding, or submission-ledger database objects.

## Local setup

Python 3.10 or newer is required. Create an isolated environment and install the
dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Run the app:

```bash
.venv/bin/python -m streamlit run app.py
```

Single-report mode works without Supabase. Collation mode needs the three secrets in
[the Supabase setup guide](docs/SUPABASE_SETUP.md). Copy
`.streamlit/secrets.example.toml` to `.streamlit/secrets.toml`, replace the
placeholders, and never commit the destination file.

## Tests

Run the full suite:

```bash
.venv/bin/python -m pytest -v
```

If unrelated globally installed pytest plugins interfere, retry with:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -v
```

## Supabase and initial aliases

Run [`supabase/schema.sql`](supabase/schema.sql) in the Supabase SQL Editor. It creates
the four-field `company_aliases` table, enables row-level security, restricts browser
roles, and grants server-side service-role access. It is safe to run the complete file
again.

After setting `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`, seed the repository's 24
reviewed aliases:

```bash
.venv/bin/python scripts/seed_name_aliases.py --csv tests/fixtures/company_name_aliases.csv
```

The expected output is `24`. See [the setup guide](docs/SUPABASE_SETUP.md) for the
human-readable project, security, deployment, verification, and optional legacy
cleanup steps.

Keep the service-role key server-side. `ADMIN_PASSWORD` is the password-only login
for the entire app; use a separate high-entropy value and rotate any exposed
credential. Logging out clears report data from that browser session.
