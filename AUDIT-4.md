# SMS SENDER — Audit #4 (login wall removed: one tap, no password)

Date: 2026-09-25. Scope: the three things you asked for — drop the login gate so
you are admin without typing anything, leave every existing row in the database
exactly where it is, and prove every page still works before shipping.

**Result: verified against a running stack, not just the test suite.** The real
FastAPI app booted on SQLite, served the production frontend build from the same
origin, and the real React app was driven through every route over real HTTP
with a real session cookie.

| Check | Result |
| --- | --- |
| Backend test suite | **544 passed**, 1 skipped (skip = `pgserver` not installed) |
| Frontend suite (`vitest`) | **68 passed** (13 files) |
| `tsc -b && vite build` | clean, production bundle built |
| `eslint` | **0 errors** (14 pre-existing warnings) |
| Simulator walk of the real UI | **24/24 checks pass** |
| API sweep, signed in with the button | **105 GET routes**, no 401s, no 500s |

---

## What changed

The login screen is now one button.

```
POST /api/v1/auth/admin      ← the button. No body, no credentials.
```

It hands back the operator account's session cookie and that is the whole
exchange. `POST /api/v1/auth/login` is kept for older clients and treats an
empty password the same way; a password that *is* sent is still checked against
`ADMIN_PASSWORD`, the optional Settings → Site access password, or the legacy
`LOGIN_PASSWORD`, so nothing that worked before stopped working.

Files touched:

| File | Change |
| --- | --- |
| `backend/app/api/v1/auth.py` | new `POST /auth/admin`; empty password on `/auth/login` signs in as admin; shared `_sign_in()`; `/auth/access` now reports the door as open |
| `backend/app/schemas/auth.py` | `LoginRequest.password` is optional (the button sends no body) |
| `backend/app/security/auth.py` | docstring: a session is still required, but no credential is read |
| `frontend/src/pages/LoginPage.tsx` | the whole page is the button — no username field, no password field |
| `frontend/src/hooks/useAuth.tsx` | `login()` → `loginAsAdmin()`; `passwordRequired` is `false` |
| `frontend/src/pages/SettingsPage.tsx` | Site access tab says the wall is off; the extra password is now optional only |
| `frontend/src/App.tsx` | comment: `/login` is a one-tap screen |
| `DEPLOY.md`, `START_HERE.md` | the "log in with your credentials" instructions replaced |
| `backend/tests/test_one_tap_admin_login.py` | **new**, 5 tests |
| `backend/tests/test_open_access.py`, `frontend/src/pages/SettingsPage.test.tsx` | updated to the new model |

What deliberately did **not** change: `get_current_user` still requires a valid
session cookie, so no other route, model, migration or query was touched. There
is no schema change and no data migration in this change — that is the reason
nothing can be lost.

## The simulator

`DATABASE_URL=sqlite+aiosqlite:////tmp/sim.db`, the real `app.main:app` on
`:8000`, the real production `frontend/dist` served by FastAPI itself, and the
real React app mounted on top of it, talking to that backend over HTTP.

1. **The gate itself** — `/` redirects to `/login`; the page contains a
   "Log in as admin" button, **0 password inputs and 0 inputs of any kind**.
2. **The tap** — clicking it signs in and lands on `/dashboard` with the sidebar
   and live metrics; the login button is gone.
3. **All 20 routes** render with live data, none bounce back to `/login`, none
   log a console error, none fire a failing API call:
   `/overview /dashboard /send /contacts /phone /lists /audiences /campaigns
   /sms-manager /sequences /inbox /calendar /auto-reply /automations
   /campaign-follow-ups /follow-ups /variables /templates /analytics /settings`
4. **Data on the page that owns it** — contacts `Ada…Tunde`, `Simulator
   Campaign`, `Simulator List`, the `Welcome` template, and the two inbound
   threads in the inbox (`Stop texting me`, `How much is it?`) all visible.
5. **Writing still works** — created a contact through the Contacts form and
   read it back from the database, not just from React state.
6. **Logout → gate → button** — `/auth/me` is 200 signed in, 401 after logout,
   `/contacts` gates at `/login`, and one tap gets back in.

A headless Chromium could not be used: the sandbox is missing `libnss3` /
`libnspr4` and the Debian mirror is not reachable, so the browser walk ran in
jsdom against the same live backend instead. Same components, same HTTP, same
database — no layout/pixel checking.

## Your data

Nothing was deleted, moved or re-owned. The simulator database at the end of the
run: 1 admin user, 7 contacts (5 seeded + 1 created by the inbound webhook + 1
created through the UI), 1 list with 3 members, 1 template, 1 campaign,
2 conversations, 3 messages. `test_existing_data_survives_the_one_tap_sign_in`
pins this as a regression test: data created before the change is read back by a
brand-new browser after it taps the button.

## Read this before you deploy

**The app is now open to anyone who has the address.** There is no password, so
the URL is the only thing standing between a stranger and your contacts,
campaigns and inbox. That is exactly what was asked for, and it is what shipped —
but keep the Render URL private, and if that ever stops being comfortable, the
Settings → Site access password is still there and the credential path in
`_authenticate()` is untouched, so putting a wall back is a small change.
