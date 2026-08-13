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

The final implementation will include a ready-to-run schema file at `supabase/schema.sql`.

Once that file exists:

1. Open your Supabase project.
2. Select **SQL Editor** in the left sidebar.
3. Select **New query**.
4. Open `supabase/schema.sql` from this repository.
5. Copy the entire file into the Supabase query editor.
6. Select **Run** once.
7. Confirm the result says the command completed successfully.

Running that file will create the vector extension, permanent group and mapping tables, indexes, security settings, and the atomic submission function. Do not invent tables manually before the schema file is available.

## 3. Copy the two server credentials

1. In Supabase, open **Project Settings**.
2. Open **API** or **Data API**. Supabase may rename this page over time.
3. Copy the **Project URL**.
4. Copy the server-side secret key. Depending on the dashboard version, it may be labeled **service_role** or **Secret key**.

Important: do not use the public `anon` or publishable key for the server credential. The secret/service-role key can bypass database security and must never be exposed in browser code or committed to GitHub.

## 4. Choose the app's shared admin password

Choose a password that authorized users will enter before changing permanent mappings. It should be different from your Supabase database password.

Use a unique, high-entropy password of at least 20 characters. The app rate-limits
failed attempts within each browser session, but this is not a global rate limit:
an attacker can create new sessions. Restrict access to the deployed app or add
edge authentication/rate limiting (for example through your hosting or proxy) when
you need stronger protection.

Anyone may view the deployed app, but only someone with this password should be able to submit permanent mapping changes or download the backup.

## 5. Configure Streamlit Community Cloud

1. Open <https://share.streamlit.io/>.
2. Find this deployed app.
3. Open its menu and select **Settings**.
4. Open **Secrets**.
5. Paste the following configuration, replacing all three placeholder values:

```toml
SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
SUPABASE_SERVICE_KEY = "YOUR-SERVER-SIDE-SECRET-KEY"
ADMIN_PASSWORD = "YOUR-SEPARATE-SHARED-ADMIN-PASSWORD"
```

6. Save the secrets.
7. Reboot the app if Streamlit does not do so automatically.

Keep the quotation marks. Do not add this configuration to GitHub.

## 6. Optional local configuration

To run the app locally against the same Supabase project:

1. Create `.streamlit/secrets.toml` in the repository.
2. Paste the same three settings from the previous step.
3. Confirm `.streamlit/secrets.toml` is ignored by Git before committing anything.

The implementation will add the necessary ignore rule. Never commit the local secrets file.

## 7. Verify the connection

After the feature is deployed:

1. Open the Streamlit app.
2. Confirm it shows the database as connected.
3. Process a small report.
4. Create a clearly labeled test group and add one test name.
5. Enter the shared admin password and submit.
6. Reboot the Streamlit app from Community Cloud.
7. Process the same test name again.
8. Confirm it returns in the saved canonical group.

That reboot check proves the mapping came from Supabase rather than temporary Streamlit storage.

## 8. Back up mappings

After implementation, an authorized user can download the mapping backup from the app. Save occasional copies somewhere outside both Streamlit and Supabase, such as private cloud storage.

The backup has exactly two columns (`cleaned_name,canonical_title`) and is
spreadsheet-safe. Cells that could be interpreted as formulas are prefixed with one
apostrophe. That apostrophe is export escaping; the seed importer reverses exactly
this pattern when a backup is restored, while preserving ordinary apostrophes.

Backups are especially sensible before bulk regrouping or renaming canonical groups.

## Optional: import reviewed mappings from CSV

After creating the database tables, you can validate a reviewed CSV locally without
connecting to Supabase or loading the embedding model:

```bash
python3 scripts/seed_name_mappings.py company_name_normalization_finetuning.csv
```

Review the reported mapping and group counts. To submit the same validated file,
provide the server credentials as environment variables and opt in with `--apply`:

```bash
export SUPABASE_URL="https://YOUR-PROJECT.supabase.co"
export SUPABASE_SERVICE_KEY="YOUR-SERVER-SIDE-SECRET-KEY"
python3 scripts/seed_name_mappings.py company_name_normalization_finetuning.csv --apply
```

The importer derives its request ID from the normalized logical mappings, so retrying
the same CSV after a lost response reuses the same atomic RPC identity. Embeddings are
generated in bounded batches of 64 while the database update remains one submission.

The importer does not need `ADMIN_PASSWORD`. Replace the placeholders only in your
local shell; never put real credentials in this document, the CSV, source code, or
Git commits.

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
- The first model load can take longer because Streamlit must download and initialize it.

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
