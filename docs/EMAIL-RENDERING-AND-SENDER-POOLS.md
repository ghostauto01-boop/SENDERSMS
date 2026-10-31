# Email rendering, opt-out handling, inbox contrast & sender pools

## Message bodies and unsubscribe handling

Outbound email bodies are sent without an automatically appended unsubscribe
button, link, or text footer. The retained `apply_unsubscribe()` helper is a
no-op for compatibility with existing send paths. Bulk email can still carry
machine-readable `List-Unsubscribe` / `List-Unsubscribe-Post` headers, and
inbound unsubscribe requests continue to be honored. SMS messages are not
modified with an automatic opt-out footer either; inbound STOP handling remains
active.

The Brevo provider refuses to send when both `textContent` and `htmlContent`
are blank instead of substituting a single space. Render/provider logging
records body lengths only (never the copy), so "was a body included?" is
answerable from logs.

Legal requirements for opt-out options vary by jurisdiction and message type.
Keep applicable consent and opt-out requirements in mind when sending.

## Inbox dark-mode contrast

`frontend/src/index.css` defines `--email-*` tokens (light + `.dark`, which
the app sets from `prefers-color-scheme`, plus a direct
`prefers-color-scheme` fallback). Dark mode uses #e0e0e0 text, #b0b0b0
secondary text and #66b3ff links on #141b28/#1c2637 surfaces (≥ 4.5:1, WCAG
AA) instead of brand-blue text on dark blue. The Email Inbox list and the
rendered message body (`email-list-item`, `email-subject`,
`email-body-preview`, `email-body-content`) use these tokens; sender HTML
inherits the readable foreground in dark mode and its links take the light
blue token.

## Multi-sender pools (optional)

Both campaign systems (Campaigns page and Ads Manager) accept:

| Field | Meaning |
|---|---|
| `email_account_ids` | Unified sender ids (positive = Brevo, negative = Gmail mailbox). Empty/absent = legacy single sender. |
| `sender_distribution` | `round_robin` (default, even), `weighted` (interleaved by share), or `random` (stable per contact). |
| `email_sender_weights` | `{sender_id: share}` for the weighted strategy, mirroring creative allocations. |

Semantics:

- The campaign's primary sender is folded into the pool; a per-creative
  sender (Ads) still overrides the pool for its contacts.
- Each contact is **pinned** to one sender at assignment time
  (`ads_assignments.email_account_id` / `campaign_contacts.email_account_id`),
  exactly like their creative, so the initial send, retries and follow-ups
  all come from the same From address.
- At dispatch, an unusable pool member (disabled, burned key) is skipped in
  rotation order for the next usable member instead of stalling the campaign.
- Every message/conversation records its sender, replies are sent from the
  thread's sender, and the Email Inbox has an **All senders / per-sender**
  filter (the multi-inbox view, backed by `GET /email/inbox/conversations?account_id=`).

Everything is optional: campaigns that never touch the pool fields behave
exactly as they did before. New columns are added automatically on boot by
`schema_repair` (and are mirrored in `scripts/migrate_existing_db.sql`).
