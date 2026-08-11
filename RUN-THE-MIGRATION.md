# Run the migration — step by step

**For:** Neon (database) + Render free (hosting) — your setup.
**Time:** ~10 minutes. **Risk:** low. The whole thing is one transaction: if any part fails, nothing changes.
**You do not need to install anything.** It's all in the browser.

---

## Read this first — what changed

Your Campaigns page is **already fixed**. The app now adds missing *columns* by itself on startup, so you are **not in a broken state** and this is **not an emergency**.

What the app still cannot do for itself:

| | Handled automatically? |
| --- | --- |
| Missing **columns** (`scheduled_start_at`, `is_auto_reply`) | ✅ Yes — on every restart |
| Missing **tables** (`auto_reply_rules`) | ✅ Yes — on every restart |
| Missing **indexes** (`uq_conversation_contact`, `ix_campaigns_scheduled_start_at`) | ❌ **No — this migration** |
| **Merging duplicate chat threads** | ❌ **No — this migration** |

I verified that gap directly: after auto-repair runs, both indexes are still absent. So this migration is now about **two things only** — indexes and duplicate threads.

**What you actually lose by not running it:**

1. **Duplicate chat threads.** Without `uq_conversation_contact`, the same contact can end up with two separate conversations, so your inbox splits one person's history into two threads. If you already have splits, they stay split.
2. **Slower scheduling.** Without `ix_campaigns_scheduled_start_at`, the "which campaigns are due?" query scans the whole table every 30–60 seconds. Harmless at 5 campaigns; wasteful at 5,000.

Not urgent. Still worth doing, and it's a 10-minute job.

---

## Step 1 — Back up (60 seconds, do not skip)

Neon gives you an instant snapshot, no tools needed.

1. Log in at **[neon.tech](https://neon.tech)** → open your `sendsms` project.
2. Left sidebar → **Branches** → **New Branch** (or *Create branch*).
3. Name it: `before-migration-2026-08-11`
4. Create it.

That's a full copy-on-write snapshot of your data as it is right now. If anything goes wrong, you restore from this branch.

**Why this matters here specifically:** Section 2 of the migration *merges duplicate conversation threads and deletes the leftovers*. That part is destructive **by design**. The transaction protects you from a crash halfway through; it does **not** protect you from the merge doing something you didn't intend. Only the backup does that.

---

## Step 2 — See what's actually missing (read-only)

1. In Neon, left sidebar → **SQL Editor**.
2. Open the file **`scripts/check_schema_web.sql`** from this repo.
3. Copy the **whole file**, paste it into the editor, press **Run**.

> Use `check_schema_web.sql`, **not** `check_schema.sql`. The other one contains `\echo`, a psql-only command that errors in the browser editor.

You'll get one table back:

| kind | item | status |
| --- | --- | --- |
| COLUMN | campaigns.scheduled_start_at | OK |
| COLUMN | messages.is_auto_reply | OK |
| TABLE | auto_reply_rules | OK |
| INDEX | uq_conversation_contact … | **MISSING - run the migration** |
| INDEX | ix_campaigns_scheduled_start_at … | **MISSING - run the migration** |
| DATA | duplicate conversation threads (want 0) | OK - none |
| COUNT | contacts / conversations / messages / campaigns | *(your numbers)* |

The COLUMN and TABLE rows should already say `OK` — that's the auto-repair having done its job.

**📸 Write down the four COUNT numbers.** You'll compare them in Step 4.

**If every single row says OK and duplicates say `OK - none`** → you're fully migrated. Nothing to do. Stop here.

---

## Step 3 — Run the migration

1. Open **`scripts/migrate_existing_db.sql`** from this repo.
2. Copy the **entire file** — including the `BEGIN;` at the top and `COMMIT;` at the bottom.
3. Paste into the Neon **SQL Editor** and press **Run**.

> **Paste it all at once.** Running it in chunks breaks the all-or-nothing safety — a partial run is the one scenario that can leave you half-migrated.

It's safe to run even though your columns already exist: every statement is guarded with `IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`, so the already-done parts quietly do nothing. Re-running it is harmless.

**Success looks like:** the statements complete and the last one is `COMMIT`. Neon may show it as a series of results or just "Query executed successfully".

If you see **`ROLLBACK`** or a red error, **nothing was changed** — send me the error text and stop.

---

## Step 4 — Verify

Run **`scripts/check_schema_web.sql`** again, same as Step 2.

You want:

- Every `COLUMN`, `TABLE` and `INDEX` row → **`OK`**
- Duplicate threads → **`OK - none`**
- `contacts`, `messages`, `campaigns` counts → **identical to Step 2**
- `conversations` count → **same or lower** (lower = duplicates got merged, which is the point)

> ⚠️ If `contacts`, `messages` or `campaigns` **dropped**, something is wrong. Restore the Neon branch from Step 1 and tell me.

---

## Step 5 — Restart the app

In **Render**, for **both** `sendsms-api` and `sendsms-worker`:

**Manual Deploy → Deploy latest commit**

Then open your app and check:

- **Campaigns** loads, with a Schedule button
- **Auto-Reply** loads, with your rule list
- **Inbox** — each contact shows as **one** thread, not two

---

## If something goes wrong

| What you see | What it means | Fix |
| --- | --- | --- |
| `syntax error at or near "\"` | You ran `check_schema.sql` instead of `check_schema_web.sql` | Use the `_web` one in the browser |
| `ROLLBACK` / red error | A statement failed; **nothing changed** | Send me the error line |
| `relation "auto_reply_rules" already exists` | Harmless — the app auto-created it | Ignore; the script uses `IF NOT EXISTS` |
| `column "scheduled_start_at" ... already exists` | Harmless — the auto-repair added it | Ignore |
| `could not create unique index "uq_conversation_contact"` | You still have duplicate threads and section 2 didn't clear them | Send me the detail line; don't retry blindly |
| Counts dropped in Step 4 | Real problem | Restore the Neon branch from Step 1 |

**Full rollback:** Neon → **Branches** → your `before-migration-2026-08-11` branch → **Restore**. Then in Render, **Manual Deploy → Deploy a specific commit** → pick `d3eba7c`.

---

## Do you have a terminal instead?

If you have `psql` installed locally, it's four commands:

```bash
export DATABASE_URL='postgresql://...'                   # plain form — strip +asyncpg

psql "$DATABASE_URL" -f scripts/check_schema.sql         # 1. what's missing?
./scripts/backup_db.sh                                   # 2. back up
psql "$DATABASE_URL" -f scripts/migrate_existing_db.sql  # 3. migrate -> want COMMIT
psql "$DATABASE_URL" -f scripts/check_schema.sql         # 4. verify
```

Get the URL from Render → `sendsms-api` → Environment → `DATABASE_URL`, and **strip `+asyncpg`** — `psql` doesn't understand it. Quote the URL; passwords contain characters your shell will mangle.

---

## Why this is manual at all

This project has no Alembic. Startup runs `Base.metadata.create_all()`, which creates missing tables and nothing else, plus the new auto-repair that adds missing columns. Neither can safely infer an index or a data merge — those need a human decision about *your* data, which is why they stay in a script you run deliberately.

If the app keeps growing, adding Alembic would collapse this whole document into `alembic upgrade head`. A couple of hours' work, happy to do it whenever you want.
