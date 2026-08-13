# Company Report Plumber

A Streamlit app for cleaning and combining company reports. It has two modes:

- **Single-report mode** cleans one uploaded report and produces a downloadable spreadsheet.
- **Collation mode** combines multiple reports, groups company-name variants under reviewed canonical names, and totals their revenue.

Collation mode stores approved name mappings in Supabase. Its review screen supports creating and renaming groups, moving names between groups, excluding inventory rows, and reviewing the final grouped totals before download. Controls and status messages use explicit labels and high-contrast colors rather than color alone.

## Local setup

Python 3.10 or newer is required. Create a virtual environment inside the project so its packages do not affect the rest of your system:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Run the app locally:

```bash
.venv/bin/python -m streamlit run app.py
```

Single-report mode works without Supabase. Collation mode needs the three secrets described in [the Supabase setup guide](docs/SUPABASE_SETUP.md). Copy `.streamlit/secrets.example.toml` to `.streamlit/secrets.toml` and replace its placeholders for local use. Never commit the resulting secrets file.

FastEmbed downloads `BAAI/bge-small-en-v1.5` on its first vector-matching run. That first run needs network access and takes longer than later runs because the model is then cached locally.

## Tests

Run the non-integration suite:

```bash
.venv/bin/python -m pytest -v
```

Run the real embedding integration test separately (it downloads/loads the FastEmbed model and verifies 384-dimensional vectors):

```bash
.venv/bin/python -m pytest -m integration tests/test_matching.py -v
```

If unrelated globally installed pytest plugins interfere in a particular local environment, retry with plugin auto-loading disabled:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -v
```

This is a troubleshooting workaround, not the standard test command.

## Supabase and reviewed mappings

Run [`supabase/schema.sql`](supabase/schema.sql) in the Supabase SQL Editor before using collation mode. The schema creates the pgvector-backed group and mapping tables, restricted service-role access, indexes, update triggers, and the atomic/idempotent review-submission function. It is safe to run the complete file again.

See [the setup guide](docs/SUPABASE_SETUP.md) for project creation, secrets, deployment, persistence verification, retries, and security details.

To validate the repository's reviewed CSV without connecting to Supabase or loading the embedding model:

```bash
.venv/bin/python scripts/seed_name_mappings.py company_name_normalization_finetuning.csv
```

After reviewing the counts, explicitly apply it with server credentials in your local environment:

```bash
export SUPABASE_URL="https://YOUR-PROJECT.supabase.co"
export SUPABASE_SERVICE_KEY="YOUR-SERVER-SIDE-SECRET-KEY"
.venv/bin/python scripts/seed_name_mappings.py company_name_normalization_finetuning.csv --apply
```

The apply operation is atomic and retry-safe. Do not put real credentials in commands saved to shell history, documentation, source files, the CSV, issues, or chat.

## Backup and security

Authorized users can download a spreadsheet-safe CSV backup of all permanent mappings from the review UI. Save periodic copies outside both Streamlit and Supabase, especially before bulk regrouping or renaming.

The app uses the Supabase service-role key only on the server. Keep it and `ADMIN_PASSWORD` in Streamlit secrets, use a separate high-entropy admin password, restrict access to the deployed app when appropriate, and rotate any credential that is exposed. The built-in failed-password throttle is per browser session, so it is not a substitute for hosting- or proxy-level access controls.
