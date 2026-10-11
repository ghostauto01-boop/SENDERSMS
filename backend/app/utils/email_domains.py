"""Pure helpers about email domains: who is free-mail, what a valid domain looks like, and how to
publish a DMARC record without ever creating a second one.

Nothing here touches the network or the database.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

#: Mailbox providers that hand out addresses on THEIR domain. The app does not control their DNS, so
#: nobody can authenticate mail "from" them through a third party like Brevo: SPF/DKIM cannot align,
#: DMARC fails (these domains publish p=quarantine or p=reject) and the mail is junked or bounced.
FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com",
    "yahoo.com", "yahoo.co.uk", "yahoo.fr", "yahoo.de", "yahoo.ca", "yahoo.com.ng", "ymail.com", "rocketmail.com",
    "outlook.com", "hotmail.com", "hotmail.co.uk", "live.com", "msn.com", "outlook.fr",
    "icloud.com", "me.com", "mac.com",
    "aol.com", "aim.com",
    "proton.me", "protonmail.com", "pm.me",
    "gmx.com", "gmx.net", "gmx.de", "mail.com", "zoho.com", "zohomail.com",
    "yandex.com", "yandex.ru", "fastmail.com", "tutanota.com", "tuta.io", "hey.com",
})

_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_SEVERITY = {"none": 0, "quarantine": 1, "reject": 2}


def domain_of(address: Optional[str]) -> Optional[str]:
    """The lower-cased domain of an address, or None when there is no ``@``."""
    value = (address or "").strip().lower()
    if "@" not in value:
        return None
    return value.rpartition("@")[2].strip() or None


def is_free_mail(address: Optional[str]) -> bool:
    return domain_of(address) in FREE_MAIL_DOMAINS


def is_free_mail_domain(domain: Optional[str]) -> bool:
    return (domain or "").strip().lower() in FREE_MAIL_DOMAINS


def valid_domain(name: Optional[str]) -> bool:
    """A bare registrable-looking domain: no scheme, path, ``@`` or spaces."""
    return bool(_DOMAIN.match((name or "").strip().lower()))


# --------------------------------------------------------------------------
# DMARC
# --------------------------------------------------------------------------


def parse_dmarc(text: str) -> dict[str, str]:
    """``"v=DMARC1; p=none; rua=mailto:a@x"`` -> ``{"v": "DMARC1", "p": "none", "rua": "mailto:a@x"}`` (tag order kept)."""
    tags: dict[str, str] = {}
    for part in (text or "").split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key.strip():
            tags[key.strip().lower()] = value.strip()
    return tags


def _is_dmarc(text: str) -> bool:
    return (text or "").strip().lower().startswith("v=dmarc1")


def _uris(value: Optional[str]) -> list[str]:
    """A ``rua`` value (or several addresses) as normalised ``mailto:`` URIs, in order."""
    out: list[str] = []
    for item in re.split(r"[,\s]+", (value or "").strip()):
        item = item.strip()
        if not item:
            continue
        out.append(item if re.match(r"^[a-z][a-z0-9+.-]*:", item, re.I) else f"mailto:{item}")
    return out


def _union(*groups: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for uri in group:
            if uri.lower() not in seen:
                seen.add(uri.lower())
                merged.append(uri)
    return merged


def _render(tags: dict[str, str]) -> str:
    ordered = {"v": tags.get("v", "DMARC1"), **{k: v for k, v in tags.items() if k != "v"}}
    return "; ".join(f"{key}={value}" for key, value in ordered.items())


def merge_dmarc(existing, *, rua: Optional[str] = None, policy: Optional[str] = None) -> dict:
    """The ONE DMARC record to publish at ``_dmarc.<domain>``.

    ``existing`` is whatever TXT records are at that name (None, a string, or a list). A domain may
    publish exactly one DMARC record: with two, receivers treat DMARC as absent. So a ``rua`` address
    is *merged into* the record that is there -- every other tag untouched, a policy never weakened --
    and if the domain already (wrongly) has several, they are merged into one that replaces them all.

    Returns ``{"record", "changed", "action", "had_multiple", "existing"}`` where ``action`` is
    ``create`` (nothing there yet), ``keep`` (already has this rua), ``update`` or ``merge``.
    """
    if existing is None:
        texts: list[str] = []
    elif isinstance(existing, str):
        texts = [existing]
    else:
        texts = list(existing)
    found = [t.strip() for t in texts if _is_dmarc(t)]
    requested = _uris(rua)

    if not found:
        tags = {"v": "DMARC1", "p": (policy or "none").lower()}
        if requested:
            tags["rua"] = ",".join(requested)
        return {"record": _render(tags), "changed": True, "action": "create",
                "had_multiple": False, "existing": []}

    parsed = [parse_dmarc(t) for t in found]
    base = dict(parsed[0])
    for other in parsed[1:]:
        for key, value in other.items():
            if key == "p":
                if _SEVERITY.get(value.lower(), 0) > _SEVERITY.get(base.get("p", "none").lower(), 0):
                    base["p"] = value
            elif key not in base:
                base[key] = value
    merged_rua = _union(*(_uris(p.get("rua")) for p in parsed), requested)
    if merged_rua:
        base["rua"] = ",".join(merged_rua)

    had_multiple = len(found) > 1
    record = _render(base)
    unchanged = (not had_multiple) and parse_dmarc(record) == parsed[0]
    return {
        "record": found[0] if unchanged else record,
        "changed": not unchanged,
        "action": "merge" if had_multiple else ("keep" if unchanged else "update"),
        "had_multiple": had_multiple,
        "existing": found,
    }
