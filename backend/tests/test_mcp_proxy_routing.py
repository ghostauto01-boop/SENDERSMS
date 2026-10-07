"""Regression tests for root-level MCP routes behind the repository's proxies.

The application serves MCP and OAuth endpoints beside the SPA, not below
``/api``. If either proxy lets one of these paths fall through to ``index.html``,
remote clients receive HTML instead of protocol JSON and report a failed
connection.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_vite_forwards_all_mcp_and_oauth_route_prefixes():
    vite_config = (ROOT / "vite.config.ts").read_text(encoding="utf-8")

    for context in ('"/mcp"', '"/oauth"', '"/connectors"', '"/.well-known"'):
        assert context in vite_config, f"Vite does not proxy {context} to FastAPI"


def test_nginx_forwards_mcp_and_oauth_before_the_spa_fallback():
    nginx_config = (ROOT / "nginx.conf").read_text(encoding="utf-8")
    spa_fallback = nginx_config.index("location / {")
    locations = (
        "location = /mcp",
        "location ^~ /mcp/",
        "location ^~ /oauth/",
        "location ^~ /connectors/",
        "location ^~ /.well-known/",
    )

    for location in locations:
        match = re.search(re.escape(location) + r"\s*\{(?P<body>.*?)\n\s*\}", nginx_config, re.S)
        assert match, f"Nginx has no proxy location for {location}"
        assert match.start() < spa_fallback, f"{location} appears after the SPA fallback"
        assert "proxy_pass http://backend:8000;" in match.group("body")
