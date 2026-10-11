# Campaign follow-up steps (Ads Manager)

A campaign sends one message. The follow-up chain says what happens if nobody
answers: wait N hours, check a condition, send the next message. This document
is the contract for the API, the storage and the sending engine.

Everything below lives under `/api/v1/ads`.

---

## 1. The API

| Method | Path | What it does |
| --- | --- | --- |
| POST | `/campaigns/{id}/followup-steps` | Add a step. Every field in the request is honoured. |
| GET | `/campaigns/{id}/followup-steps` | Every step on the campaign, in chain order (`{"items": [...], "steps": [...], "total": n}` — `steps` is an alias of `items`, so either spelling works). Pass `?include_inactive=false` to see only live steps. |
| GET | `/followup-steps/{step_id}` | Full details for one step, plus its pending/sent task counts. |
| PATCH | `/campaigns/{id}/followup-steps/{step_id}` | Edit any subset of a step. |
| DELETE | `/campaigns/{id}/followup-steps/{step_id}` | Soft-delete: the step stops sending, the history stays. |
| POST | `/followup-templates` | Save a reusable step. |
| GET | `/followup-templates` | List saved templates (`?channel=sms\|email\|all`). |
| GET/PATCH/DELETE | `/followup-templates/{id}` | Read, edit, retire a template. |

Removed (soft-deleted) steps are gone from the list; their `GET /followup-steps/{id}`
record still resolves, which is how history stays readable.

`PATCH /followup-steps/{step_id}` and `DELETE /followup-steps/{step_id}` remain
as aliases for older clients; the campaign-scoped routes are the ones to use,
because they 404 on a step that belongs to another campaign.

### Creating a step

```json
POST /api/v1/ads/campaigns/3/followup-steps
{
  "campaign_id": 3,
  "step_order": 1,
  "name": "24h follow-up",
  "delay_hours": 24,
  "condition": "no_reply",
  "action": "send_email",
  "subject": "Re: {{business_name}} — quick follow-up",
  "body": "Hi {{contact}},\n\nJust following up on my note yesterday..."
}
```

```json
200 OK
{
  "id": 3,
  "campaign_id": 3,
  "step_order": 1,
  "name": "24h follow-up",
  "wait_hours": 24,
  "delay_hours": 24,
  "condition": "no_reply",
  "action": "send_email",
  "subject": "Re: {{business_name}} — quick follow-up",
  "body": "Hi {{contact}},\n\nJust following up on my note yesterday...",
  "template_id": null,
  "action_value": null,
  "is_active": true,
  "created_at": "2026-10-11T12:58:00Z"
}
```

Field by field:

