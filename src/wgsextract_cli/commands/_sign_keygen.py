"""Find an OpenSSH verifier that can read a manifest on Windows."""

import os
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from wgsextract_cli.core.utils import WGSExtractError

WINDOWS_VERIFIER_HINT = (
    "Use Git for Windows' OpenSSH (usr\\bin\\ssh-keygen.exe) or put another "
    "working ssh-keygen.exe on PATH."
)

_PROBE_TIMEOUT = 2
_PROBE_CACHE_SECONDS = 60
_PROBE_NAMESPACE = "wgsextract-probe"
_PROBE_MESSAGE = b"wgsextract-probe" * 4096
# A one-off Ed25519 signature of _PROBE_MESSAGE in _PROBE_NAMESPACE, not
# trusted for any user manifest. Use 64 KiB: affected builds can read up to
# 8 KiB from stdin and then hang on larger inputs (#1358).
_PROBE_SIGNATURE = """-----BEGIN SSH SIGNATURE-----
U1NIU0lHAAAAAQAAADMAAAALc3NoLWVkMjU1MTkAAAAgQAdyaMBcJtTrVremmmKdKEaIUx
Yx0h+tSUJz7VOCyXYAAAAQd2dzZXh0cmFjdC1wcm9iZQAAAAAAAAAGc2hhNTEyAAAAUwAA
AAtzc2gtZWQyNTUxOQAAAED/x0pLYCAIgmp5ItOaYcCStLGOm5ujHapNV073UYq8I0fRFx
epBo/zDzHT2hI18YA/JowNfCo5loW0tgP6/zEI
-----END SSH SIGNATURE-----
"""


def _windows_candidates() -> Iterator[Path]:
    keygen = "ssh-keygen.exe"
    # Prefer system installs over potentially user-writable PATH directories.
    for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        root = Path(os.environ.get(variable, ""))
        if root.is_absolute():
            yield root / "Git" / "usr" / "bin" / keygen
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    if system_root.is_absolute():
        yield system_root / "System32" / "OpenSSH" / keygen
    for entry in os.get_exec_path():
        folder = Path(entry.strip('"'))
        if folder.is_absolute():
            yield folder / keygen


@lru_cache(maxsize=32)
def _can_verify(keygen: Path, _file: tuple[int, int, int], _period: int) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        message = Path(tmp) / "message"
        message.write_bytes(_PROBE_MESSAGE)
        signature = message.with_suffix(".sig")
        signature.write_bytes(_PROBE_SIGNATURE.encode("ascii"))
        with message.open("rb") as stdin:
            try:
                result = subprocess.run(
                    [
                        keygen,
                        "-Y",
                        "check-novalidate",
                        "-n",
                        _PROBE_NAMESPACE,
                        "-s",
                        signature,
                    ],
                    stdin=stdin,
                    capture_output=True,
                    timeout=_PROBE_TIMEOUT,
                )
            except (OSError, subprocess.TimeoutExpired):
                return False
    return result.returncode == 0


def keygen_description(keygen: Path) -> str:
    # ssh-keygen has no version switch. Ask only the ssh from the same install.
    ssh = keygen.with_name(f"ssh{keygen.suffix}")
    version = "version unavailable"
    try:
        if ssh.is_file():
            result = subprocess.run(
                [ssh, "-V"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=_PROBE_TIMEOUT,
            )
            if result.returncode == 0 and result.stdout.strip():
                version = result.stdout.strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return f"{keygen} ({version})"


def windows_verifier() -> Path:
    tried: dict[Path, None] = {}
    period = int(time.monotonic() // _PROBE_CACHE_SECONDS)
    for candidate in _windows_candidates():
        try:
            keygen = candidate.resolve(strict=True)
            info = keygen.stat()
        except (OSError, RuntimeError):  # Windows access errors or a symlink loop.
            continue
        if stat.S_ISREG(info.st_mode) and keygen not in tried:
            # Reprobe replaced binaries, and don't cache transient timeouts forever.
            if _can_verify(
                keygen, (info.st_ino, info.st_size, info.st_mtime_ns), period
            ):
                return keygen
            tried[keygen] = None
    detail = (
        "Tried " + ", ".join(map(keygen_description, tried)) + ". " if tried else ""
    )
    raise WGSExtractError(
        "Signature verification FAILED: No working ssh-keygen.exe was found. "
        + detail
        + WINDOWS_VERIFIER_HINT
    )
