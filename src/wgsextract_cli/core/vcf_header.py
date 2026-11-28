"""Structured VCF header attributes, including quoted commas and escapes."""

import re

_ATTRIBUTE = re.compile(r'([^,=\s]+)=("(?:[^"\\]|\\.)*"|[^,]*)')


def attributes(body: str) -> dict[str, str]:
    return dict(_ATTRIBUTE.findall(body))
