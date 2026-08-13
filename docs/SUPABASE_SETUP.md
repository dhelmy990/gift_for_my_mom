# Supabase Setup for the Company Name Mapper

This guide sets up permanent storage for the Streamlit Community Cloud app. You do not need to install PostgreSQL or understand databases.

Plan for about 10–15 minutes. Do not paste passwords or API keys into GitHub, the app code, an issue, or chat.

## What this setup guarantees

The mappings will live in Supabase instead of Streamlit's temporary filesystem. They will therefore survive Streamlit app restarts, sleeping, and redeployments.

No hosted service can promise protection from every outage, account deletion, or provider failure. The app will include a CSV backup download for that reason.

## 1. Create a Supabase project

1. Go to <https://supabase.com/dashboard>.
2. Sign in. Using GitHub is fine.
3. Select **New project**.
4. Choose your organization.
5. Enter a project name such as `gift-for-my-mom`.
6. Generate a strong database password and save it in a password manager. The Streamlit app will not normally need this password, but you will need it to administer or restore the database.
7. Pick a region near Singapore.
8. Select the free plan if it meets your needs.
9. Select **Create new project** and wait until setup finishes.

## 2. Create the tables

This repository includes the ready-to-run schema at `supabase/schema.sql`:

1. Open your Supabase project.
2. Select **SQL Editor** in the left sidebar.
3. Select **New query**.
4. Open `supabase/schema.sql` from this repository.
5. Copy the entire file into the Supabase query editor.
6. Select **Run**.
7. Confirm the result says the command completed successfully.

Running the file creates the `vector` and `pgcrypto` extensions; the permanent
`name_groups`, `name_mappings`, and `submission_ledger` tables; 384-dimensional
vector columns and indexes; update triggers; row-level security; and the atomic
`submit_name_review` function. The schema uses `if not exists` and replaceable
functions/triggers, so run the complete file a second time to verify that it is
idempotent. Both runs should complete successfully. Do not create the objects
manually or run isolated fragments.

## 3. Copy the two server credentials

1. In Supabase, open **Project Settings**.
2. Open **API** or **Data API**. Supabase may rename this page over time.
3. Copy the **Project URL**.
4. Copy the server-side service-role secret key. Depending on the dashboard version, it may be labeled **service_role** or **Secret key**. The app requires this server-side secret.

Important: do not use the public `anon` or publishable key for the server credential. The secret/service-role key can bypass database security and must never be exposed in browser code or committed to GitHub.

## 4. Choose the app's shared admin password

Choose a password that authorized users will enter before changing permanent mappings. It should be different from your Supabase database password.

Use a unique, high-entropy password of at least 20 characters. The app rate-limits
failed attempts within each browser session, but this is not a global rate limit:
an attacker can create new sessions. Restrict access to the deployed app or add
edge authentication/rate limiting (for example through your hosting or proxy) when
you need stronger protection.

Anyone may view the deployed app, but only someone with this password should be able to submit permanent mapping changes or download the backup.

## 5. Deploy and configure Streamlit Community Cloud

1. Open <https://share.streamlit.io/>.
2. Select **Create app** and connect GitHub if prompted.
3. Select this GitHub repository, the `main` branch, and `app.py` as the entrypoint.
4. Open **Advanced settings**. Select Python 3.10 when a Python-version selector is available; the project is tested with Python 3.10.12.
5. In **Secrets**, paste the following configuration, replacing all three placeholder values:

```toml
SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
SUPABASE_SERVICE_KEY = "YOUR-SERVER-SIDE-SECRET-KEY"
ADMIN_PASSWORD = "YOUR-SEPARATE-SHARED-ADMIN-PASSWORD"
```

6. Save the settings and select **Deploy**.
7. Reboot the app if a later secrets change does not restart it automatically.

Keep the quotation marks. Do not add this configuration to GitHub.

Community Cloud automatically installs the root `requirements.txt`; confirm it is
present and still pins `fastembed==0.7.4` before deployment. Do not install FastEmbed
separately with `apt`. The first vector-matching use downloads
`BAAI/bge-small-en-v1.5` at runtime. Its cache can be lost when the app reboots or is
rescheduled, so a later first use may download it again. The integration test verifies
that this model produces the schema's required 384-dimensional vectors.

## 6. Optional local configuration

To run the app locally against the same Supabase project:

1. Create `.streamlit/secrets.toml` in the repository.
2. Paste the same three settings from the previous step.
3. Confirm `.streamlit/secrets.toml` is ignored by Git before committing anything.

The repository already ignores `.streamlit/secrets.toml` and tracks only
`.streamlit/secrets.example.toml`. Never commit the local secrets file.

## 7. Verify the connection

After the feature is deployed:

1. Open the Streamlit app.
2. Confirm it shows the database as connected.
3. Process a small report containing at least two aliases that should represent the
   same company.
4. Confirm untouched names appear under **Separate companies** and do not require
   you to create a group for each one.
5. Search for the two aliases and move both into the **Working tray**.
6. Type a final company name that does not appear in the report, then create the
   combined group.
7. Move one alias from that group back to the Working tray, return it to Separate
   companies, and then add it to the Working tray and combined group again. This
   confirms every move is reversible before saving.
