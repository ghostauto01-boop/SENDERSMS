"""Bounded, tri-state mail routing lookup shared by enrichment and validation.

A DNS timeout/SERVFAIL is not an NXDOMAIN. Never quarantine an address because
our resolver or network is unavailable. Honour null MX and RFC 5321 A/AAAA
fallback, and cache only definitive answers.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache

from app.config import settings


@dataclass(frozen=True)
class MailRoute:
    accepts_mail: bool | None
    hosts: tuple[tuple[int, str], ...] = ()


# The time bucket bounds staleness; lru_cache bounds memory for arbitrary CSVs.
@lru_cache(maxsize=4096)
def _lookup(domain: str, bucket: int) -> MailRoute:
    import dns.exception
    import dns.resolver

    timeout = max(1, min(settings.EMAIL_ENRICHMENT_TIMEOUT, 8))
    try:
        answer = dns.resolver.resolve(domain, "MX", lifetime=timeout)
        hosts = tuple(sorted(
            (int(record.preference), str(record.exchange).rstrip(".").lower())
            for record in answer
        ))
        # RFC 7505: a sole "0 ." explicitly means this domain receives no mail.
        if hosts == ((0, ""),):
            return MailRoute(False)
        hosts = tuple((priority, host) for priority, host in hosts if host)
        if hosts:
            return MailRoute(True, hosts)
    except dns.resolver.NXDOMAIN:
        return MailRoute(False)
    except dns.resolver.NoAnswer:
        pass
    # Transient failures must escape the cache (and never become False).
    except dns.exception.DNSException:
        raise

    for kind in ("A", "AAAA"):
        try:
            if dns.resolver.resolve(domain, kind, lifetime=timeout):
                return MailRoute(True, ((0, domain),))
        except dns.resolver.NXDOMAIN:
            return MailRoute(False)
        except dns.resolver.NoAnswer:
            continue
    return MailRoute(False)


def mail_route(domain: str) -> MailRoute:
    domain = domain.strip().lower().rstrip(".")
    # Reserved names cannot be publicly deliverable, even without a resolver.
    if not domain or domain.rsplit(".", 1)[-1] in {"invalid", "test", "localhost", "local"}:
        return MailRoute(False)
    try:
        ttl = max(1, settings.EMAIL_ENRICHMENT_DNS_TTL)
        return _lookup(domain, int(time.time() // ttl))
    except Exception:
        return MailRoute(None)
