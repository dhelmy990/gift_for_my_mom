# Supabase setup for company aliases

This guide creates the app's only persistent database object: `public.company_aliases`.
Plan for about 10–15 minutes. Never paste passwords or API keys into GitHub, source
files, issues, screenshots, or chat.

## 1. Create a Supabase project

1. Open <https://supabase.com/dashboard>, sign in, and select **New project**.
2. Choose an organization and a project name such as `gift-for-my-mom`.
3. Generate a strong database password and store it in a password manager.
4. Pick a suitable region and plan, then create the project.

The database password is for project administration. It is not one of the three
Streamlit secrets used by the app.

## 2. Run the schema

1. In the Supabase dashboard, open **SQL Editor** and select **New query**.
2. Open [`supabase/schema.sql`](../supabase/schema.sql) from this repository.
3. Copy the entire file into the query editor and select **Run**.
4. Confirm the query completes successfully.

The schema is safe to run again. It creates only the `company_aliases` table, its
`updated_at` trigger, and the permissions needed by the server-side service role.
The current app does not read or write the older `name_groups`, `name_mappings`, or
`submission_ledger` objects. It does not use pgvector or database RPC functions.

## 3. Verify the table and security

In **Table Editor**, open `public.company_aliases` and verify these four fields:

| Field | Expected definition |
| --- | --- |
| `alias_key` | text, primary key |
| `cleaned_alias` | text, required |
| `canonical_name` | text, required |
| `updated_at` | timestamp with time zone, required, defaults to the current time |

Then open the table's RLS or policies view and confirm row-level security is enabled.
There should be no access policy for `anon` or `authenticated`; the app accesses the
table only with the server-side service-role key. The schema grants that role select,
insert, and update access.

## 4. Configure the three secrets

In **Project Settings**, open **API** or **Data API** and copy:

- the Project URL; and
- the server-side `service_role` or secret key, not the public `anon` or publishable
  key.

Choose a separate, high-entropy admin password for approving alias changes. In
Streamlit Community Cloud, deploy `app.py` from `main`, open **Advanced settings**,
and add exactly these three secrets:

```toml
SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
SUPABASE_SERVICE_KEY = "YOUR-SERVER-SIDE-SECRET-KEY"
ADMIN_PASSWORD = "YOUR-SEPARATE-SHARED-ADMIN-PASSWORD"
```

Both `https://YOUR-PROJECT.supabase.co` and the same URL with a trailing slash are
accepted. Keep the quotation marks. Save the settings and reboot the app after any
secret change.

For local use, copy `.streamlit/secrets.example.toml` to
`.streamlit/secrets.toml` and replace the placeholders. The destination is ignored
by Git; never commit it.

## 5. Seed the 24 reviewed aliases

The repository includes the reviewed fixture at
`tests/fixtures/company_name_aliases.csv`. With `SUPABASE_URL` and
`SUPABASE_SERVICE_KEY` available in the environment, run this exact command from the
repository root:

```bash
.venv/bin/python scripts/seed_name_aliases.py --csv tests/fixtures/company_name_aliases.csv
```

Expected output:

```text
24
```

The importer validates the exact `input_text,target_text,remarks` header, normalizes
alias keys, rejects conflicting duplicates, and upserts the complete batch. Repeating
the command is safe and should still print `24`. It does not use `ADMIN_PASSWORD`.

To avoid putting credentials in shell history, place them in an ignored,
permission-restricted `.env.seed`, load them for this shell, run the exact command
above, and unset them afterward:

```bash
install -m 600 /dev/null .env.seed
# Edit .env.seed and add SUPABASE_URL=... and SUPABASE_SERVICE_KEY=...
set -a
source .env.seed
set +a
.venv/bin/python scripts/seed_name_aliases.py --csv tests/fixtures/company_name_aliases.csv
unset SUPABASE_URL SUPABASE_SERVICE_KEY
```

Keep the service key in a password manager. Delete `.env.seed` only when you have
confirmed the import succeeded and accept that deleting the local file is irreversible.

## 6. Verify the deployed workflow

1. Upload one or more reports and process them in collation mode.
2. Confirm report rows are cleaned and duplicate cleaned names are combined.
3. Confirm a name with an exact saved normalized alias key immediately receives its
   saved canonical name and is marked **Saved**.
4. For an unmatched but similar name, confirm the app may show a RapidFuzz suggestion.
   The suggestion is optional and changes nothing until **Use this suggestion** is
   selected or the final name is edited manually.
5. Review every final company name, enter `ADMIN_PASSWORD`, and select
   **Save all changes and update totals**.
6. Confirm room nights and revenue are summed under the chosen final company names.
7. Reboot the app, process the same report again, and confirm saved exact aliases are
   restored from Supabase.

## Troubleshooting

- **Supabase is not configured:** confirm all three secret names and quotation marks,
  then reboot Streamlit.
- **Authentication failed:** use the server-side service-role/secret key, and update
  Streamlit after rotating it.
- **Table missing:** run the complete current `supabase/schema.sql` file and inspect
  the first SQL Editor error.
- **Admin password rejected:** it is case-sensitive and must match `ADMIN_PASSWORD`.
- **No fuzzy suggestion:** suggestions are deliberately conservative and optional;
  enter the final name manually. Exact saved aliases remain authoritative.

## Security checklist

- Never expose or commit `.streamlit/secrets.toml` or the service-role key.
- Use a separate high-entropy admin password and rotate exposed credentials.
- Restrict access to the deployed app when appropriate. Password checks in the app
  are not a substitute for hosting- or proxy-level access controls.
- Back up the Supabase project before any manual destructive database maintenance.

## Appendix: optional cleanup of legacy objects

> **Destructive and optional:** back up the Supabase project and verify the current
> app works with `company_aliases` before running any statement below. Dropping these
> objects permanently deletes legacy grouping data. The app never runs this cleanup.

Only projects upgraded from the retired grouping/vector design may contain these
objects. In the Supabase SQL Editor, review and run the following manually if you are
certain the old data is no longer needed:

```sql
drop table if exists public.name_mappings cascade;
drop table if exists public.name_groups cascade;
drop table if exists public.submission_ledger cascade;
drop function if exists public.submit_name_review(jsonb);
drop function if exists public.purge_name_submission_ledger(interval);
drop function if exists public.valid_review_embedding(jsonb);
drop function if exists public.review_lookup_key(text);
drop function if exists public.set_updated_at();
```

The `vector` and `pgcrypto` extensions can be shared by unrelated project features,
so this guide does not recommend dropping them automatically.
