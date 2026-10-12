"""
The tools an AI assistant can call through MCP.

Every tool is a thin, declarative mapping onto the app's own REST API: the
assistant gets the same validation, the same consent/opt-out checks and the same
side effects the UI gets, because it is literally the same endpoint being called
in-process. Nothing here re-implements business logic, so a rule can never drift
between "what the app does" and "what the AI does".

Adding a tool is one entry in ``TOOLS``. Anything not listed is still reachable
through ``app_api_request`` (see the bottom of this file), so the assistant is
never blocked by a missing wrapper.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Param:
    """One tool argument, and where it belongs in the HTTP request."""

    name: str
    type: str = "string"  # string | integer | number | boolean | object | array
    description: str = ""
    required: bool = False
    #: path | query | body | form | file
    where: str = "body"
    enum: list[str] | None = None
    default: Any = None
    items: str | None = None  # element type when type == "array"
    #: Documented bounds (JSON-schema ``minimum``/``maximum``). A page size above the
    #: endpoint's ceiling is a 422, so the schema must say what the ceiling is.
    minimum: int | None = None
    maximum: int | None = None
    #: True when the endpoint takes this value AS the whole request body (a bare
    #: JSON array, e.g. POST /lists/{id}/contacts), instead of a named field.
    root: bool = False

    def schema(self) -> dict:
        out: dict = {"type": self.type}
        if self.description:
            out["description"] = self.description
        if self.enum:
            out["enum"] = self.enum
        if self.type == "array" and self.items:
            out["items"] = {"type": self.items}
        if self.default is not None:
            out["default"] = self.default
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        return out


@dataclass
class Tool:
    name: str
    description: str
    method: str
    path: str
    scope: str = "read"  # read | write
    params: list[Param] = field(default_factory=list)
    group: str = "general"
    #: True when the endpoint's JSON body is mandatory even if every field is
    #: optional (FastAPI rejects a missing body with 422). An empty object is
    #: sent in that case.
    body_always: bool = False
    #: True for tools that act on ONE campaign by id. The classic campaigns and the
    #: Ads Manager number independently, so a bare id can name two campaigns; the
    #: server resolves which one (or refuses, listing the candidates) before it
    #: acts, rather than pausing/deleting whichever it happened to find first.
    by_kind: bool = False
    #: Where the action lives for an Ads Manager campaign: ``{"ads": (method,
    #: path)}``. Absent = the action does not exist for Ads Manager campaigns.
    kind_paths: dict = field(default_factory=dict)

    def input_schema(self) -> dict:
        properties = {p.name: p.schema() for p in self.params}
        required = [p.name for p in self.params if p.required]
        schema: dict = {"type": "object", "properties": properties, "additionalProperties": False}
        if required:
            schema["required"] = required
        return schema

    def title(self) -> str:
        """A human title for the tool — required by Anthropic's directory rules.

        Derived from the name so it can never drift out of sync with it:
        ``send_email_now`` becomes "Send email now".
        """
        return self.name.replace("_", " ").strip().capitalize()

    def annotations(self) -> dict:
        """What the tool does to the world, in the MCP annotation vocabulary.

        Anthropic's connector requirements make ``title`` plus the applicable
        ``readOnlyHint``/``destructiveHint`` mandatory, and ChatGPT uses the same
        hints to decide whether a call needs the user's confirmation. They are
        derived from the declared scope and HTTP method rather than written by
        hand per tool, so a new tool cannot forget them:

        * ``readOnlyHint`` — a ``read``-scoped tool on a GET (or one of the
          in-server pseudo-methods) changes nothing;
        * ``destructiveHint`` — a DELETE, or the escape hatch, which can reach
          any endpoint including deleting ones;
        * ``idempotentHint`` — repeating a GET or a PUT is safe; repeating a
          send is not;
        * ``openWorldHint`` — every tool talks to this app's own database, never
          to the open internet, so it is always False.
        """
        method = (self.method or "").upper()
        escape_hatch = method == "REQUEST"
        read_only = self.scope == "read" and method in ("GET", "GUIDE", "OPENAPI", "PREVIEW_TEMPLATE")
        return {
            "title": self.title(),
            "readOnlyHint": read_only and not escape_hatch,
            "destructiveHint": method == "DELETE" or escape_hatch,
            "idempotentHint": method in ("GET", "PUT", "GUIDE", "OPENAPI", "PREVIEW_TEMPLATE"),
            "openWorldHint": False,
        }

    def as_mcp(self, *, annotations: bool = True) -> dict:
        out = {
            "name": self.name,
            "title": self.title(),
            "description": self.description,
            "inputSchema": self.input_schema(),
        }
        if annotations:
            out["annotations"] = self.annotations()
        return out


CHANNEL = Param(
    "channel", type="string", enum=["sms", "email"], default="sms",
    description="Which channel this is for: 'sms' (default) or 'email'.", where="body",
)
LIST_ID = Param("list_id", type="integer", description="Contact list id.", where="body")


def _contact_fields(for_create: bool = False) -> list[Param]:
    """The columns an operator actually imports, in one place.

    ``phone_number`` only appears when creating: the update endpoint
    deliberately does not accept a new number, because changing a phone number
    would silently inherit the old contact's history. ``for_create`` keeps the
    tool schema honest in both directions.

    Neither channel is individually required any more. A contact needs a phone
    number *or* an email address, so the only thing the schema can honestly say
    is "at least one of these two" — the API is where that is enforced, and it
    answers 400 with the reason when both are missing.
    """
    fields = [
        Param("email", description="Email address. Required to receive email from this app.", where="body"),
        Param("first_name", where="body"),
        Param("last_name", where="body"),
        Param("business_name", where="body"),
        Param("city", where="body"),
        Param("state", where="body"),
        # No default: an unknown country stays unknown (the API stopped defaulting it to Nigeria).
        # A default advertised here is applied to every call, creates AND updates.
        Param("country", where="body"),
        Param("website", where="body"),
        Param("industry", where="body"),
        Param("lead_status", where="body"),
        Param("source", where="body"),
        Param("tags", type="array", items="string", description="Tag names.", where="body"),
    ]
    if for_create:
        # Mirrors ContactCreate: the API rejects a contact with neither channel,
        # so neither is marked required here — flagging phone_number as required
        # would make every AI client refuse to create an email-only contact,
        # which is exactly the case this app now supports.
        fields.insert(0, Param(
            "phone_number", where="body",
            description=(
                "The contact's phone number in international form, e.g. +2348012345678. "
                "Optional, but a contact needs a phone number or an email address — "
                "provide at least one."
            ),
        ))
    return fields


TOOLS: list[Tool] = [
    # ------------------------------------------------------------------ guide
    Tool(
        name="how_to_use_this_app",
        description=(
            "READ THIS FIRST. Explains the app's workflow, the SMS/email channels, consent "
            "rules and the safe order of operations (import → list → template → campaign → "
            "validate → start), plus what each tool group is for. Call it before running a "
            "campaign so you use the right channel and do not email someone who opted out."
        ),
        method="GUIDE", path="", group="guide",
    ),
    # --------------------------------------------------------------- contacts
    Tool(
        name="search_contacts",
        description="Search/list contacts. Use this before creating a contact to avoid duplicates.",
        method="GET", path="/api/v1/contacts/", group="contacts",
        params=[
            Param("search", description="Matches name, phone, email or business.", where="query"),
            Param("channel", enum=["all", "sms", "email"], default="all",
                  description="Filter by reachability: 'email' only emailable contacts.", where="query"),
            Param("email_state", enum=["emailable", "no_email", "unsubscribed", "bounced"],
                  description="Email standing of the contact.", where="query"),
            Param("lead_status", where="query"),
            Param("list_id", type="integer", description="Only contacts in this list.", where="query"),
            Param("page", type="integer", default=1, where="query"),
            Param("per_page", type="integer", default=25, minimum=1, maximum=100, where="query",
                  description="Page size, 1-100 (above 100 is refused, not clamped)."),
        ],
    ),
    Tool(
        name="get_contact",
        description="Full record for one contact, including custom fields and email engagement.",
        method="GET", path="/api/v1/contacts/{contact_id}", group="contacts",
        params=[Param("contact_id", type="integer", required=True, where="path")],
    ),
    Tool(
        name="create_contact",
        description=(
            "Create one contact. Provide a phone number, an email address, or both — a "
            "contact with neither is refused. Refuses a duplicate phone or email."
        ),
        method="POST", path="/api/v1/contacts/", scope="write", group="contacts",
        params=_contact_fields(for_create=True),
    ),
    Tool(
        name="update_contact",
        description=(
            "Change fields on an existing contact — only the fields you pass are changed. The "
            "phone number itself is fixed: the app will not move a contact's history onto a new "
            "number, so create a new contact instead."
        ),
        method="PUT", path="/api/v1/contacts/{contact_id}", scope="write", group="contacts",
        params=[Param("contact_id", type="integer", required=True, where="path")] + _contact_fields(),
    ),
    Tool(
        name="import_contacts_csv",
        description=(
            "Import many contacts from CSV text. The first line must be the header row; the app "
            "maps common headers automatically (name, phone, email, city, state, website, …) "
            "and stores anything else as a custom field usable as {{column_name}}. Safest to "
            "import straight into a list."
        ),
        method="POST", path="/api/v1/contacts/import/csv", scope="write", group="contacts",
        params=[
            Param("csv_text", type="string", required=True, where="file",
                  description="The whole CSV file as text (header row first)."),
            Param("list_id", type="integer", where="form",
                  description="Put everyone into this existing list."),
            Param("new_list_name", where="form",
                  description="…or create a new list with this name."),
            Param("skip_duplicates", type="boolean", default=True, where="form",
                  description="Skip rows whose phone/email already exists."),
            Param("tags", where="form", description="Comma-separated tags for every imported row."),
            Param(
                "column_mapping",
                description=(
                    "Optional JSON object mapping CSV headers to contact fields, e.g. "
                    '{"Phone":"phone_number","Name":"first_name"}. Omit to auto-detect.'
                ),
                where="form",
            ),
        ],
    ),
    Tool(
        name="register_sending_domain",
        description=(
            "Optionally register a domain you control (not a free-mail domain) with Brevo and get the "
            "DNS records to publish: the verification code, DKIM, and exactly ONE DMARC record. If the "
            "domain already publishes DMARC, your report address is merged into that record and you are "
            "told to REPLACE it -- never to add a second. This domain setup is for deliverability guidance; "
            "it does not block campaign launch or change the configured send route."
        ),
        method="POST", path="/api/v1/email/domains", scope="write", group="email",
        params=[
            Param("domain", required=True, where="body", description="e.g. acme-leads.io"),
            Param("rua", where="body", description="Address for DMARC aggregate reports."),
            Param("email_account_id", type="integer", where="body",
                  description="The Brevo sender to register it with (the default sender if omitted)."),
        ],
    ),
    Tool(
        name="list_sending_domains",
        description="Every registered sending domain with its status, DNS records and warm-up progress.",
        method="GET", path="/api/v1/email/domains", group="email",
    ),
    Tool(
        name="verify_sending_domain",
        description=(
            "Ask Brevo to check the published DNS records now, re-read the domain's DMARC, and -- the first "
            "time it authenticates -- start the warm-up ramp (about 5 a day in week one, 25 from week four)."
        ),
        method="POST", path="/api/v1/email/domains/{domain_id}/verify", scope="write", group="email",
        params=[Param("domain_id", type="integer", required=True, where="path")],
    ),
    Tool(
        name="validator_status",
        description=(
            "Can this server actually CONFIRM a mailbox? Reports the engine (builtin | reacher), "
            "`smtp_enabled` and -- when it is false -- `smtp_reason` (for example outbound port 25 "
            "is blocked) and what to do about it. When mailboxes cannot be confirmed every address "
            "stays 'unknown'. Validation is optional; unknown addresses can receive campaigns."
        ),
        method="GET", path="/api/v1/validator/status", group="contacts",
    ),
    Tool(
        name="verify_contacts",
        description=(
            "Queue verification of contacts (a list, a filtered set, or explicit ids) as a "
            "background job and return its id: follow it with `verification_job`. Every verdict is "
            "written onto the contact (email_verified, email_verified_at, email_confidence, "
            "email_verdict). Validation is optional for campaigns. Saved confirmed-bad results "
            "quarantine addresses; unknown results do not block sending. Contacts with no address, and ones already "
            "proven deliverable, are left out of the total."
        ),
        method="POST", path="/api/v1/validator/jobs", group="contacts", scope="write",
        body_always=True,
        params=[
            Param("scope", enum=["all", "list", "ids"], default="all", where="body"),
            Param("list_id", type="integer", where="body", description="Required when scope=list."),
            Param("contact_ids", type="array", items="integer", where="body",
                  description="Required when scope=ids (max 1000)."),
            Param("search", where="body"),
            Param("lead_status", where="body"),
            Param("channel", enum=["sms", "email"], where="body"),
            Param("email_state", where="body"),
            Param("deep", type="boolean", default=True, where="body",
                  description="Ask the mailbox (SMTP) as well as DNS. Falls back to DNS-only, and "
                              "says why, when this server cannot reach port 25."),
            Param("recheck", type="boolean", default=False, where="body",
                  description="Re-check contacts already proven deliverable."),
        ],
    ),
    Tool(
        name="verification_job",
        description=(
            "Progress of a verification job: status (queued | running | done | failed | cancelled), "
            "total, processed, valid / invalid / risky / unknown counts, and `smtp_enabled` / "
            "`smtp_reason` when the run could not confirm mailboxes."
        ),
        method="GET", path="/api/v1/validator/jobs/{job_id}", group="contacts",
        params=[Param("job_id", required=True, where="path")],
    ),
    Tool(
        name="quarantine_generated_addresses",
        description=(
            "Tag every pattern-generated (guessed, never verified) email address `pattern_generated` and "
            "`sendable:false` so they are visible and filterable. These legacy labels do not block sending; "
            "use email suppression to block an address. Never deletes anything. It is a DRY RUN "
            "unless you pass dry_run=false: look at the counts first."
        ),
        method="POST", path="/api/v1/contacts/quarantine/generated", scope="write", group="contacts",
        params=[Param("dry_run", type="boolean", default=True, where="query",
                      description="true (default) only counts; false applies the tags.")],
    ),
    Tool(
        name="export_contacts_csv",
        description=(
            "Export contacts as CSV, ONE PAGE AT A TIME. Every page reports 'returned', 'total', "
            "'truncated' and 'next_cursor'; repeat the call with that next_cursor (and the same "
            "filters) until it is null, then concatenate the 'csv' of each page — only the first "
            "carries the header row. Nothing is skipped or repeated even if contacts change "
            "meanwhile. (The old one-shot download was cut to a few dozen rows by the response "
            "limit; this is how to get all of them.) Use format='json' for rows as objects."
        ),
        method="GET", path="/api/v1/contacts/export", group="contacts",
        params=[
            Param("cursor", where="query",
                  description="The next_cursor of the previous page, unchanged. Omit for page 1."),
            Param("limit", type="integer", default=100, minimum=1, maximum=500, where="query",
                  description="Rows per page, 1-500 (above 500 is refused, not clamped)."),
            Param("format", enum=["csv", "json"], default="csv", where="query"),
            Param("search", where="query"),
            Param("lead_status", where="query"),
            Param("tag", where="query"),
            Param("email_state", where="query"),
            Param("channel", enum=["sms", "email"], where="query"),
            Param("list_id", type="integer", where="query",
                  description="Only contacts on this list."),
        ],
    ),
    Tool(
        name="set_email_opt_out",
        description="Unsubscribe a contact from EMAIL only (SMS consent is untouched).",
        method="POST", path="/api/v1/contacts/{contact_id}/email-opt-out", scope="write",
        group="contacts",
        params=[Param("contact_id", type="integer", required=True, where="path")],
    ),
    Tool(
        name="set_email_opt_in",
        description="Restore email consent for a contact who unsubscribed.",
        method="POST", path="/api/v1/contacts/{contact_id}/email-opt-in", scope="write",
        group="contacts",
        params=[Param("contact_id", type="integer", required=True, where="path")],
    ),
    # ------------------------------------------------------------------ lists
    Tool(
        name="list_contact_lists",
        description="All contact lists with their contact counts.",
        method="GET", path="/api/v1/lists/", group="lists",
    ),
    Tool(
        name="create_contact_list",
        description="Create a contact list. Campaigns are always sent to a list.",
        method="POST", path="/api/v1/lists/", scope="write", group="lists",
        params=[
            Param("name", required=True, where="query"),
            Param("description", where="query"),
        ],
    ),
    Tool(
        name="add_contacts_to_list",
        description="Add existing contacts to a list by id.",
        method="POST", path="/api/v1/lists/{list_id}/contacts", scope="write", group="lists",
        params=[
            Param("list_id", type="integer", required=True, where="path"),
            # This endpoint takes a bare JSON array as its body, not an object.
            Param("contact_ids", type="array", items="integer", required=True,
                  where="body", root=True),
        ],
    ),
    Tool(
        name="list_contacts_in_list",
        description="The contacts inside one list.",
        method="GET", path="/api/v1/lists/{list_id}/contacts", group="lists",
        params=[Param("list_id", type="integer", required=True, where="path")],
    ),
    # -------------------------------------------------------------- templates
    Tool(
        name="list_templates",
        description=(
            "Templates for one channel. An SMS template is a body; an email template also has "
            "a subject and optional HTML."
        ),
        method="GET", path="/api/v1/templates/", group="templates",
        params=[Param("channel", enum=["sms", "email"], default="sms", where="query"),
                Param("search", where="query")],
    ),
    Tool(
        name="create_template",
        description=(
            "Create a reusable template. Use {{first_name}}, {{business_name}} … for "
            "personalisation; for email you can pass html_body with images, links, buttons and "
            "tables, and 'attachments' as [{\"name\":\"quote.pdf\",\"content_base64\":\"…\"}]."
        ),
        method="POST", path="/api/v1/templates/", scope="write", group="templates",
        params=[
            Param("name", required=True, where="body"),
            Param("channel", enum=["sms", "email"], default="sms", where="body"),
            Param("body", required=True, description="The message text.", where="body"),
            Param("subject", description="Email only: the subject line.", where="body"),
            Param("html_body", description="Email only: the HTML version.", where="body"),
            Param("preheader", description="Email only: inbox preview text.", where="body"),
            Param("email_account_id", type="integer",
                  description="Email only: default sender for this template.", where="body"),
            Param("category", where="body"),
            Param("attachments", type="array", items="object",
                  description="Email only: files, each {name, content_base64, content_type}.",
                  where="body"),
        ],
    ),
    Tool(
        name="update_template",
        description="Edit a template. Only the fields you pass change.",
        method="PUT", path="/api/v1/templates/{template_id}", scope="write", group="templates",
        params=[
            Param("template_id", type="integer", required=True, where="path"),
            Param("name", where="body"),
            Param("body", where="body"),
            Param("subject", where="body"),
            Param("html_body", where="body"),
            Param("preheader", where="body"),
            Param("category", where="body"),
            Param("is_active", type="boolean", where="body"),
            Param("attachments", type="array", items="object", where="body"),
        ],
    ),
    Tool(
        name="get_template",
        description=(
            "Read one template in full — body, subject, HTML and attachments. Use it before "
            "editing a template so you change the real text instead of guessing it."
        ),
        method="GET", path="/api/v1/templates/{template_id}", group="templates",
        params=[Param("template_id", type="integer", required=True, where="path")],
    ),
    Tool(
        name="preview_template",
        description=(
            "Render a saved template the way one real contact would receive it — variables "
            "filled in, subject and HTML included. Do this (or send_test_email) and show the "
            "operator the result before sending to a list."
        ),
        method="PREVIEW_TEMPLATE", path="", group="templates",
        params=[
            Param("template_id", type="integer", required=True, where="arg"),
            Param("contact_id", type="integer", where="arg",
                  description="Whose values to fill in. Omit to use sample data."),
            Param("channel", enum=["sms", "email"], where="arg",
                  description="Overrides the template's own channel."),
        ],
    ),
    Tool(
        name="render_text_preview",
        description=(
            "Render a message you have not saved yet: pass the SMS body, or the email subject "
            "and HTML, and see exactly what goes out with the variables filled in -- for one real "
            "contact (contact_id) or with sample data. A variable that does not exist is shown as "
            "[UNKNOWN VARIABLE: name] and listed in unknown_variables instead of being silently "
            "deleted. Use it to check copy and character/segment counts before creating a template."
        ),
        method="POST", path="/api/v1/templates/preview", group="templates",
        params=[
            Param("body", required=True, where="query",
                  description="The message text (for email, the plain-text fallback)."),
            Param("subject", where="query", description="Email only."),
            Param("html_body", where="query", description="Email only: the HTML version."),
            Param("channel", enum=["sms", "email"], default="sms", where="query"),
            Param("contact_id", type="integer", where="query",
                  description="Render for this contact: their values, imported columns and the "
                              "operator's fallbacks, exactly as a send would. Omit for sample data."),
            # Overrides only: with no default, a value is sent only when the caller gives one.
            # (A default like first_name=John sent on every call would beat the contact's own.)
            Param("first_name", where="query", description="Override (sample data when no contact)."),
            Param("last_name", where="query"),
            Param("business_name", where="query"),
            Param("city", where="query"),
            Param("website", where="query"),
        ],
    ),
    # -------------------------------------------------------------- campaigns
    Tool(
        name="list_campaigns",
        description=(
            "Every campaign in the account, from BOTH campaign systems: the classic campaigns "
            "(kind 'campaign', also spelled 'legacy') and the Ads Manager campaigns (kind "
            "'ads'). Ids are only unique within a kind — campaign 2 and ads campaign 2 are "
            "different campaigns — so always keep an id together with its 'kind' and pass both "
            "to get_campaign. Rows carry channel, status, is_live and sent/delivered/failed/"
            "replied counts. 'total' is how many rows match; 'next_page' is null on the last "
            "page. Filters: kind, channel, status (exact, per system: classic 'running' = Ads "
            "'active'), live, search."
        ),
        method="GET", path="/api/v1/overview/campaigns", group="campaigns",
        params=[
            Param("kind", enum=["campaign", "legacy", "ads"], where="query",
                  description="Restrict to one system. Omit for both."),
            Param("channel", enum=["sms", "email", "all"], where="query",
                  description="Omit (or 'all') for both channels."),
            Param("status", where="query"),
            Param("live", type="boolean", where="query",
                  description="true = only campaigns that are running/scheduled/paused."),
            Param("search", where="query"),
            Param("page", type="integer", default=1, where="query"),
            Param("per_page", type="integer", default=25, minimum=1, maximum=200, where="query",
                  description="Page size, 1-200 (above 200 is refused, not clamped)."),
        ],
    ),
    Tool(
        name="get_campaign",
        description=(
            "One campaign's full definition and results. Pass the 'kind' the id was listed "
            "with (campaign | ads): the two systems number their campaigns independently, so "
            "an id that exists in both is refused as ambiguous (the error lists the candidates) "
            "instead of guessed. An id that exists in only one system resolves without 'kind'."
        ),
        method="GET", path="/api/v1/overview/campaigns/{campaign_id}", group="campaigns",
        params=[
            Param("campaign_id", type="integer", required=True, where="path"),
            Param("kind", enum=["campaign", "legacy", "ads"], where="query",
                  description="Which system the id belongs to (see list_campaigns)."),
        ],
    ),
    Tool(
        name="create_campaign",
        description=(
            "Create a campaign (draft). Pick ONE channel: 'sms' or 'email'. Provide the audience "
            "(list_id) and the message — either message_body, or template_id to use a saved "
            "template. Email campaigns also take subject, html_body and email_account_id. "
            "Nothing is sent until you start it (or schedule it for a time). Optional daily, "
            "hourly and per-minute caps, send hours, weekend access and min/max delays belong to "
            "this campaign only; leave them unset for unrestricted pacing. Direct sends are not "
            "limited by campaign controls."
        ),
        method="POST", path="/api/v1/campaigns/", scope="write", group="campaigns",
        params=[
            Param("name", required=True, where="body"),
            Param("channel", enum=["sms", "email"], default="sms", required=True, where="body"),
            Param("list_id", type="integer", description="Audience list id.", where="body"),
            Param("message_body", description="The SMS/email text (or leave blank to use a template).",
                  where="body"),
            Param("template_id", type="integer", where="body"),
            Param("subject", description="Email only: subject line.", where="body"),
            Param("html_body", description="Email only: HTML version.", where="body"),
            Param("email_account_id", type="integer",
                  description="Email only: unified sender id (positive Brevo, negative connected Gmail); default if omitted.",
                  where="body"),
            Param("fallback_email_account_id", type="integer",
                  description="Optional Brevo or Gmail sender id tried if the primary provider fails.",
                  where="body"),
            Param("daily_limit", type="integer", description="Optional daily cap for this campaign only.", where="body"),
            Param("hourly_limit", type="integer", description="Optional hourly cap for this campaign only.", where="body"),
            Param("per_minute_limit", type="integer", description="Optional per-minute cap for this campaign only.", where="body"),
            Param("min_delay", type="integer", description="Optional minimum delay between sends, in seconds.", where="body"),
            Param("max_delay", type="integer", description="Optional maximum delay between sends, in seconds.", where="body"),
            Param("send_start_hour", type="integer", minimum=0, maximum=23,
                  description="Optional local hour to begin sending (0-23); leave unset for 24/7.", where="body"),
            Param("send_end_hour", type="integer", minimum=0, maximum=23,
                  description="Optional local hour to stop sending (0-23); leave unset for 24/7.", where="body"),
            Param("allow_weekends", type="boolean", default=True,
                  description="Allow weekend sends for this campaign.", where="body"),
            Param("scheduled_start_at", description="ISO-8601 time to launch automatically.",
                  where="body"),
            Param("description", where="body"),
        ],
    ),
    Tool(
        name="update_campaign",
        description=(
            "Edit a campaign that has not started sending yet. Optional caps, delays, and send "
            "windows are campaign-scoped; leave them unset for unrestricted sending."
        ),
        method="PUT", path="/api/v1/campaigns/{campaign_id}", scope="write", group="campaigns",
        by_kind=True,
        params=[
            Param("campaign_id", type="integer", required=True, where="path"),
            Param("kind", enum=["campaign", "legacy", "ads"], where="meta",
                  description="Which campaign system the id belongs to (see list_campaigns). "
                              "Optional when the id exists in only one system; required when "
                              "it exists in both (the call is refused, listing the candidates)."),
            Param("name", where="body"),
            Param("list_id", type="integer", where="body"),
            Param("message_body", where="body"),
            Param("template_id", type="integer", where="body"),
            Param("subject", where="body"),
            Param("html_body", where="body"),
            Param("email_account_id", type="integer",
                  description="Email sender id: positive Brevo, negative connected Gmail; default if omitted.",
                  where="body"),
            Param("daily_limit", type="integer", description="Optional daily cap for this campaign only.", where="body"),
            Param("hourly_limit", type="integer", description="Optional hourly cap for this campaign only.", where="body"),
            Param("per_minute_limit", type="integer", description="Optional per-minute cap for this campaign only.", where="body"),
            Param("min_delay", type="integer", description="Optional minimum delay between sends, in seconds.", where="body"),
            Param("max_delay", type="integer", description="Optional maximum delay between sends, in seconds.", where="body"),
            Param("send_start_hour", type="integer", minimum=0, maximum=23,
                  description="Optional local start hour; unset means 24/7.", where="body"),
            Param("send_end_hour", type="integer", minimum=0, maximum=23,
                  description="Optional local end hour; unset means 24/7.", where="body"),
            Param("allow_weekends", type="boolean", description="Allow weekend sends for this campaign.", where="body"),
            Param("scheduled_start_at", where="body"),
        ],
    ),
    Tool(
        name="delete_campaign",
        description=(
            "Delete a campaign that has not sent anything: a draft, a scheduled one (or one "
            "paused while scheduled), or a failed one. Once a campaign has sent even one "
            "message the app refuses (409) — stop it instead to keep the history. An unknown "
            "id is a 404. Classic campaigns only: an Ads Manager campaign (kind 'ads') is "
            "paused or archived, never deleted from here."
        ),
        method="DELETE", path="/api/v1/campaigns/{campaign_id}", scope="write",
        group="campaigns", by_kind=True,
        params=[
            Param("campaign_id", type="integer", required=True, where="path"),
            Param("kind", enum=["campaign", "legacy", "ads"], where="meta",
                  description="Which campaign system the id belongs to (see list_campaigns). "
                              "Optional when the id exists in only one system; required when "
                              "it exists in both (the call is refused, listing the candidates)."),
        ],
    ),
    Tool(
        name="schedule_campaign",
        description=(
            "Set (or clear) the time a campaign launches by itself — the operator's "
            "'send it tomorrow at 9' request. The app validates the campaign first, so a "
            "scheduled launch cannot fail later for a reason that exists now. Pass a future "
            "ISO 8601 timestamp with an offset (e.g. 2026-05-01T09:00:00+01:00); pass null to "
            "CANCEL the schedule — the campaign goes back to draft. The reply carries "
            "'changed': false when the call did nothing (e.g. cancelling an unscheduled draft)."
        ),
        method="POST", path="/api/v1/campaigns/{campaign_id}/schedule", scope="write",
        group="campaigns", body_always=True, by_kind=True,
        params=[
            Param("campaign_id", type="integer", required=True, where="path"),
            Param("kind", enum=["campaign", "legacy", "ads"], where="meta",
                  description="Which campaign system the id belongs to (see list_campaigns). "
                              "Optional when the id exists in only one system; required when "
                              "it exists in both (the call is refused, listing the candidates)."),

            Param("scheduled_start_at", description="Future ISO 8601 time, or null to clear.",
                  where="body"),
        ],
    ),
    Tool(
        name="validate_campaign",
        description=(
            "Check a campaign before sending: audience, message, sender and consent. REPORT "
            "ONLY — it changes nothing (status, schedule, contacts), so call it as often as you "
            "like; the reply says 'valid', lists 'errors' and 'warnings', and always carries "
            "'changed': false. It does not schedule or arm anything: to send use "
            "start_campaign (now) or schedule_campaign (later). Call it after creating or "
            "editing an email campaign — it catches a missing subject or an unusable Brevo "
            "sender."
        ),
        method="POST", path="/api/v1/campaigns/{campaign_id}/validate", scope="write",
        group="campaigns", by_kind=True,
        kind_paths={"ads": ("POST", "/api/v1/ads/campaigns/{campaign_id}/validate")},
        params=[
            Param("campaign_id", type="integer", required=True, where="path"),
            Param("kind", enum=["campaign", "legacy", "ads"], where="meta",
                  description="Which campaign system the id belongs to (see list_campaigns). "
                              "Optional when the id exists in only one system; required when "
                              "it exists in both (the call is refused, listing the candidates)."),
        ],
    ),
    Tool(
        name="start_campaign",
        description=(
            "START SENDING a campaign now. This really sends messages to real people — confirm "
            "the channel, audience and message with the operator first. A draft is validated as "
            "part of starting (a problem comes back as a 400 listing every error)."
        ),
        method="POST", path="/api/v1/campaigns/{campaign_id}/start", scope="write",
        group="campaigns", by_kind=True,
        kind_paths={"ads": ("POST", "/api/v1/ads/campaigns/{campaign_id}/launch")},
        params=[
            Param("campaign_id", type="integer", required=True, where="path"),
            Param("kind", enum=["campaign", "legacy", "ads"], where="meta",
                  description="Which campaign system the id belongs to (see list_campaigns). "
                              "Optional when the id exists in only one system; required when "
                              "it exists in both (the call is refused, listing the candidates)."),
            Param("acknowledge_breaker", type="boolean", where="query",
                  description="Only to continue a campaign the bounce circuit breaker paused."),
        ],
    ),
    Tool(
        name="pause_campaign",
        description=(
            "Pause a RUNNING campaign, or hold a SCHEDULED one so it does not launch. "
            "resume_campaign puts it back where it was."
        ),
        method="POST", path="/api/v1/campaigns/{campaign_id}/pause", scope="write",
        group="campaigns", by_kind=True,
        kind_paths={"ads": ("POST", "/api/v1/ads/campaigns/{campaign_id}/pause")},
        params=[
            Param("campaign_id", type="integer", required=True, where="path"),
            Param("kind", enum=["campaign", "legacy", "ads"], where="meta",
                  description="Which campaign system the id belongs to (see list_campaigns). "
                              "Optional when the id exists in only one system; required when "
                              "it exists in both (the call is refused, listing the candidates)."),
        ],
    ),
    Tool(
        name="resume_campaign",
        description=(
            "Resume a paused campaign. One paused mid-send continues sending; one paused while "
            "scheduled goes back to scheduled and does NOT start sending (use start_campaign "
            "for that). A campaign paused by the circuit breaker says why in 'paused_reason'."
        ),
        method="POST", path="/api/v1/campaigns/{campaign_id}/resume", scope="write",
        group="campaigns", by_kind=True,
        kind_paths={"ads": ("POST", "/api/v1/ads/campaigns/{campaign_id}/resume")},
        params=[
            Param("campaign_id", type="integer", required=True, where="path"),
            Param("kind", enum=["campaign", "legacy", "ads"], where="meta",
                  description="Which campaign system the id belongs to (see list_campaigns). "
                              "Optional when the id exists in only one system; required when "
                              "it exists in both (the call is refused, listing the candidates)."),
            Param("acknowledge_breaker", type="boolean", where="query",
                  description="Required to resume a campaign the bounce circuit breaker paused "
                              "(read 'paused_reason' first; fix the list)."),
        ],
    ),
    Tool(
        name="campaign_analytics",
        description="Performance of one campaign: sent, delivered, failed, replies, interested.",
        method="GET", path="/api/v1/campaigns/{campaign_id}/analytics", group="campaigns",
        by_kind=True, kind_paths={"ads": ("GET", "/api/v1/ads/campaigns/{campaign_id}/analytics")},
        params=[
            Param("campaign_id", type="integer", required=True, where="path"),
            Param("kind", enum=["campaign", "legacy", "ads"], where="meta",
                  description="Which campaign system the id belongs to (see list_campaigns). "
                              "Optional when the id exists in only one system; required when "
                              "it exists in both (the call is refused, listing the candidates)."),
        ],
    ),
    # -------------------------------------------------------------- one-offs
    Tool(
        name="send_sms_now",
        description=(
            "Send ONE SMS now (or schedule it) to a contact, a raw phone number, or a whole "
            "list. Opted-out contacts are refused by the app."
        ),
        method="POST", path="/api/v1/send/", scope="write", group="sending",
        params=[
            Param("body", required=True, description="The SMS text.", where="query"),
            Param("contact_id", type="integer", where="query"),
            Param("phone_number", description="e.g. +2348012345678", where="query"),
            Param("list_id", type="integer", where="query"),
            Param("schedule_at", description="ISO-8601; omit to send now.", where="query"),
        ],
    ),
    Tool(
        name="send_email_now",
        description=(
            "Send ONE email through Brevo now, or schedule it. Target a contact_id, a raw "
            "address, or a whole list_id. Supports subject, body (plain text), html_body, "
            "attachments ([{name, content_base64}]), cc/bcc and template_id. Choose any connected "
            "Gmail or Brevo sender; direct sends ignore campaign-only pacing controls."
        ),
        method="POST", path="/api/v1/email/send", scope="write", group="sending",
        params=[
            Param(
                "subject",
                description="Subject line. Required unless a template supplies it.",
                where="body",
            ),
            Param("body", description="Plain-text body (also the fallback when html_body is set).",
                  where="body"),
            Param("html_body", where="body"),
            Param("contact_id", type="integer", where="body"),
            Param("email", description="A raw address instead of a contact.", where="body"),
            Param("list_id", type="integer", description="Send to everyone in the list.",
                  where="body"),
            Param("template_id", type="integer", where="body"),
            Param("email_account_id", type="integer",
                  description="Unified sender id: positive Brevo, negative connected Gmail; default if omitted.",
                  where="body"),
            Param("cc", type="array", items="string", where="body"),
            Param("bcc", type="array", items="string", where="body"),
            Param("attachments", type="array", items="object",
                  description="[{name, content_base64, content_type}]", where="body"),
            Param("schedule_at", description="ISO-8601; omit to send now.", where="body"),
        ],
    ),
    Tool(
        name="send_test_email",
        description=(
            "Mail the email currently being planned to one address as a test — variables "
            "rendered, attachments included, no contact or thread created. Use this to check a "
            "campaign before it goes to the list."
        ),
        method="POST", path="/api/v1/email/test-send", scope="write", group="sending",
        params=[
            Param("to", required=True, where="body"),
            Param("subject", where="body"),
            Param("body", where="body"),
            Param("html_body", where="body"),
            Param("template_id", type="integer", where="body"),
            Param("email_account_id", type="integer", where="body"),
            Param("attachments", type="array", items="object", where="body"),
        ],
    ),
    Tool(
        name="send_history",
        description=(
            "Recent sends with their status. SMS by default (email rows used to be mixed in); "
            "channel=email or channel=all asks for the others."
        ),
        method="GET", path="/api/v1/send/history", group="sending",
        params=[Param("channel", enum=["sms", "email", "all"], default="sms", where="query"),
                Param("page", type="integer", default=1, where="query"),
                Param("per_page", type="integer", default=25, minimum=1, maximum=100, where="query",
                      description="Page size, 1-100 (above 100 is refused, not clamped).")],
    ),
    Tool(
        name="scheduled_messages",
        description="Everything queued to go out later (SMS and email).",
        method="GET", path="/api/v1/send/scheduled", group="sending",
    ),
    Tool(
        name="email_history",
        description=(
            "Email history including sent and failed messages, failure reasons, sender, opens, "
            "clicks and attachments. Filter status=failed to inspect errors or status=sent,delivered "
            "for successful delivery."
        ),
        method="GET", path="/api/v1/email/history", group="sending",
        params=[Param("status", description="Comma-separated statuses such as sent,delivered or failed.", where="query"),
                Param("direction", enum=["incoming", "outgoing"], where="query"),
                Param("account_id", type="integer", description="Unified sender id (positive Brevo, negative Gmail).", where="query"),
                Param("campaign_id", type="integer", where="query"),
                Param("page", type="integer", default=1, where="query"),
                Param("per_page", type="integer", default=25, minimum=1, maximum=200, where="query",
                      description="Page size, 1-200 (above 200 is refused, not clamped).")],
    ),
    # ------------------------------------------------------------------ email
    Tool(
        name="list_email_accounts",
        description=(
            "Every selectable email sender: Brevo API accounts and all connected Gmail "
            "mailboxes. Each has a From address and unified sender id (positive Brevo, negative "
            "Gmail); use that id for sends, replies, and campaigns."
        ),
        method="GET", path="/api/v1/email/senders", group="email",
    ),
    Tool(
        name="email_inbox",
        description="Email conversations in the shared inbox, newest activity first.",
        method="GET", path="/api/v1/email/inbox/conversations", group="email",
        params=[Param("status", where="query"),
                Param("unread_only", type="boolean", where="query",
                      description="Only threads with unread messages."),
                Param("replied_only", type="boolean", where="query",
                      description="Only threads the contact actually replied to — a real "
                                  "conversation rather than mail nobody answered."),
                Param("campaign_id", type="integer", where="query",
                      description="Only threads attributed to this campaign."),
                Param("search", where="query", description="Match the contact's email or name."),
                Param("page", type="integer", default=1, where="query"),
                Param("per_page", type="integer", default=25, minimum=1, maximum=100, where="query",
                      description="Page size, 1-100 (above 100 is refused, not clamped).")],
    ),
    Tool(
        name="get_email_conversation",
        description="One email thread with every message in it — read what the prospect said.",
        method="GET", path="/api/v1/email/inbox/conversations/{conversation_id}", group="email",
        params=[Param("conversation_id", type="integer", required=True, where="path")],
    ),
    Tool(
        name="reply_to_email",
        description=(
            "Reply inside an email thread, from the same sender it started on and threaded "
            "correctly. Supports html_body and attachments."
        ),
        method="POST", path="/api/v1/email/inbox/conversations/{conversation_id}/reply",
        scope="write", group="email",
        params=[
            Param("conversation_id", type="integer", required=True, where="path"),
            Param("body", required=True, where="body"),
            Param("html_body", where="body"),
            Param("subject", where="body"),
            Param("email_account_id", type="integer", where="body"),
            Param("attachments", type="array", items="object", where="body"),
        ],
    ),
    Tool(
        name="email_engagement",
        description=(
            "What one contact did with your email: sent/opened/clicked/bounced, the links they "
            "clicked, and their recent messages."
        ),
        method="GET", path="/api/v1/email/contacts/{contact_id}/engagement", group="email",
        params=[Param("contact_id", type="integer", required=True, where="path")],
    ),
    Tool(
        name="email_stats",
        description="Email totals for the dashboard: sent, delivered, opened, clicked, bounced.",
        method="GET", path="/api/v1/email/stats", group="email",
    ),
    Tool(
        name="email_suppression_list",
        description="Addresses that must never be emailed again, with the reason.",
        method="GET", path="/api/v1/email/suppression", group="email",
    ),
    Tool(
        name="add_email_suppression",
        description="Block an address from all future email.",
        method="POST", path="/api/v1/email/suppression", scope="write", group="email",
        params=[
            Param("email_address", required=True, where="body"),
            Param("reason", where="body"),
        ],
    ),
    # ------------------------------------------------------------------ inbox
    Tool(
        name="inbox_conversations",
        description="Conversations across BOTH channels (SMS and email) in the unified inbox.",
        method="GET", path="/api/v1/inbox/conversations", group="inbox",
        params=[Param("channel", enum=["all", "sms", "email"], default="all", where="query"),
                Param("status", where="query"),
                Param("page", type="integer", default=1, where="query"),
                Param("per_page", type="integer", default=25, minimum=1, maximum=500, where="query",
                      description="Page size, 1-500 (above 500 is refused, not clamped).")],
    ),
    Tool(
        name="get_inbox_conversation",
        description="One conversation with its full message history (either channel).",
        method="GET", path="/api/v1/inbox/conversations/{conversation_id}", group="inbox",
        params=[Param("conversation_id", type="integer", required=True, where="path")],
    ),
    Tool(
        name="reply_to_conversation",
        description=(
            "Reply in a conversation. The app sends it on the conversation's own channel: a text "
            "message for an SMS thread, an email from the sender the thread started on for an email "
            "thread. Give `body`, or a `template_id` to have it written from a template (variables "
            "filled in for this contact); the reply is refused if you give neither."
        ),
        method="POST", path="/api/v1/inbox/conversations/{conversation_id}/reply", scope="write",
        group="inbox",
        params=[
            Param("conversation_id", type="integer", required=True, where="path"),
            Param("body", where="body", description="The message text. Optional when template_id is given."),
            Param("template_id", type="integer", where="body",
                  description="Reply from this saved template instead of writing the text."),
            Param("subject", where="body",
                  description="Email conversations only. Defaults to 'Re: <the thread's subject>'."),
        ],
    ),
    Tool(
        name="mark_conversation",
        description="Set a conversation's status: interested, not interested, closed or unread.",
        method="POST", path="/api/v1/inbox/conversations/{conversation_id}/{mark}", scope="write",
        group="inbox",
        params=[
            Param("conversation_id", type="integer", required=True, where="path"),
            Param("mark", required=True, where="path",
                  enum=["mark-interested", "mark-not-interested", "mark-close", "mark-unread"]),
        ],
    ),
    # -------------------------------------------------------------- follow-ups
    Tool(
        name="list_followups",
        description=(
            "Follow-ups scheduled for specific contacts, both channels. By default: every follow-up "
            "that has not been sent yet, whatever day it is due. (The dashboard page opens on "
            "'due today, SMS only' -- this tool does not, so a follow-up you scheduled for next week "
            "or by email is here.) Use `view` for overdue / upcoming / completed / skipped."
        ),
        method="GET", path="/api/v1/followups/", group="follow-ups",
        params=[
            Param("view", enum=["pending", "due_today", "overdue", "upcoming", "completed", "skipped", "all"],
                  default="pending", where="query"),
            Param("channel", enum=["sms", "email", "all"], default="all", where="query"),
            Param("page", type="integer", default=1, minimum=1, where="query"),
            Param("per_page", type="integer", default=25, minimum=1, maximum=100, where="query"),
        ],
    ),
    Tool(
        name="delete_followup",
        description=(
            "Remove a follow-up that has not gone out yet. One that was already sent stays in the "
            "history. Removing a campaign's follow-up ends that contact's automation, like skipping it."
        ),
        method="DELETE", path="/api/v1/followups/{followup_id}", scope="write", group="follow-ups",
        params=[Param("followup_id", type="integer", required=True, where="path")],
    ),
    Tool(
        name="create_followup",
        description=(
            "Schedule a follow-up for one contact, on either channel. It lands in the same chat "
            "as the original message."
        ),
        method="POST", path="/api/v1/followups/", scope="write", group="follow-ups",
        params=[
            Param("contact_id", type="integer", required=True, where="body"),
            Param("scheduled_at", required=True,
                  description="ISO-8601 with timezone, e.g. 2026-10-05T09:00:00+01:00",
                  where="body"),
            Param("message_text", required=True, where="body"),
            Param("channel", enum=["sms", "email"], default="sms", where="body"),
            Param("subject", description="Email only.", where="body"),
            Param("email_account_id", type="integer", where="body"),
        ],
    ),
    Tool(
        name="send_followup_now",
        description="Send a pending follow-up immediately.",
        method="POST", path="/api/v1/followups/{followup_id}/send-now", scope="write",
        group="follow-ups",
        params=[Param("followup_id", type="integer", required=True, where="path")],
    ),
    Tool(
        name="campaign_followups",
        description="The automatic follow-up rules attached to a campaign.",
        method="GET", path="/api/v1/campaign-followups/campaigns/{campaign_id}", group="follow-ups",
        params=[Param("campaign_id", type="integer", required=True, where="path")],
    ),
    Tool(
        name="create_campaign_followup",
        description=(
            "Add a follow-up step to a campaign: wait N minutes after the previous message, then "
            "send this text to everyone who has not replied."
        ),
        method="POST", path="/api/v1/campaign-followups/campaigns/{campaign_id}", scope="write",
        group="follow-ups",
        params=[
            Param("campaign_id", type="integer", required=True, where="path"),
            Param("name", where="body"),
            Param("channel", enum=["sms", "email"], default="sms", where="body"),
            Param("subject", description="Email only.", where="body"),
            Param("message_text", required=True, where="body"),
            Param("delay_minutes", type="integer", default=1440,
                  description="How long to wait before this step.", where="body"),
            Param("stop_on_reply", type="boolean", default=True, where="body"),
        ],
    ),
    # -------------------------------------------------------------- analytics
    Tool(
        name="dashboard_stats",
        description=(
            "Headline numbers: contacts, messages, replies, campaigns. All channels and all time by "
            "default; pass channel and/or days to scope it. The response says which scope and which "
            "definitions its numbers use, and they are the same ones analytics_overview and the email "
            "tools use -- the same database gives the same numbers in all of them."
        ),
        method="GET", path="/api/v1/dashboard/stats", group="analytics",
        params=[
            Param("channel", enum=["sms", "email", "all"], where="query",
                  description="One channel, or all (the default)."),
            Param("days", type="integer", minimum=1, maximum=365, where="query",
                  description="Only the last N days (UTC, today included). Omit for all time."),
        ],
    ),
    Tool(
        name="email_overview",
        description="Email dashboard: totals, deliverability and recent activity.",
        method="GET", path="/api/v1/email/overview", group="analytics",
    ),
    Tool(
        name="analytics_overview",
        description=(
            "Analytics overview (volume, delivery, replies) for the last N days. SMS by default -- "
            "pass channel=email or channel=all for the others. Same definitions and window as "
            "dashboard_stats and the email tools."
        ),
        method="GET", path="/api/v1/analytics/overview", group="analytics",
        params=[
            Param("days", type="integer", default=30, minimum=1, maximum=365, where="query"),
            Param("channel", enum=["sms", "email", "all"], default="sms", where="query"),
            Param("campaign_id", type="integer", where="query"),
        ],
    ),
    Tool(
        name="list_variables",
        description=(
            "The personalisation variables available in this app ({{first_name}}, {{city}}, and "
            "any custom column imported from CSV)."
        ),
        method="GET", path="/api/v1/variables/", group="analytics",
    ),
    Tool(
        name="app_reference",
        description="Everything the app knows it can send with: channels, accounts, lists, counts.",
        method="GET", path="/api/v1/email/reference", group="analytics",
    ),
    # ------------------------------------------------------- the escape hatch
    Tool(
        name="list_api_endpoints",
        description=(
            "Every HTTP endpoint in this app, from its own OpenAPI document. Use it when a tool "
            "you want does not exist, then call app_api_request to reach it."
        ),
        method="OPENAPI", path="/openapi.json", group="advanced",
    ),
    Tool(
        name="app_api_request",
        description=(
            "Escape hatch: call any endpoint of this app directly (path from list_api_endpoints), "
            "e.g. PATCH /api/v1/campaigns/{campaign_id} to change that campaign's optional "
            "limits and sending window. Direct sends are not subject to campaign controls. Same "
            "auth and validation as the UI. Prefer the named tools when one exists."
        ),
        # Usable by read-only tokens for GETs; the executor refuses writes when
        # the token's scope is read.
        method="REQUEST", path="", scope="read", group="advanced",
        params=[
            Param("method", required=True, enum=["GET", "POST", "PUT", "PATCH", "DELETE"],
                  where="arg"),
            Param("path", required=True,
                  description="e.g. /api/v1/campaigns/ or /api/v1/contacts/?search=ada",
                  where="arg"),
            Param("body", type="object",
                  description="JSON body, if the endpoint takes one (an array when the "
                              "endpoint expects a bare list, e.g. lists/{id}/contacts/remove).",
                  where="arg"),
            Param("query", type="object", description="Query parameters, if any.", where="arg"),
        ],
    ),
]

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


def tools_for(names: "set[str] | None") -> list[Tool]:
    """The tools a connector publishes.

    ``names`` is ``None`` for "everything", or the focused set a connector
    profile asks for — ChatGPT gets :data:`app.mcp.connectors.CORE_TOOLS`,
    because 60 tools of JSON Schema cost prompt budget and measurably degrade
    tool choice, and OpenAI's own connector guidance is to publish what the
    assistant will actually pick between.
    """
    if names is None:
        return TOOLS
    return [tool for tool in TOOLS if tool.name in names]
