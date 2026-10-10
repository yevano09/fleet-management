"""Fleet Commander — outbound webhook URL validation (SSRF guard).

A fleet_manager can register an arbitrary webhook URL that the backend will
POST event payloads to. Without validation that is server-side request
forgery: a target like http://169.254.169.254/latest/meta-data/ would make
the backend leak cloud credentials into an attacker-readable store.

Policy:
  - scheme must be http or https (no file://, gopher://, ftp://, …)
  - no embedded credentials (user:pass@host)
  - well-known cloud-metadata hostnames / IPs are always rejected,
    even when private IPs are otherwise allowed for local dev
  - IP-literal hosts are resolved locally and rejected when they are not
    globally reachable (loopback / private / link-local / multicast /
    reserved / unspecified) unless ``webhook_allow_private_ips`` is set
  - DNS hostnames are resolved and each address checked the same way; if
    DNS resolution fails (offline dev box) the URL is allowed with a warning
    rather than breaking local development — the metadata-host blocklist
    above still applies.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Hosts that serve cloud instance metadata / credentials. Blocked always —
# even in dev profiles that otherwise allow private-range targets.
_METADATA_HOSTS = {
    "169.254.169.254",
    "169.254.169.253",
    "100.100.100.200",  # Alibaba Cloud
    "192.0.0.192",  # Oracle Cloud
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
    "instance-data-compute",
}

# Carrier-grade NAT / shared address space: globally routable per IANA but
# never a legitimate webhook target — and the Alibaba metadata service
# (100.100.100.200, which is NOT flagged private/reserved by ipaddress)
# lives inside it. Always rejected, regardless of allow_private.
_NEVER_ROUTABLE_NETS = [
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT / shared space
]


def _host_is_blocked(host: str) -> bool:
    return host.lower().rstrip(".") in _METADATA_HOSTS


def _addr_is_blocked(addr: ipaddress._BaseAddress) -> bool:
    """Unconditional rejects: metadata IPs + CGNAT space, any profile."""
    if str(addr) in _METADATA_HOSTS:
        return True
    return any(addr in net for net in _NEVER_ROUTABLE_NETS)


def _ip_allowed(ip: ipaddress._BaseAddress, allow_private: bool) -> bool:
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return False
    if isinstance(ip, ipaddress.IPv4Address) and ip.is_link_local:
        return False  # 169.254.0.0/16 — cloud metadata lives here
    if isinstance(ip, ipaddress.IPv6Address) and (
        ip.is_link_local or ip.is_site_local
    ):
        return False
    if not allow_private and (
        ip.is_private or ip.is_loopback or ip.is_link_local
    ):
        return False
    return True


def validate_webhook_url(raw_url: str, *, allow_private: bool = True) -> str:
    """Validate an outbound webhook target. Returns the normalized URL.

    Raises HTTPException(422) when the target is unsafe.
    """
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid webhook URL")

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=422, detail="Webhook URL must use http or https"
        )
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise HTTPException(status_code=422, detail="Webhook URL must include a host")
    if parsed.username or parsed.password:
        raise HTTPException(
            status_code=422, detail="Webhook URL must not embed credentials"
        )
    if _host_is_blocked(host):
        logger.warning("Rejected webhook target (metadata host): %s", host)
        raise HTTPException(
            status_code=422, detail="Webhook URL target is not allowed"
        )

    # IP literal — check directly, no DNS involved.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _addr_is_blocked(literal) or not _ip_allowed(literal, allow_private):
            logger.warning("Rejected webhook target (non-routable IP): %s", host)
            raise HTTPException(
                status_code=422, detail="Webhook URL target is not allowed"
            )
        return parsed.geturl()

    # DNS hostname — resolve and check every address (TOCTOU between check
    # and connect is accepted: this is a guard rail, not a sandbox).
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        logger.warning(
            "Could not resolve webhook host %s — allowing (offline dev?)", host
        )
        return parsed.geturl()
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _addr_is_blocked(addr) or not _ip_allowed(addr, allow_private):
            logger.warning(
                "Rejected webhook target %s (resolves to non-allowed %s)",
                host,
                addr,
            )
            raise HTTPException(
                status_code=422, detail="Webhook URL target is not allowed"
            )
    return parsed.geturl()
