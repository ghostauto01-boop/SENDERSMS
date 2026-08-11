# How to run the migration against your live database

**Time needed:** about 10 minutes, most of it the backup.
**Risk:** low. The whole migration is one transaction — if any part fails, nothing changes.

---

## First, a correction to what I told you

I said earlier that *"the app will error on startup"* without this migration. **That was wrong, and the truth is worse.** I tested it properly by building a database with the old schema and starting the app against it.

What actually happens:

- The app **starts up completely normally.** The startup error is caught and logged as a warning ([`main.py:152`](backend/app/main.py)), so you get a healthy-looking service.
- The new `auto_reply_rules` **table is auto-created** — SQLAlchemy's `create_all` does add missing *tables*.
- The new **columns are NOT added.** `create_all` never alters an existing table. So `campaigns.scheduled_start_at` and `messages.is_auto_reply` stay missing.
- Then your **Campaigns page breaks** with `no such column: campaigns.scheduled_start_at`, because every campaign query selects that column.

So the real failure isn't a loud crash at boot — it's a green deploy followed by a broken Campaigns page. That's why this is worth doing carefully rather than skipping. Sorry for the imprecise warning the first time.

---

## What you need

Your **plain** Postgres URL, from Neon's dashboard or Render → `sendsms-api` → Environment → `DATABASE_URL`. It looks like:

```
postgresql://user:password@ep-something.eu-central-1.aws.neon.tech/neondb?sslmode=require
```

> ⚠️ **Strip `+asyncpg` if it's there.** The app uses `postgresql+asyncpg://`; `psql` does not understand that and will refuse to connect. Use `postgresql://`.

### Do you have `psql`?

```bash
psql --version
```

If that fails, install the client (you do **not** need a Postgres server):

| System | Command |
| --- | --- |
| Ubuntu / Debian / WSL | `sudo apt install postgresql-client` |
| macOS | `brew install libpq && brew link --force libpq` |
| Windows | Install PostgreSQL and tick only "Command Line Tools" |

