"""Network safety: reject loopback, link-local, private and reserved targets (requirements 7.4)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


class SsrfBlocked(Exception):
    pass


def _is_forbidden(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or (
            isinstance(ip, ipaddress.IPv6Address)
            and ip.ipv4_mapped is not None
            and _is_forbidden(ip.ipv4_mapped)
        )
    )


def resolve_and_check(url: str) -> list[str]:
    """Resolve the host and raise SsrfBlocked if any address is forbidden. Returns addresses."""
    host = urlsplit(url).hostname
    if not host:
        raise SsrfBlocked("no host")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_forbidden(literal):
            raise SsrfBlocked(f"{host} is a forbidden address")
        return [str(literal)]
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise SsrfBlocked(f"cannot resolve {host}: {e}") from e
    addrs: list[str] = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _is_forbidden(ip):
            raise SsrfBlocked(f"{host} resolves to forbidden address {ip}")
        addrs.append(str(ip))
    if not addrs:
        raise SsrfBlocked(f"{host} resolved to nothing")
    return addrs
