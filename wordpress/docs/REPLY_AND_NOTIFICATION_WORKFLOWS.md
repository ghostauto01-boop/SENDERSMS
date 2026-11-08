# Native reply-rule and notification-centre checkpoint

Implemented under `/wp-json/sender-suite/v1/`, using the same own-application cookie, origin checks, no-store responses, prepared database adapter and transaction boundary as the other native data APIs.

## Auto-reply rules

- List/create/get/update/delete rules, with source defaults, input checks, stable priority/ID ordering and SMS/email filtering.
- Validate keyword match types, nonblank replies, cooldown range and selected email sender existence. Unified negative sender IDs refer to mailbox rows; zero is not a sender.
- Evaluate contains/exact/starts/any rules, enabled state and stop-on-match ordering.
- Dry-run `POST autoreply/test` accepts JSON `body`. As in the source, it evaluates SMS rules, ignores cooldown and returns the first match with its non-contact template preview.
- Rule counters and last-triggered dates do not change during a dry run.

## If/Then automations

- List/create/get/update/delete definitions; persist conditions/actions separately as JSON and return parsed lists, never internal storage columns.
- Validate source create-time condition/action types and nonblank send-action bodies. Preserve the source's less restrictive update schema and channel handling rather than inventing a shared validation rule.
- Dry-run `POST automations/test` accepts `text` and optional `tags`, classifies the reply and evaluates enabled inbound-reply definitions in priority order. The original test endpoint evaluates both channels; this is not a claim that live inbound processing is channel-agnostic.
- Support AND/OR conditions, sentiment, intent, labels, keywords, tags and explicit catch-all conditions.
- Dry runs **do not send messages, add/remove tags, unsubscribe/delete contacts, queue follow-ups, increment trigger counters or execute any stored action**.

## Built-in reply classifier

`POST ai/classify` accepts JSON `text`. The original offline lexicons, ordered intent patterns, score/confidence rules, labels and suggestions are translated to PHP. No AI API or additional runtime server is used. It is deterministic source behavior, not a trained model or a guarantee of linguistic accuracy; its suggestions must not be mistaken for actions already taken.

Source comparisons caught and fixed Turkish-I caseless regex matching and PHP 7.4's empty-needle search behavior. Selected tests do not certify every Unicode/PCRE version or floating-point edge case.

## In-app notifications

List/page/filter browser-provider history, fetch unread/latest counts, mark one read and mark all read. Other provider records remain outside the centre API and are not modified by mark-all-read. Provider/error internals are excluded from notification DTOs.

Push subscriptions, VAPID management, browser/provider delivery and notification-emitting business jobs are **not** implemented by these centre endpoints.

## Evidence and remaining execution work

`reply-source-fixtures.json` is generated from selected original rule/notification AST and the pure original classifier. It includes 68 source/fixture operations, 108 DTO cases, 273 classification cases, 200 individual match cases and 504 condition cases. Each PHP runtime passes 1,289 comparisons, including database counts, dry-run ordering and notification read state. The mock request suite now has 192 assertions per runtime. These use a fixed clock, synthetic SQLite with monotonic committed IDs and omitted relationships; DTO adaptation models FastAPI v2 from-attributes validation, not actual HTTP.

All real inbound triggers, per-contact/channel cooldown checks, action execution, consent/suppression enforcement at send time, durable workers, provider delivery, destructive-action review and native WordPress/MySQL/browser/concurrency tests remain outstanding. The source classifier/condition engine contains permissive and sometimes surprising behavior; do not turn recommendations or saved actions into unattended destructive operations without that review.

The full WordPress migration is not finished. This checkpoint restores management and non-sending evaluation only; it does not replace the remaining delivery system.
