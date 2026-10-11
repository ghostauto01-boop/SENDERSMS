"""Bounded tool responses that are always complete JSON documents.

WHY THIS EXISTS
---------------
Tool output used to be ``text[:6000]`` of the JSON, and an HTTP body that was not
JSON (the CSV export) was reduced to ``{"raw": text[:4000]}``. A long list was
therefore cut mid-string -- no longer valid JSON -- and the export delivered 29
of 1,297 rows, and *nothing said so*. An assistant has no way to know a result is
partial unless the result says it.

The contract here:

* the response text is **always one valid JSON object** (success, error, or plain
  message), so a client can ``json.loads`` it without special cases;
* a response that is too large is shortened by **whole items** (never mid-item),
  and says so: ``cut`` records what was asked for and what was kept, and the
  response carries ``returned`` / ``total`` / ``next_cursor``;
* ``next_cursor`` is a request the caller can repeat to continue **without a gap**:
  a shortened page is continued with a page size that divides the original one,
  so the pages line up and no row is skipped or read twice;
* ``truncated`` means "more results exist beyond this response" -- true on page 1
  of 3 and false on page 3 -- and ``cut`` is what distinguishes the dangerous case
  (we shortened a page you did not ask to have shortened) from ordinary paging.

``structuredContent`` is bounded the same way, and when something was cut it
carries a ``_truncated`` marker, so a client that only reads the structured part
is told too.
"""

from __future__ import annotations

import json
from typing import Any, Optional

#: Characters of JSON allowed in one tool response (about 7-8k tokens). Large enough
#: for a comfortable page of results, small enough not to flood an assistant's context.
DEFAULT_BUDGET = 30_000

#: Room reserved for the envelope's own keys around ``data``.
_OVERHEAD = 900

#: Room kept free for the ``cut`` / ``note`` / ``next_cursor`` fields that are added
#: once a list has been shortened, so the finished response still fits the budget.
_CUT_RESERVE = 800


def _ser(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def message_envelope(text: str, *, is_error: bool = False) -> dict:
    """A plain message (a refusal, the guide, a validation problem) as a JSON object."""
    return {"ok": not is_error, "message": text}


def _error_text(payload: Any, limit: int = 2000) -> str:
    detail = None
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("message")
    text = detail if isinstance(detail, str) else _ser(payload if detail is None else detail)
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _divisors_desc(n: int):
    for d in range(n, 0, -1):
        if n % d == 0:
            yield d


def _primary_list(payload: Any) -> tuple[Optional[str], Optional[list]]:
    """Which list in ``payload`` is "the result": (key or None for a bare list, list)."""
    if isinstance(payload, list):
        return None, payload
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return "items", items
        lists = {k: v for k, v in payload.items() if isinstance(v, list) and len(v) >= 2}
        if lists:
            key = max(lists, key=lambda k: len(_ser(lists[k])))
            # Only if that list really is the bulk of the payload. A small list next
            # to a huge string (a CSV page's column names beside its text) is not
            # something to shorten: that would corrupt the result instead of cutting it.
            if len(_ser(lists[key])) * 2 >= len(_ser(payload)):
                return key, lists[key]
    return None, None


def _with_items(payload: Any, key: Optional[str], items: list) -> Any:
    if key is None:
        return items
    return {**payload, key: items}


def _paging(query: Optional[dict], payload: Any, n_items: int) -> Optional[tuple[int, int]]:
    """(page, per_page) when the result is a page of a page-numbered listing."""
    query = query or {}
    page = query.get("page")
    per_page = query.get("per_page")
    if isinstance(payload, dict):
        page = payload.get("page", page)
        per_page = payload.get("per_page", per_page)
    try:
        if per_page is None and page is None:
            return None
        return int(page or 1), int(per_page or n_items)
    except (TypeError, ValueError):
        return None


def _is_raw_body(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("raw"), str)
        and set(payload) <= {"raw", "content_type", "chars"}
    )