8. Enter the shared admin password and select **Save mappings and show totals**.
9. Confirm the result uses the final company name you typed and combines the room
   nights and revenue for both aliases.
10. Reboot the Streamlit app from Community Cloud and process the same report again.
11. Confirm both aliases return in the saved combined group with the saved final
    company name.
12. Expand **Backup and recovery**, authorize the action with the shared admin
    password, and confirm you can download the two-column mappings CSV.
13. Confirm controls, focus indicators, and status messages remain readable in the
    browser's normal and high-contrast/forced-colors display modes.

That reboot check proves the mapping came from Supabase rather than temporary Streamlit storage.

## 8. Back up mappings

An authorized user can download the mapping backup from the app. Save occasional copies somewhere outside both Streamlit and Supabase, such as private cloud storage.

The backup has exactly two columns (`cleaned_name,canonical_title`) and is
spreadsheet-safe. Cells that could be interpreted as formulas are encoded using a
reserved, apostrophe-led marker and a URL-safe encoding of the original UTF-8 text.
The seed importer decodes only valid values carrying that exact marker. Ordinary
apostrophes—including apostrophe-prefixed formulas—remain unchanged, and original
text beginning with the reserved marker is itself encoded so restoration is
unambiguous.

Backups are especially sensible before bulk regrouping or renaming canonical groups.

## Optional: import reviewed mappings from CSV

After creating the database tables, you can validate either a reviewed CSV or a
downloaded app backup locally without connecting to Supabase or loading the embedding
model. A reviewed file must have exactly `input_text,target_text,remarks`; an exported
backup has exactly `cleaned_name,canonical_title`:

```bash
python3 scripts/seed_name_mappings.py /path/to/reviewed-mappings.csv
```

Review the reported mapping and group counts. To submit the same validated file,
first create an empty, permission-restricted `.env.seed`, then edit it to add one
`SUPABASE_URL=...` and one `SUPABASE_SERVICE_KEY=...` line. Never commit this ignored
file. Load it and opt in with `--apply`:

```bash
install -m 600 /dev/null .env.seed
# Edit .env.seed now; do not enter credentials before permissions are restricted.
set -a
source .env.seed
set +a
python3 scripts/seed_name_mappings.py /path/to/reviewed-mappings.csv --apply
unset SUPABASE_URL SUPABASE_SERVICE_KEY
# Only after a successful import; this local deletion cannot be undone.
rm .env.seed
```

This avoids putting the service key in shell history. Any downloaded two-column backup
can be used for either the dry run or `--apply`. Import is additive/upsert-based: it
creates or updates the supplied mappings but does not delete database mappings missing
from the CSV. It is therefore not a full snapshot restore. A full point-in-time restore
requires a clean Supabase project or newly created empty tables and is not automated by
this repository; this guide intentionally provides no destructive purge command.

The importer derives its request ID from the normalized logical mappings, so retrying
the same CSV after a lost response reuses the same atomic RPC identity. The app also
retains the request ID when an unchanged UI submission is retried. Supabase records
completed request IDs in `submission_ledger`; the same request and payload returns its
previous result, while reuse with a different payload is rejected. Embeddings are
generated in bounded batches of 64 while the database update remains one atomic
submission.

The importer does not need `ADMIN_PASSWORD`. Never put real credentials in this
document, the CSV, source code, shell command arguments, or Git commits. Keep the
service key in a password manager. Remove the local `.env.seed` only after a successful
import and only when you accept that local deletion is irreversible.

## Troubleshooting

### The app says Supabase is not configured

- Confirm all three secret names match exactly.
- Confirm each value is surrounded by quotation marks.
- Save the Community Cloud secrets and reboot the app.

### The app says authentication failed

- Confirm you copied the secret/service-role key, not the public anonymous key.
- If you rotated the key in Supabase, update the Streamlit secret and reboot.

### The database connects but tables are missing

- Run the complete `supabase/schema.sql` file in the Supabase SQL Editor.
- Check the SQL Editor output for the first error rather than rerunning individual lines.

### Submission says the admin password is wrong

- The password is case-sensitive.
- Confirm `ADMIN_PASSWORD` in Community Cloud Secrets contains the intended value.
- Reboot after changing it.

### Vector suggestions do not work

- Exact permanent mappings and non-vector similarity should still work.
- Check the Streamlit logs for a FastEmbed model-download or memory error.
- FastEmbed uses `BAAI/bge-small-en-v1.5`. The first model load needs outbound
  network access and can take longer because Streamlit must download and initialize
  it; later runs use the local cache. The model produces the 384-dimensional vectors
  required by the schema.

### Supabase free project is paused

- Open the Supabase dashboard and restore/unpause the project.
- The stored data should be available again after restoration, subject to Supabase's current free-plan retention terms.

## Security checklist

- Never commit `.streamlit/secrets.toml`.
- Never expose the service-role/secret key in the UI, logs, screenshots, or chat.
- Use a separate shared admin password.
- Use at least 20 high-entropy characters and restrict app access or add edge
  protection when per-session throttling is insufficient.
- Change the admin password if it is shared accidentally.
- Rotate the Supabase secret key if it is exposed.
- Download periodic mapping backups.
