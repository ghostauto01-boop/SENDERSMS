# Backing up your app

There are two completely separate things to protect, and they need different
treatment.

| What | Where it lives | If you lose it |
|---|---|---|
| **Code** (the app itself) | GitHub | Recoverable — clone it again |
| **Data** (contacts, conversations, message history) | Neon Postgres | **Gone forever** |

The code is already safe. The data is the part worth worrying about.

---

## 1. Code — already backed up ✅

Every change is committed and pushed to GitHub on the branch
`arena/019fedaf-sendersms`, and opened as
[PR #2](https://github.com/ghostauto01-boop/SENDERSMS/pull/2).

Nothing further is required. To confirm at any time:

```bash
git status            # should say "working tree clean"
git log --oneline -1  # newest commit
```

### Merging the PR (recommended)

Your work currently sits on a branch. `main` is still at the old, broken code
from before any of the fixes. Merging makes the working version the official
one:

Open [PR #2](https://github.com/ghostauto01-boop/SENDERSMS/pull/2) → **Merge
pull request** → **Confirm merge**.

> Only do this now that you have confirmed the live app works — which you have.

### Optional: an offline copy of the code

`scripts/` produces a git *bundle*: the entire repository and its full history
squeezed into one file you can copy to a USB stick or Drive.

```bash
git bundle create backups/sendersms-repo-$(date -u +%Y%m%d).bundle --all
git bundle verify backups/sendersms-repo-*.bundle   # prove it is readable
```

Restore from one with `git clone sendersms-repo-YYYYMMDD.bundle myapp`.

---

## 2. Data — you must set this up ⚠️

**Nothing is backing up your database right now.** Neon holds every contact and
every message your customers have sent you. On the free plan a Neon database
can also **expire after 30 days** of inactivity.

### Take a backup

```bash
export DATABASE_URL='postgresql://...'   # from Render -> sendsms-api -> Environment
./scripts/backup_db.sh
```

This writes a compressed, timestamped file to `backups/` and keeps the 10 most
recent, pruning older ones. It handles the Neon URL as-is — you do not need to
strip `?sslmode=...` yourself.

Requires `pg_dump`:
- macOS — `brew install libpq && brew link --force libpq`
- Ubuntu/Debian — `sudo apt install postgresql-client`
- Windows — install PostgreSQL, or use WSL

### Restore a backup

```bash
export DATABASE_URL='postgresql://...'   # the database to restore INTO
./scripts/restore_db.sh backups/sendersms-20260811-120000Z.dump
```

It shows you the target host (password redacted) and makes you type `restore`
before touching anything.

> ⚠️ **Test this once, now, into a throwaway database — not for the first time
> during an emergency.** In Neon, create a new branch or project, point
> `DATABASE_URL` at it, restore, and check your contacts are there. A backup
> you have never restored is only a hope.

### The easiest option: let Neon do it

Neon's dashboard can snapshot instantly, with no tools to install:

**Neon → your project → Branches → New Branch.** A branch is a full
point-in-time copy of the data. Made before a risky change, it is an undo
button.

---

## 3. Things that are NOT in any backup

These live only in Render's environment settings. Losing them locks you out of
your own services, and no database dump or git bundle contains them.

Store them in a password manager (Bitwarden, 1Password, or even a Google Doc
only you can see):

- `SMSGATE_USERNAME` / `SMSGATE_PASSWORD` — from the SMS Gate app, Cloud server
- `SMSGATE_WEBHOOK_SECRET` — the device Signing Key
- `SECRET_KEY` and `CREDENTIAL_ENCRYPTION_KEY`
- `DATABASE_URL` (Neon) and `REDIS_URL` (Upstash)
- `ADMIN_PASSWORD`
- Pushover user key and app token

> **`CREDENTIAL_ENCRYPTION_KEY` deserves special care.** Some stored values are
> encrypted with it. Restore a database backup without that exact key and the
> rows come back unreadable.

---

## 4. A routine that is actually realistic

- **Before any risky change** (migration, big edit): run `backup_db.sh`, or
  click a new Neon branch.
- **Weekly, if you have real customer data:** run `backup_db.sh` and copy the
  file off your laptop — Drive, Dropbox, external disk.
- **After changing any secret:** update your password manager the same minute.

`backups/` is gitignored on purpose — dumps can contain personal data belonging
to your contacts and must never be pushed to GitHub. The flip side is that
those files exist on one machine only until you copy them somewhere else.