def build_envelope(
    method: str,
    path: str,
    status: int,
    payload: Any,
    *,
    query: Optional[dict] = None,
    budget: Optional[int] = None,
) -> tuple[dict, Any]:
    """Turn an endpoint's answer into ``(envelope, structured_content)``.

    ``envelope`` is the JSON object the tool returns as text; ``structured_content``
    is what goes in ``structuredContent`` (the payload itself, shortened the same
    way, or ``None`` for an error).
    """
    budget = budget or DEFAULT_BUDGET
    request_line = f"{method} {path}"

    if status >= 400:
        error = _error_text(payload)
        envelope: dict = {
            "ok": False,
            "http_status": status,
            "request": request_line,
            "error": error,
            "message": f"HTTP {status} from {request_line}: {error}",
        }
        if isinstance(payload, dict):
            code = payload.get("code")
            if code:
                envelope["code"] = code
            # Structured conflicts (an ambiguous id's candidates, ...) are worth
            # keeping whole; anything bulky is not.
            extras = {
                k: v for k, v in payload.items()
                if k not in ("detail", "message", "request_id", "code", "field", "hint")
            }
            if extras and len(_ser(extras)) <= 4000:
                envelope["details"] = extras
        return envelope, None

    envelope = {
        "ok": True,
        "http_status": status,
        "request": request_line,
        "truncated": False,
        "has_more": False,
        "returned": None,
        "total": None,
        "next_cursor": None,
        "cut": None,
        "note": None,
        "data": payload,
    }

    if _is_raw_body(payload):
        return _fit_raw(envelope, payload, budget)

    key, items = _primary_list(payload)
    if items is not None and key in (None, "items"):
        total = payload.get("total") if isinstance(payload, dict) else None
        total = total if isinstance(total, int) else len(items)
        envelope["returned"] = len(items)
        envelope["total"] = max(total, len(items))
    elif isinstance(payload, dict) and isinstance(payload.get("returned"), int) \
            and isinstance(payload.get("total"), int):
        # An endpoint that reports its own counts (the paginated export): surface them
        # at the top level, where a client looks for them.
        envelope["returned"] = payload["returned"]
        envelope["total"] = payload["total"]
    paging = _paging(query, payload, len(items)) if items is not None else None

    if len(_ser(envelope)) <= budget:
        _finish_uncut(envelope, payload, paging)
        structured = payload if isinstance(payload, dict) else {"items": payload}
        return envelope, structured

    # ------------------------------------------------------------------ too big
    if items is not None and len(items) >= 1:
        shrunk = _shrink_list(envelope, payload, key, items, paging, budget)
        if shrunk is not None:
            return shrunk

    return _preview(envelope, payload, budget)


def _finish_uncut(envelope: dict, payload: Any, paging: Optional[tuple[int, int]]) -> None:
    """Flags for a response that fits as-is.

    ``truncated`` means *more results exist beyond this response* -- so it is true on
    page 1 of 3 and false on the last page, where ``returned`` is smaller than
    ``total`` only because earlier pages already delivered the rest. ``next_cursor``
    is the request that continues without a gap, or null when there is nothing more.
    """
    returned, total = envelope["returned"], envelope["total"]
    cursor = None
    if isinstance(payload, dict) and "next_cursor" in payload and "truncated" in payload:
        # An endpoint that paginates itself (the contact export): believe it.
        cursor = payload["next_cursor"] or None
        truncated = bool(payload["truncated"])
    elif paging and returned is not None and total is not None:
        page, per_page = paging
        truncated = (page - 1) * per_page + returned < total
        if truncated and returned == per_page:
            cursor = {"page": page + 1, "per_page": per_page}
    else:
        truncated = bool(returned is not None and total is not None and returned < total)
        if isinstance(payload, dict) and isinstance(payload.get("next_page"), int):
            cursor = {"page": payload["next_page"]}
            truncated = True
    envelope["truncated"] = truncated
    envelope["next_cursor"] = cursor
    envelope["has_more"] = truncated or cursor is not None