| Field | Notes |
| --- | --- |
| `delay_hours` | Hours to wait **before this step**, measured from the previous message (the campaign's own send for step 1, otherwise the step before it). `0`–`8760`. `wait_hours` is accepted as an alias; sending both with different values is a 422. The response always carries both names, with the same value. |
| `action` | `send_sms`, `send_email`, `add_tag`, `remove_tag`, `change_status`, `create_task`, `stop`, `suppress`, `notify`. Omitted → `send_email` on an email campaign, `send_sms` otherwise. A `send_email` step sends by email **even on an SMS campaign** — the step, not the campaign, picks the channel. |
| `subject` | Email only. Falls back to the campaign subject when empty. Supports `{{shortcode}}`. |
| `body` | The message. Supports the same `{{shortcode}}` variables as creatives, rendered **at send time** for the contact. |
| `condition` | See the table in §3. Default `no_reply`. |
| `step_order` | 1-based position in the chain. Omitted → appended after the last step. |
| `template_id` | Start from a saved template. Fields sent explicitly still win over the template. |
| `is_active` | `false` parks the step without deleting it. |

### Editing and deleting

```json
PATCH /api/v1/ads/campaigns/3/followup-steps/4
{ "delay_hours": 48, "subject": "Quick question", "is_active": false }
```

Returns the full step object. Changing `delay_hours` also moves the follow-ups
already queued for that step (a pending task's `due_at` becomes *now + the new
wait*), so "wait 24h" edited to "wait 48h" takes effect for everyone already in
the chain. Setting `is_active: true` on a removed step brings it back into the
chain (it clears the soft-delete marker).

```json
DELETE /api/v1/ads/campaigns/3/followup-steps/4
{ "ok": true, "deleted": true, "step_id": 4, "campaign_id": 3,
  "soft_deleted": true, "is_active": false, "pending_tasks_cancelled": 6 }
```

Deleting is a **soft delete** by default: the step is deactivated and marked
deleted, its queued follow-ups are cancelled immediately, and it disappears from
the campaign's step list — but the row (and any history) survives. `?hard=true`
deletes the row outright, for a step that should never have existed.

### Validation

Every rejected field is a **422** in the app's standard error envelope:

```json
{ "code": "VALIDATION_ERROR",
  "message": "Input should be 'send_sms', 'send_email', ...",
  "detail": "action: Input should be 'send_sms', 'send_email', ...",
  "field": "action", "hint": "Check the request fields and try again." }
```

- `action` / `condition` must be one of the documented values.
- `delay_hours` (and `wait_hours`) must be within `0 … 8760`.
- `delay_hours` and `wait_hours` must agree if both are sent.
- `template_id` must name an active template, otherwise 422.
- Unknown fields are ignored (they are not silently *used*, and never silently
  override a documented one).

---

## 2. The chain

Steps are **chained one at a time**, which is what makes a 3-step sequence
behave like a sequence:

1. The campaign sends its message and queues **step 1**, due `now + delay_hours(step 1)`.
2. When step 1's message goes out, **step 2** is queued at
   `step 1's send time + delay_hours(step 2)`.
3. Step 2 sends only if its own condition still holds — so a reply, an opt-out,
   a bounce or a suppression ends the chain at the step that noticed it.

There is exactly one pending task per (step, contact): the unique
`(step_id, contact_id)` row is the durable guard, so a double sweep, a retry
after a crash or two workers racing cannot send a follow-up twice.

A step that is disabled, deleted, or has no message body is cancelled with a
reason on the task (`Step removed`, `Step disabled`, `Step has no message body`)
rather than being silently dropped — the Follow-Up Center shows why.

---

## 3. Conditions

Checked against the message *before* the step and the contact's replies to it.

| Condition | Sends when |
| --- | --- |
| `no_reply` (default) | No inbound message since the previous send. |
| `replied` | There *was* a reply. |
| `positive_reply` / `negative_reply` | The latest reply classifies positive / negative, using the existing keyless AI classifier (`Message.ai_sentiment`) with the ads reply classifier as fallback. |
| `positive` / `negative` | The assignment's stored `reply_status`. |
| `opened_no_click` | Email opened, no tracked link clicked. |
| `clicked_no_reply` | A tracked link was clicked, no reply yet. |
| `opened` / `not_opened` | Email open data from the Brevo webhook. |
| `link_clicked` / `link_not_clicked` | Tracked-link data (email webhook or SMS redirect). |
| `interested` / `not_interested` / `converted` | Contact's lead status. |
| `meeting_scheduled` | A scheduled meeting exists for the contact. |
| `always` | Unconditionally, after the delay. |

Open/click data comes off the Message row for the previous send, so the same
conditions work for both channels (SMS clicks arrive from the tracked-link
redirect, email opens/clicks from Brevo).

---

## 4. Personalization

`subject` and `body` accept the same `{{shortcode}}` variables as creatives:
`{{first_name}}`, `{{last_name}}`, `{{contact}}`, `{{business_name}}`,
`{{brand_name}}`, `{{city}}`, `{{website}}`, `{{industry}}`, any imported
custom-field key, and a `{{field|fallback}}` form. Rendering happens at send
time with the contact's own values — SMS through `render_for_contact`, email
through the same renderer inside `queue_email`, so subject and body can never
disagree. `{{contact}}` is the greeting spelling (`full name` → business name →
"there").

---

## 5. Storage

```sql
ads_followup_steps        -- one row per step: wait_hours, condition, action,
                          -- subject, body, template_id, is_active, deleted_at
ads_followup_templates    -- the reusable library (usage_count tracks reuse)
ads_followup_tasks        -- one row per (step, contact), with due_at + status
```

New columns and the template table are created by
`Base.metadata.create_all` on a fresh database and by the startup
`schema_repair` (additive `ALTER TABLE`) on an existing one — no Alembic step.

---

## 6. Tests

`backend/tests/test_followup_steps_api.py` covers the API contract and the
engine: defaults, validation, edit, soft delete, the template library, chained
sends in order, a reply ending the chain, the engagement conditions, and
`{{token}}` rendering for both channels. `backend/tests/test_ads_manager.py`
keeps the surrounding audience/sending behaviour honest.
