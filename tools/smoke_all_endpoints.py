"""Hit every parameterless GET endpoint the running app exposes.

Model, schema and response changes can break an unrelated page silently: a list
endpoint that starts returning HTTP 500 is invisible until someone opens that
page. This walks the app's own OpenAPI document and reports anything that is
not a success, so a channel pass cannot leave a dead screen behind.

Usage: python tools/smoke_all_endpoints.py
"""
import json
import sys

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
DOC = "http://127.0.0.1:8000/openapi.json"

# Endpoints that legitimately fail without live third-party credentials or a
# broker, or that need a body/path parameter we cannot invent here.
EXPECTED = {
    "/auth/me",
    # Require a real query parameter this walker does not invent.
    "/ads/search",
    "/calendar/day",
    "/email/unsubscribe",
    # Third-party credentials that a local run does not have.
    "/calls/webhooks",
    "/settings/callgate/webhooks",
    "/settings/gateway/webhooks",
}

with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as api:
    api.post("/auth/admin", json={})
    spec = httpx.get(DOC, timeout=30).json()

    targets: list[str] = []
    for path, operations in spec["paths"].items():
        if not path.startswith("/api/v1"):
            continue
        if "{" in path:
            continue  # needs a real id; covered by the feature simulators
        if "get" not in operations:
            continue
        relative = path[len("/api/v1"):] or "/"
        targets.append(relative)

    failures = []
    checked = 0
    for target in sorted(set(targets)):
        try:
            resp = api.get(target)
        except Exception as exc:  # noqa: BLE001
            failures.append((target, "EXC", str(exc)[:120]))
            continue
        checked += 1
        if resp.status_code >= 500:
            failures.append((target, resp.status_code, resp.text[:160]))
        elif resp.status_code >= 400 and target not in EXPECTED:
            failures.append((target, resp.status_code, resp.text[:120]))

    print(f"checked {checked} parameterless GET endpoints")
    for target, code, detail in failures:
        print(f"  [{code}] {target}  {detail}")
    print("\nRESULT:", "ALL CLEAN" if not failures else f"{len(failures)} PROBLEM(S)")
    sys.exit(0 if not failures else 1)
