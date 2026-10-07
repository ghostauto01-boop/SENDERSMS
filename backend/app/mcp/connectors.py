"""
One connector profile per AI client, built from that client's own documentation.

WHY THEY ARE SEPARATE
---------------------
"Point ChatGPT and Claude at /mcp with a bearer token" does not work, and the
reasons are different for each client. Each vendor publishes its own
requirements, so each gets its own endpoint, its own OAuth resource identifier,
its own redirect-URI allowlist and its own tool set. Everything here is a
transcription of published behaviour:

**ChatGPT** (developers.openai.com — *Building MCP servers for plugins and API
integrations*, *Authentication*):

* remote HTTPS server speaking Streamable HTTP at a stable URL ending in
  ``/mcp``;
* authentication is OAuth 2.1 only. OpenAI is explicit that ChatGPT "does not
  support machine-to-machine OAuth grants such as client credentials, service
  accounts, or JWT bearer assertions, nor can it present custom API keys" —
  which is why a Settings → *Create token* bearer could never connect;
* the server must host protected-resource metadata, the authorization server
  must publish its own metadata, and the ``resource`` parameter (RFC 8707) must
  be echoed through the whole flow;
* client registration is by Client ID Metadata Document (preferred —
  ``client_id`` is the URL ``https://chatgpt.com/oauth/client.json``) or by
  dynamic client registration;
* ``token_endpoint_auth_methods_supported`` must advertise ``none`` (public
  client + PKCE) and/or ``private_key_jwt``;
* the compliant redirect URI is
  ``https://chatgpt.com/connector_platform_oauth_redirect``.

**Claude** (claude.com/docs — *Custom connectors*, *Authentication for
connectors*, plus the connector-directory submission requirements):

* OAuth 2.1 + PKCE S256; claude.ai connects from Anthropic's cloud, so the URL
  must be publicly reachable;
* "Return an actual 401 Unauthorized with a WWW-Authenticate challenge. Claude
  does not honor the challenge on a 200 response";
* "The resource in protected resource metadata must match the MCP server URL
  exactly as users enter it, including its path";
* "List the primary authorization server first because Claude currently uses the
  first entry";
* hosted Claude surfaces use ``https://claude.ai/api/mcp/auth_callback``;
  Claude Code uses a localhost/127.0.0.1 loopback redirect on a *varying* port,
  so loopback matching has to ignore the port;
* the token endpoint is called with ``application/x-www-form-urlencoded`` while
  dynamic registration is called with JSON — both parsers must work;
* every tool needs a ``title`` and the applicable ``readOnlyHint`` or
  ``destructiveHint`` annotation;
* Claude Code (and any client that can set headers) also accepts a static
  ``Authorization: Bearer`` header, so both auth modes stay available.

**Arena** (help.arena.ai — *How to use Agent Mode*): Agent Mode gives the agent
web search, file tools and a bash sandbox; it does not offer a hosted
"custom connector" form the way ChatGPT and Claude do. So the Arena connector is
the one an agent in a sandbox can actually use: a static bearer token over
Streamable HTTP, a stdio↔HTTP bridge script that runs in the sandbox
(``tools/arena-connector/``), and a paste-in prompt. OAuth is offered too, for
any Arena-side client that grows a connector form, but bearer is the default.

Adding a fourth client is one more entry in :data:`CONNECTORS` — the endpoints,
the metadata documents and the settings card are generated from the profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.utils.urls import public_base_url

#: Every protocol revision this server answers. Newest first — a client's own
#: requested version is echoed when it appears here, otherwise the newest we
#: support is offered back.
PROTOCOL_VERSIONS: tuple[str, ...] = (
    "2026-07-28",  # stateless core, MCP Apps, extensions framework
    "2025-11-25",  # OIDC discovery, icons, Client ID Metadata Documents
    "2025-06-18",  # structured tool output, OAuth resource-server classification
    "2025-03-26",  # Streamable HTTP + OAuth 2.1
    "2024-11-05",  # HTTP+SSE legacy
)
DEFAULT_PROTOCOL = PROTOCOL_VERSIONS[0]

#: Scopes an assistant can ask for. Coarse on purpose, and identical in meaning
#: to the read/write switch on a static token.
SCOPES: tuple[str, ...] = ("read", "write")
DEFAULT_SCOPE = "read write"

#: Hosts whose Client ID Metadata Document we are willing to fetch. A CIMD
#: client_id is an arbitrary URL, so fetching one is a server-side request
#: forgery vector; the allowlist closes it while still covering every published
#: client document (OpenAI's and Anthropic's).
CIMD_ALLOWED_HOSTS: tuple[str, ...] = (
    "chatgpt.com",
    "openai.com",
    "claude.ai",
    "claude.com",
    "anthropic.com",
    "arena.ai",
)

#: The focused tool list ChatGPT gets by default. OpenAI's own guidance is that a
#: connector should expose what the assistant will actually pick between; 60
#: tools of JSON Schema costs prompt budget and degrades tool choice. The full
#: set stays one switch away in Settings → AI (MCP).
CORE_TOOLS: tuple[str, ...] = (
    "how_to_use_this_app",
    "app_reference",
    "search_contacts",
    "get_contact",
    "create_contact",
    "update_contact",
    "import_contacts_csv",
    "list_contact_lists",
    "create_contact_list",
    "add_contacts_to_list",
    "list_templates",
    "create_template",
    "preview_template",
    "list_campaigns",
    "get_campaign",
    "create_campaign",
    "update_campaign",
    "validate_campaign",
    "start_campaign",
    "campaign_analytics",
    "send_email_now",
    "send_sms_now",
    "send_test_email",
    "inbox_conversations",
    "get_inbox_conversation",
    "reply_to_email",
    "reply_to_conversation",
    "mark_conversation",
    "email_engagement",
    "dashboard_stats",
    "list_variables",
    "list_api_endpoints",
    "app_api_request",
)


@dataclass(frozen=True)
class ConnectorProfile:
    """Everything client-specific about one connector."""

    #: Stable id, used in URLs, database rows and the UI.
    key: str
    #: Human name.
    label: str
    vendor: str
    #: Path of this connector's MCP endpoint. Always ends in ``/mcp`` because
    #: that is the stable shape OpenAI documents.
    path: str
    #: Path prefix for this connector's OAuth endpoints. Kept per connector so a
    #: client registered for Claude can never present a ChatGPT redirect URI.
    oauth_path: str = "/oauth"
    #: Which auth modes this connector accepts, best first.
    auth_modes: tuple[str, ...] = ("oauth", "bearer")
    #: Host suffixes whose redirect URIs are accepted without further question.
    #: Loopback is handled separately (see ``allow_loopback``).
    redirect_hosts: tuple[str, ...] = ()
    #: Exact redirect URIs that are always accepted (documented callback URLs).
    redirect_uris: tuple[str, ...] = ()
    #: Accept any well-formed HTTPS callback? Only the generic OAuth endpoint
    #: does this; vendor-specific connectors keep their tighter host allowlists.
    allow_any_https_redirect: bool = False
    #: Accept http://localhost:PORT / http://127.0.0.1:PORT on any port?
    #: Required for Claude Code, which picks a random free port each run.
    allow_loopback: bool = False
    #: Advertise Client ID Metadata Document support to this client?
    cimd: bool = False
    #: "core" (CORE_TOOLS) or "full" (everything in the registry).
    toolset: str = "full"
    #: Anthropic asks for these on every tool; harmless (and useful) elsewhere.
    annotations: bool = True
    docs_url: str = ""
    blurb: str = ""
    #: The click-path shown on the settings card.
    setup_steps: tuple[str, ...] = field(default_factory=tuple)

    # -- derived URLs ------------------------------------------------------

    @property
    def resource(self) -> str:
        """The canonical resource identifier — the exact URL the user pastes."""
        base = (public_base_url() or "").rstrip("/")
        return f"{base}{self.path}" if base else self.path

    def well_known_prm(self) -> str:
        """RFC 9728 protected-resource metadata URL for this resource."""
        base = (public_base_url() or "").rstrip("/")
        return f"{base}/.well-known/oauth-protected-resource{self.path}"

    def supports_oauth(self) -> bool:
        return "oauth" in self.auth_modes

    def allows_redirect(self, uri: str) -> bool:
        """Is this redirect URI acceptable for this connector?

        Exact match against documented callbacks first, then a host suffix, the
        generic HTTPS rule, and finally loopback (any port, because Claude Code
        picks a random one). Remote callbacks must use HTTPS; HTTP is accepted
        only for loopback. Fragments and embedded credentials are never valid.
        """
        raw = (uri or "").strip()
        if not raw:
            return False
        if raw in self.redirect_uris:
            return True
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
        if not host or parts.username is not None or parts.password is not None or parts.fragment:
            return False
        try:
            _ = parts.port  # Reject malformed/out-of-range ports.
        except ValueError:
            return False
        if parts.scheme == "https":
            if any(
                host == allowed or host.endswith("." + allowed)
                for allowed in self.redirect_hosts
            ):
                return True
            if self.allow_any_https_redirect:
                return True
        if self.allow_loopback and parts.scheme in ("http", "https") and host in (
            "localhost",
            "127.0.0.1",
            "[::1]",
            "::1",
        ):
            return True
        return False


CHATGPT = ConnectorProfile(
    key="chatgpt",
    label="ChatGPT",
    vendor="OpenAI",
    path="/connectors/chatgpt/mcp",
    oauth_path="/connectors/chatgpt/oauth",
    auth_modes=("oauth",),  # ChatGPT cannot present an API key at all.
    redirect_hosts=("chatgpt.com", "openai.com", "chat.openai.com"),
    redirect_uris=(
        "https://chatgpt.com/connector_platform_oauth_redirect",
        "https://chatgpt.com/backend-api/aip/connectors/links/oauth/callback",
    ),
    allow_loopback=True,  # ChatGPT desktop/dev flows use a local callback.
    cimd=True,
    toolset="core",
    docs_url="https://developers.openai.com/api/docs/mcp",
    blurb=(
        "ChatGPT connects with OAuth 2.1 only — it cannot send an API key. Sign in once with your "
        "app login, approve the scopes, and the connector stays connected."
    ),
    setup_steps=(
        "ChatGPT → Settings → Connectors → Advanced settings → turn on Developer mode.",
        "Connectors → Create (or Tools → Run deep research → Add sources → Connect more → Create).",
        "Name it, then paste the MCP server URL below — it must include the full path.",
        "Set Authentication to OAuth. ChatGPT discovers the authorization server from this URL's "
        "protected-resource metadata; there is nothing else to paste.",
        "A window opens on this app: sign in, choose Read-only or Read & write, press Approve.",
        "ChatGPT scans the tool list. Ask it to run how_to_use_this_app before it sends anything.",
    ),
)

CLAUDE = ConnectorProfile(
    key="claude",
    label="Claude",
    vendor="Anthropic",
    path="/connectors/claude/mcp",
    oauth_path="/connectors/claude/oauth",
    auth_modes=("oauth", "bearer"),
    redirect_hosts=("claude.ai", "claude.com", "anthropic.com"),
    redirect_uris=(
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.ai/api/auth/callback",
    ),
    # Claude Code signs in through a browser and catches the redirect on
    # localhost, on a port it chooses at run time.
    allow_loopback=True,
    cimd=True,
    toolset="full",
    docs_url="https://claude.com/docs/connectors/building/authentication",
    blurb=(
        "claude.ai, Claude Desktop and the mobile apps connect from Anthropic's cloud with OAuth 2.1 "
        "+ PKCE. Claude Code can also use the static bearer token."
    ),
    setup_steps=(
        "Claude → Customize → Connectors (claude.ai/customize/connectors).",
        "Click +, then Add custom connector, and paste the MCP server URL below.",
        "Leave Advanced settings empty — this server publishes its own OAuth metadata, so Claude "
        "discovers the client registration itself.",
        "Click Add, then sign in on the page that opens here and press Approve.",
        "Enable the connector in the chat you want it in (the + / connectors menu).",
        "Claude Code instead: claude mcp add --transport http sendsms <url>, then /mcp to sign in — "
        "or pass --header \"Authorization: Bearer <token>\" to skip OAuth entirely.",
    ),
)

ARENA = ConnectorProfile(
    key="arena",
    label="Arena AI agent",
    vendor="Arena.ai",
    path="/connectors/arena/mcp",
    oauth_path="/connectors/arena/oauth",
    auth_modes=("bearer", "oauth"),
    redirect_hosts=("arena.ai",),
    allow_loopback=True,
    cimd=False,
    toolset="full",
    docs_url="https://help.arena.ai/articles/5432423882-how-to-use-agent-mode",
    blurb=(
        "Arena's Agent Mode runs your agent in a sandbox with bash and file tools rather than a "
        "hosted connector form, so this connector is built for that: a bearer token over Streamable "
        "HTTP, plus a stdio bridge the agent can run inside the sandbox."
    ),
    setup_steps=(
        "Arena Agent Mode has no connector screen — it has a sandbox with bash. So the agent "
        "reaches this endpoint with curl, or through the stdio bridge in this repository.",
        "Create a token below (Read & write if the agent should send).",
        "Paste this into the agent's prompt: \"Read tools/arena-connector/AGENTS.md in this "
        "repository and follow it. The MCP server is <url> and the bearer token is <token>.\" "
        "That file has the exact curl handshake, the tool tour and the safety rules.",
        "A host that can only launch a command instead runs "
        "tools/arena-connector/arena_mcp_bridge.py — Python 3 standard library, nothing to install.",
        "Any MCP client that reads a config block can use tools/arena-connector/mcp.json (bridge) "
        "or mcp.http.json (remote HTTP) as-is, after fixing the URL and token.",
    ),
)

GENERIC = ConnectorProfile(
    key="generic",
    label="Any MCP client",
    vendor="Model Context Protocol",
    path="/mcp",
    oauth_path="/oauth",
    auth_modes=("bearer", "oauth"),
    # Accept well-formed HTTPS callback URIs from dynamic clients, plus the
    # loopback URIs used by local tooling. This is what lets a hosted client such
    # as ChatGPT use the canonical /mcp URL; vendor-specific connector paths
    # retain their narrower callback allowlists. PKCE S256 is always mandatory.
    allow_any_https_redirect=True,
    allow_loopback=True,
    cimd=True,
    toolset="full",
    docs_url="https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization",
    blurb=(
        "The canonical endpoint. Works with MCP Inspector, Cursor, Cline, LangChain, the Python/TS "
        "SDKs — anything that speaks Streamable HTTP, with a bearer token or the same OAuth flow."
    ),
    setup_steps=(
        "MCP Inspector: npx @modelcontextprotocol/inspector, choose Streamable HTTP, paste the URL, "
        "then Open Auth Settings → Quick OAuth Flow.",
        "Cursor / Cline / Windsurf: add an HTTP server with this URL; the token goes in the "
        "Authorization header.",
        "Python SDK: mcp.client.streamable_http.streamablehttp_client(url, headers=...).",
    ),
)

CONNECTORS: tuple[ConnectorProfile, ...] = (CHATGPT, CLAUDE, ARENA, GENERIC)
BY_KEY: dict[str, ConnectorProfile] = {c.key: c for c in CONNECTORS}
BY_PATH: dict[str, ConnectorProfile] = {c.path: c for c in CONNECTORS}


def get(key: str | None) -> ConnectorProfile:
    """The profile for a connector key; unknown keys fall back to generic."""
    return BY_KEY.get((key or "generic").strip().lower(), GENERIC)


def by_path(path: str | None) -> ConnectorProfile:
    """The profile whose endpoint matches a request path."""
    clean = (path or "").rstrip("/") or "/"
    return BY_PATH.get(clean, GENERIC)


def tool_names(profile: "ConnectorProfile | None", toolset: str | None = None) -> set[str] | None:
    """Which tools this connector publishes — ``None`` means all of them.

    ``toolset`` lets the operator override the profile's default from Settings,
    so a connector can be widened to every tool (or narrowed to the focused set)
    without a code change.
    """
    if profile is None:
        effective = toolset or "full"
    else:
        effective = toolset or profile.toolset
    return set(CORE_TOOLS) if effective == "core" else None


def authorization_server_metadata(profile: ConnectorProfile) -> dict:
    """RFC 8414 metadata, tailored per client.

    The fields that actually decide whether a client connects:

    * ``client_id_metadata_document_supported`` — ChatGPT prefers CIMD and skips
      registration entirely when this is true;
    * ``token_endpoint_auth_methods_supported`` — must include ``none`` so a
      public client can exchange a code with PKCE and no secret;
    * ``registration_endpoint`` — present only when dynamic registration is
      allowed for this connector;
    * ``code_challenge_methods_supported`` — S256 only; OAuth 2.1 removed plain;
    * ``authorization_response_iss_parameter_supported`` — OpenAI's docs list it,
      and we do return ``iss`` on the redirect.
    """
    base = (public_base_url() or "").rstrip("/")
    oauth_root = f"{base}{profile.oauth_path}"
    metadata: dict = {
        "issuer": base or "/",
        "authorization_endpoint": f"{oauth_root}/authorize",
        "token_endpoint": f"{oauth_root}/token",
        "revocation_endpoint": f"{oauth_root}/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        # "none" first: a public client + PKCE is what ChatGPT and Claude use.
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
        "scopes_supported": list(SCOPES),
        "service_documentation": profile.docs_url,
        "authorization_response_iss_parameter_supported": True,
        "client_id_metadata_document_supported": bool(profile.cimd),
        "token_endpoint_auth_signing_alg_values_supported": ["HS256", "RS256"],
    }
    if profile.cimd or "oauth" in profile.auth_modes:
        metadata["registration_endpoint"] = f"{oauth_root}/register"
    return metadata


def protected_resource_metadata(profile: ConnectorProfile) -> dict:
    """RFC 9728 protected-resource metadata for this connector's endpoint.

    ``resource`` is the exact URL the operator pastes into the client, and
    ``authorization_servers`` lists this deployment first — Claude uses the first
    entry only.
    """
    base = (public_base_url() or "").rstrip("/")
    return {
        "resource": profile.resource,
        "authorization_servers": [base or "/"],
        "scopes_supported": list(SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_documentation": profile.docs_url,
        "tls_client_certificate_bound_access_tokens": False,
        "dpop_bound_access_tokens_required": False,
        # Tells the client what to expect from a 401 here.
        "resource_signing_alg_values_supported": [],
    }


def describe(profile: ConnectorProfile) -> dict:
    """The connector card the settings UI renders."""
    base = (public_base_url() or "").rstrip("/")
    return {
        "key": profile.key,
        "label": profile.label,
        "vendor": profile.vendor,
        "blurb": profile.blurb,
        "docs_url": profile.docs_url,
        "setup_steps": list(profile.setup_steps),
        "endpoint": profile.resource,
        "endpoint_ready": bool(base),
        "auth_modes": list(profile.auth_modes),
        "toolset": profile.toolset,
        "tool_count": len(CORE_TOOLS) if profile.toolset == "core" else None,
        "protected_resource_metadata": profile.well_known_prm(),
        "authorization_server_metadata": f"{base}/.well-known/oauth-authorization-server",
        "registration_endpoint": f"{base}{profile.oauth_path}/register",
        "authorize_endpoint": f"{base}{profile.oauth_path}/authorize",
        "token_endpoint": f"{base}{profile.oauth_path}/token",
        "redirect_uris": list(profile.redirect_uris),
        "allow_loopback_redirects": profile.allow_loopback,
        "client_id_metadata_documents": profile.cimd,
        "protocol_versions": list(PROTOCOL_VERSIONS),
    }
