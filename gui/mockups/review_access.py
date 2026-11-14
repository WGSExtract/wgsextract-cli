"""Network boundary for the unauthenticated, private review service."""

from __future__ import annotations

import ipaddress
from http import HTTPStatus

NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "::1/128",
        "fc00::/7",
    )
)


class BadRequest(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def allowed_client(address: str) -> bool:
    """Use the socket peer only, never proxy or caller-supplied headers."""
    try:
        ip = ipaddress.ip_address(address)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return any(ip.version == net.version and ip in net for net in NETWORKS)
    except ValueError:
        return False