def _shrink_list(
    envelope: dict, payload: Any, key: Optional[str], items: list,
    paging: Optional[tuple[int, int]], budget: int,
) -> Optional[tuple[dict, Any]]:
    """Drop items from the end until the response fits; None if even one cannot."""

    def fits(k: int) -> bool:
        trial = {**envelope, "data": _with_items(payload, key, items[:k])}
        return len(_ser(trial)) + _CUT_RESERVE <= budget

    if not fits(1):
        return None
    lo, hi = 1, len(items)  # fits(lo) is True; find the largest k that fits
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(mid):
            lo = mid
        else:
            hi = mid - 1
    kept = lo

    next_cursor = None
    note = None
    if paging:
        page, per_page = paging
        # Keep the continuation aligned with the page boundaries the caller already
        # has: a page size that divides the original one means offset (page-1)*per_page
        # is a multiple of it, so the next request starts exactly where this one stops.
        size = next(d for d in _divisors_desc(max(per_page, 1)) if d <= kept)
        kept = min(kept, size) if size >= 1 else kept
        offset = (page - 1) * per_page
        total = envelope["total"] if envelope["total"] is not None else len(items)
        if offset + kept < total:
            next_cursor = {"page": (offset + kept) // kept + 1, "per_page": kept}
        note = (
            f"This page was too large for one response, so only the first {kept} of "
            f"{len(items)} items are included. Continue with next_cursor (page "
            f"{next_cursor['page'] if next_cursor else '-'}, per_page={kept}): it picks up "
            "exactly where this response stops. Ask for a smaller per_page (or add filters) "
            "to avoid this."
        )
    else:
        note = (
            f"This result was too large for one response, so only the first {kept} of "
            f"{len(items)} items are included. Narrow the request (filters, or a smaller "
            "per_page with page) to read the rest."
        )

    data = _with_items(payload, key, items[:kept])
    envelope["data"] = data
    if key in (None, "items"):
        envelope["returned"] = kept
    else:
        envelope["returned"] = None
        envelope["total"] = None
    envelope["truncated"] = True
    envelope["cut"] = {
        "requested": len(items), "kept": kept, "unit": "items",
        "reason": f"response size limit ({budget} characters)",
    }
    if key not in (None, "items"):
        envelope["cut"]["field"] = key
    envelope["next_cursor"] = next_cursor
    envelope["has_more"] = True
    envelope["note"] = note
    marker = {
        "requested": len(items), "returned": kept, "total": envelope["total"],
        "next_cursor": next_cursor, "reason": envelope["cut"]["reason"],
    }
    structured = dict(data) if isinstance(data, dict) else {"items": data}
    structured["_truncated"] = marker
    return envelope, structured


def _preview(envelope: dict, payload: Any, budget: int) -> tuple[dict, Any]:
    """Last resort: a JSON *string* holding the start of the payload, plus its size."""
    full = _ser(payload)
    room = max(budget - _OVERHEAD - 400, 200)
    preview = full[:room]
    envelope["data"] = {"preview": preview, "chars": len(full)}
    envelope["truncated"] = True
    envelope["has_more"] = True
    envelope["returned"] = None
    envelope["total"] = None
    envelope["cut"] = {
        "requested": len(full), "kept": len(preview), "unit": "characters",
        "reason": f"response size limit ({budget} characters)",
    }
    envelope["note"] = (
        f"This result is {len(full)} characters, more than one response can carry, and it has "
        "no list to shorten, so only a preview of the start is included. Request a narrower "
        "slice (filters, a specific id, or a paginated endpoint)."
    )
    structured = {"preview": preview, "chars": len(full),
                  "_truncated": {"requested": len(full), "returned": 0, "total": None,
                                 "next_cursor": None, "reason": envelope["cut"]["reason"]}}
    return envelope, structured


def _fit_raw(envelope: dict, payload: dict, budget: int) -> tuple[dict, Any]:
    """A non-JSON body (a CSV download): keep whole lines, say how many, say what to do."""
    raw: str = payload["raw"]
    content_type = payload.get("content_type")
    chars = int(payload.get("chars") or len(raw))
    lines = raw.splitlines(keepends=True)
    envelope["total"] = len(lines)
    base = {"raw": raw, "chars": chars, "content_type": content_type}

    if len(_ser({**envelope, "data": base})) <= budget:
        envelope["returned"] = len(lines)
        envelope["data"] = base
        return envelope, base

    room = budget - _OVERHEAD - 500
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = len(_ser(line)) - 2  # JSON-escaped length without the quotes
        if used + cost > room:
            break
        kept.append(line)
        used += cost
    text = "".join(kept)
    data = {"raw": text, "chars": chars, "content_type": content_type}
    envelope["data"] = data
    envelope["returned"] = len(kept)
    envelope["truncated"] = True
    envelope["has_more"] = True
    envelope["cut"] = {
        "requested": len(lines), "kept": len(kept), "unit": "lines",
        "reason": f"response size limit ({budget} characters)",
    }
    envelope["note"] = (
        f"This endpoint returns a file, not JSON, and it is {chars} characters: only the first "
        f"{len(kept)} of {len(lines)} lines (whole lines) are included. To read all of it use a "
        "paginated JSON endpoint instead -- for contacts, the export_contacts_csv tool and its "
        "next_cursor."
    )
    structured = {**data, "_truncated": {
        "requested": len(lines), "returned": len(kept), "total": len(lines),
        "next_cursor": None, "reason": envelope["cut"]["reason"],
    }}
    return envelope, structured
