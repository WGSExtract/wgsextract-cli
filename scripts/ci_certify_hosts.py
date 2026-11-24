"""Registered SSH destinations for remote writer certification."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import tomllib

from scripts.ci_certifications import PLATFORMS


@dataclass(frozen=True)
class RemoteHost:
    alias: str
    platform: str

    @property
    def windows(self) -> bool:
        return self.platform == "windows-x64"


DEFAULT_HOST = RemoteHost("randy-mini", "macos-arm64")


def hosts_for(target: str, config: Path) -> list[RemoteHost]:
    hosts: list[RemoteHost] = [DEFAULT_HOST]
    if config.exists():
        try:
            with config.open("rb") as stream:
                data = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ValueError(
                f"Cannot read certification configuration {config}: {error}"
            ) from error
        section = data.get("certification", {})
        entries = section.get("hosts", []) if isinstance(section, dict) else None
        if not isinstance(entries, list):
            raise ValueError(f"{config}: expected [[certification.hosts]] entries")
        aliases: set[str] = set()
        hosts = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"alias", "platform"}:
                raise ValueError(
                    f"{config}: each certification host needs alias and platform"
                )
            alias, platform = entry["alias"], entry["platform"]
            if (
                not isinstance(alias, str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,62}", alias)
                or alias in aliases
                or alias in PLATFORMS
                or platform not in PLATFORMS
            ):
                raise ValueError(
                    f"{config}: invalid or duplicate certification SSH alias/platform"
                )
            hosts.append(RemoteHost(alias, platform))
            aliases.add(alias)
        if "randy-mini" not in aliases:
            hosts.append(DEFAULT_HOST)
    matches = [host for host in hosts if target in (host.alias, host.platform)]
    if not matches:
        raise ValueError(f"No registered certification host for {target!r} in {config}")
    return matches
