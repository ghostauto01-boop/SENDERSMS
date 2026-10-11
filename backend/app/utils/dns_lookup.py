"""DNS reads for the sending-domain checks (dnspython, in a thread so the event loop stays free)."""

from __future__ import annotations

import asyncio
from typing import Optional


async def txt_records(host: str) -> Optional[list[str]]:
    """Every TXT record at ``host`` (strings joined), ``[]`` when there is none, and **None when the
    lookup itself failed** (no DNS here, a timeout). The difference matters: "there is no DMARC record"
    means it is safe to create one; "I could not look" does not.
    """

    def run() -> Optional[list[str]]:
        try:
            import dns.exception
            import dns.resolver
        except Exception:  # noqa: BLE001
            return None
        try:
            answers = dns.resolver.resolve(host, "TXT", lifetime=4.0)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except Exception:  # noqa: BLE001 -- timeout, no nameserver, SERVFAIL ...
            return None
        out = []
        for record in answers:
            out.append("".join(p.decode("utf-8", "replace") if isinstance(p, bytes) else str(p)
                               for p in record.strings))
        return out

    return await asyncio.to_thread(run)
