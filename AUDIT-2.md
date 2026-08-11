# SMS SENDER — Audit #2 (mobile UI, campaign composer, icon)

Date: 2026-08-11. Scope: the four things you asked for — responsive UI on phone
*and* desktop, writing a campaign message from scratch, a full check that
nothing is broken, and a real app icon.

**Result: everything below was verified against a running stack.** Backend
booted on SQLite, frontend served by Vite with the API proxied through it, all
12 routes exercised over real HTTP.

| Check | Result |
| --- | --- |
| Backend test suite | **206 passed** (was 195) |
| `tsc -b` typecheck | clean |
| `vite build` | clean, 5.4 s |
| All 12 SPA routes served | 200 |
| Every page module transformed by Vite | 200 (no import/syntax errors) |
| Campaign create → validate → scheduled | works, over HTTP |
| Template campaigns (regression) | still work |

---

## 1. A real bug found and fixed: campaigns texted the literal word "Hello"

This is the most important finding, and it was live.

`_send_template_message` in `backend/app/tasks/campaign_tasks.py` ended with a
fallback: if a campaign had no template attached, the body silently became the
string `"Hello"`. A campaign with no template would validate, schedule, start,
and send the word **"Hello"** to every contact in the list — no error, no
warning.

It was also encoded in the test suite:
`test_campaign_with_contacts_reaches_scheduled` built a campaign with *no
message at all* and asserted that it scheduled successfully. The test was
protecting the bug, so I corrected the test rather than the code.

**Fixed.** There is now one source of truth for "what will this campaign
actually send":

```python
CampaignService.resolve_body(campaign, template_id=None)
```

Precedence: explicit sequence-step template → the campaign's own written
message → the campaign's template. Returns `None` when there is no usable body
(whitespace-only counts as nothing). Two consumers:

- `validate_and_schedule` blocks with *"No message to send. Write a message or
  choose a template."* — so you find out at validation, not after the SMS goes out.
- `_send_template_message` **fails closed**: marks the contact `failed`, logs it,
  and sends nothing.

10 new tests in `backend/tests/test_campaign_message_body.py` cover the
precedence rules, blank/whitespace bodies, an empty template body, and a
deleted template id.

## 2. Write a campaign message from scratch

`CampaignsPage`'s create modal now has a **Write message / Use template**
toggle.

- **Write** — a textarea with clickable variable chips (`{{first_name}}` etc.)
  and a live **GSM-7/UCS-2 segment counter**, so you can see when a message
  crosses into 2 segments (and when a single emoji drops the limit from 160 to
  70 characters and doubles your cost).
- **Template** — unchanged; picking a template still works exactly as before.

Backend: new nullable `campaigns.message_body` column, exposed on
`CampaignCreate`/`CampaignUpdate`/`CampaignResponse`. Blank input is normalised
to `NULL` rather than stored as `"   "`. The UI sends `template_id` XOR
`message_body`.

**There is no Alembic in this project**, so the column was also added to
`scripts/migrate_existing_db.sql` (idempotent). Run that against your existing
Render/Neon database before deploying, or campaign creation will 500 on the
missing column.

## 3. Responsive UI

Conventions applied consistently across all 12 pages:

| Element | Behaviour |
| --- | --- |
| Page headers | wrap instead of overflowing; `text-xl` on phone, `text-2xl` from `sm:` |
| Form grids | one column on phone, two from `sm:` |
| All 8 modals | full-width **bottom sheets** on phone, centred dialogs on desktop |
| Inputs | `text-base` on phone — **stops iOS zooming in when you focus a field** |
| Buttons | `min-h-44px` on phone (Apple/Google minimum touch target) |
| Viewport | `viewport-fit=cover` for notched phones |

Modals were the worst offender: previously centred with a fixed max-width, they
could exceed the screen and had no scroll containment. They are now
`max-h-[92vh] overflow-y-auto` with a rounded top edge, which is the pattern
users expect on a phone.

Audited for the classic mobile-breakers and found none remaining: no fixed
pixel widths wider than a 320px viewport, no unwrapped tables (all 3 are inside
`overflow-x-auto`), no grids stuck at multiple columns on a phone.

`MainLayout` and `InboxPage` were already responsive and were left alone.

## 4. App icon

The old icon was a placeholder: a blue square with the letter **S**, duplicated
as a hardcoded `<div>` in both the sidebar and the login page.

The new mark is a **speech bubble with a send arrow inside it** — messaging plus
outbound SMS, which is what the app does. It is defined as vector geometry in
`scripts/make_icons.py` and rendered at 4× then downscaled, so it stays legible
at 16px in a browser tab. I checked it at 256/128/64/32/16px and on a dark
background.

Generated into `frontend/public/`:

| File | Purpose |
| --- | --- |
| `icon-192.png`, `icon-512.png` | `purpose: "any"` — rounded corners |
| `icon-maskable-192.png`, `icon-maskable-512.png` | `purpose: "maskable"` — full-bleed, safe for Android's circle/squircle crop |
| `apple-touch-icon.png` (180px) | opaque, iOS applies its own mask |
| `favicon.svg` | scalable browser tab icon |

Previously both sizes were declared `"any maskable"`, which is a common PWA
mistake — Android crops maskable icons, so a mark sized for `any` gets its edges
shaved off. They are now declared separately.

The sidebar/login letter-S divs are replaced by a shared
`frontend/src/components/BrandMark.tsx` (this repo previously had no components
directory). To change the brand, edit the geometry and re-run
`.venv/bin/python scripts/make_icons.py`.

## 5. Also fixed along the way

- **`vite.config.ts` rejected proxied hosts.** The dev server returned HTTP 403
  *"Blocked request. This host is not allowed"* behind any tunnel/preview
  hostname. Added `host: true` + `allowedHosts: true`. Dev-only; does not affect
  the production build or Render.
- The campaign `PUT` route was verified to pass `message_body` through
  (`model_dump(exclude_unset=True)` — confirmed by an HTTP round-trip, not by
  reading the code).

---

## Still outstanding — needs you

Carried over, none of these are code problems:

1. **Rotate the Upstash Redis password** — it was shared in chat and should be
   considered compromised.
2. **Run `scripts/migrate_existing_db.sql`** on the existing database before
   deploying this change (see §2).
3. **Merge PR #2.**
4. Take a database backup (`scripts/backup_db.sh`) and copy it off-machine — the
   free Neon database expires after 30 days of inactivity.
5. Set the SMS Gate app's battery setting to **Unrestricted** on the Android
   phone, or Android will eventually kill it and inbound SMS will stop.

## Minor, not worth blocking on

- `poll_status_for_ids` substring-matches the gateway `state` field.
- `gateway_settings.password_encrypted` is written but never read (credentials
  come from Render env vars).
- The Settings → Gateway tab could say that credentials are configured via
  environment variables, since there is no form for them.