**No terminal at all?** Skip to [Option B: the Neon web console](#option-b-no-psql--neon-web-console).

---

## Option A: the normal way (psql)

### Step 1 — Set your URL

```bash
cd /path/to/SENDERSMS
export DATABASE_URL='postgresql://user:password@host/dbname?sslmode=require'
```

Quote it. Passwords contain characters your shell will otherwise mangle.

### Step 2 — Check what's actually missing (read-only, changes nothing)

```bash
psql "$DATABASE_URL" -f scripts/check_schema.sql
```

You'll get something like:

```
 item                                        | status
---------------------------------------------+----------------------------------
 campaigns.scheduled_start_at  (scheduling)  | >>> MISSING - run the migration
 messages.is_auto_reply  (auto-reply)        | >>> MISSING - run the migration
 campaigns.allow_weekends                    | OK
 campaigns.message_body                      | OK
```

If **everything says OK**, you're already migrated — stop here, nothing to do.

Note the row counts it prints at the bottom. You'll compare against them in step 5.

### Step 3 — Back up. Do not skip this.

```bash
./scripts/backup_db.sh
```

That writes a timestamped file into `backups/`. Or do it by hand:

```bash
pg_dump "$DATABASE_URL" > backup-before-migration.sql
```

Then confirm the file is real and not empty:

```bash
ls -lh backups/          # should be kilobytes at minimum, not 0
```

**Why it matters here specifically:** section 2 of this migration *merges duplicate conversation threads and deletes the leftovers*. That part is genuinely destructive by design. The transaction protects you against a crash mid-run; it does not protect you against the merge doing something you didn't want. The backup does.

### Step 4 — Run it

```bash
psql "$DATABASE_URL" -f scripts/migrate_existing_db.sql
```

Expected output — `ALTER TABLE`, `UPDATE n`, `CREATE INDEX`, and a final `COMMIT`:

```
BEGIN
ALTER TABLE
UPDATE 0
UPDATE 12
DELETE 0
CREATE INDEX
ALTER TABLE
ALTER TABLE
CREATE INDEX
CREATE TABLE
ALTER TABLE
COMMIT
```

**`COMMIT` on the last line is your success signal.** If you see `ROLLBACK` instead, nothing was changed — send me the error text.

`UPDATE 0` / `DELETE 0` are fine and expected; they just mean you had no duplicate threads to clean up.

### Step 5 — Verify

```bash
psql "$DATABASE_URL" -f scripts/check_schema.sql
```

Every row should now read `OK`, the duplicate-threads query should return **0 rows**, and your row counts should match step 2 (except `conversations`, which drops if duplicates were merged).

### Step 6 — Deploy the code

Only now push the new code, or hit **Manual Deploy** in Render for both `sendsms-api` and `sendsms-worker`.

Then check: open **Campaigns** (should load, with a Schedule button) and **Auto-Reply** (should load with an empty rule list).

---

## Option B: no psql — Neon web console

Neon has a SQL editor in the browser, which is enough.

1. Log in at [neon.tech](https://neon.tech) → your project → **SQL Editor**.
2. **Back up first.** In the Neon sidebar, use **Branches → Create branch** from your current state, and name it `before-migration-2026-08-11`. That's an instant copy-on-write snapshot you can restore from — the equivalent of a `pg_dump` and it takes seconds.
3. Open `scripts/migrate_existing_db.sql` in your editor, **copy the entire file**, paste it into the Neon SQL Editor, and press **Run**.
   - Paste the whole thing at once, including `BEGIN;` and `COMMIT;`. Running it in pieces breaks the all-or-nothing safety.
4. Then paste and run the contents of `scripts/check_schema.sql` to verify.
   - The `\echo` lines are a psql feature and may error in the web console — delete them, or just run the `SELECT` blocks.

Render also gives you a shell on paid instances (**Shell** tab), but not on free ones, so the Neon console is your path.

---

## If something goes wrong

| What you see | What it means | Fix |
| --- | --- | --- |
| `psql: command not found` | Client not installed | See the install table above |
| `invalid dsn` / `unrecognized driver` | You used the `postgresql+asyncpg://` form | Remove `+asyncpg` |
| `SSL connection required` | Neon needs TLS | Append `?sslmode=require` to the URL |
| `password authentication failed` | Wrong or stale URL | Recopy it from Neon; rotate if unsure |
| `ROLLBACK` at the end | A statement failed; **nothing changed** | Send me the error line |
| Campaigns page still 500s after deploy | App is on an old cached build, or you migrated a *different* database than the app uses | Compare the migrated URL to Render's `DATABASE_URL`, then redeploy |
| `relation "auto_reply_rules" already exists` | Harmless — the app auto-created it | Ignore; the script uses `IF NOT EXISTS` |

**Rolling back entirely:** restore your backup.

```bash
psql "$DATABASE_URL" < backup-before-migration.sql
```

Or in Neon, restore the branch you made in Option B step 2.

Then redeploy the commit *before* these features. The feature code is `b2cbbab`, so roll back to **`d3eba7c`** — in Render, use **Manual Deploy → Deploy a specific commit** and pick `d3eba7c`.

---

## Why this is manual at all

This project has **no Alembic**. The app only ever runs `Base.metadata.create_all`, which creates tables that don't exist and nothing else — it will never add a column, add a constraint, or change a type on a table that already exists.

That's fine for a fresh database (which builds itself correctly on first boot) and a trap for an existing one. Every schema change therefore gets appended to `scripts/migrate_existing_db.sql` and run by hand, once, before the matching code goes live.

**Sections 5 and 6 are the new ones** for scheduling and auto-reply. Sections 1–4 you have almost certainly already run; they're `IF NOT EXISTS` guarded, so re-running them does nothing.

If this app keeps growing, adding Alembic is worth a couple of hours — it would turn this whole document into `alembic upgrade head`. Happy to set that up whenever you want.

---

## The short version

```bash
export DATABASE_URL='postgresql://...'        # plain form, no +asyncpg

psql "$DATABASE_URL" -f scripts/check_schema.sql       # 1. what's missing?
./scripts/backup_db.sh                                 # 2. back up
psql "$DATABASE_URL" -f scripts/migrate_existing_db.sql # 3. migrate -> want COMMIT
psql "$DATABASE_URL" -f scripts/check_schema.sql       # 4. all OK?
# 5. deploy the code
```
