"""One shape for "who was not sent to, and why", shared by every send response.

A refused send used to say ``"skipped": 1`` and nothing else, and an SMS list send that hit the
rate limit part-way dropped the rest of its recipients from the response altogether.
"""

from __future__ import annotations

from collections import Counter

MAX_DETAILS = 50


def skip_summary(details: list[dict]) -> dict:
    """``details`` is ``[{"contact_id", "reason", "message"}, ...]`` in the order they were refused."""
    return {
        "skipped": len(details),
        "skipped_reason": details[0]["reason"] if details else None,
        "skipped_reasons": dict(Counter(d["reason"] for d in details)),
        "skipped_details": details[:MAX_DETAILS],
    }


def summary_sentence(sent: int, details: list[dict]) -> str:
    """A plain sentence for whoever reads only ``message``."""
    if not details:
        return f"Sent {sent}."
    why = "; ".join(
        f"{count} {reason.replace('_', ' ')}" for reason, count in Counter(d["reason"] for d in details).items()
    )
    first = details[0].get("message") or details[0]["reason"].replace("_", " ")
    if sent == 0:
        return f"Nothing was sent: {len(details)} skipped ({why}). First: {first}."
    return f"Sent {sent}; {len(details)} skipped ({why})."
