"""Explicit listener and external request-header policy; no DNS lookups."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Listener:
    address: str
    port: int

    @property
    def loopback(self) -> bool:
        return ipaddress.ip_address(self.address).is_loopback

    @property
    def authority(self) -> str:
        host = f"[{self.address}]" if ":" in self.address else self.address
        return f"{host}:{self.port}"


def parse_listener(value: str, port: int) -> Listener:
    """Accept numeric IPs only: resolving a hostname must not broaden a bind."""
    try:
        if value.startswith("["):
            end = value.index("]")
            address = value[1:end]
            suffix = value[end + 1 :]
            if suffix:
                if not suffix.startswith(":"):
                    raise ValueError
                port = int(suffix[1:])
        elif value.count(":") == 1:
            address, number = value.split(":")
            port = int(number)
        else:
            address = value
        address = str(ipaddress.ip_address(address))
        if not 0 <= port <= 65535:
            raise ValueError
        return Listener(address, port)
    except ValueError:
        raise ValueError(
            "Listen must be a numeric IP address[:port] (IPv6: [address]:port)."
        ) from None


def exact_headers(
    values: str | tuple[str, ...] | None, *, origins: bool
) -> frozenset[str]:
    entries = (values,) if isinstance(values, str) else (values or ())
    for entry in entries:
        parsed = urlsplit(entry if origins else f"http://{entry}")
        try:
            valid = (
                bool(parsed.hostname)
                and parsed.scheme in ("http", "https")
                and not parsed.username
                and not parsed.password
                and not parsed.path
                and not parsed.query
                and not parsed.fragment
                and not any(c.isspace() or c in "*\\," for c in entry)
            )
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                valid = False
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                "Allowed hosts/origins must be exact authorities/origins, without wildcards."
            )
    return frozenset(entries)
