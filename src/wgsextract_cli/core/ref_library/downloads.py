from __future__ import annotations

import email.parser
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from http.client import HTTPException, IncompleteRead
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from wgsextract_cli.core.dev_download_cache import (
    restore_cached_download,
    store_download_in_dev_cache,
)
from wgsextract_cli.core.download_progress import (
    DownloadCancelled,
    DownloadIncomplete,
    copy_response_to_file,
    curl_progress_args,
    redact_urls,
    require_http_url,
    retryable_http_status,
)
from wgsextract_cli.core.network_policy import require_download
from wgsextract_cli.core.utils import WGSExtractError, run_command

from . import segmented
from .segmented import range_start


class CancelEvent(Protocol):
    def is_set(self) -> bool: ...


ProgressCallback = Callable[[int, int, float], None]

# Socket timeout (seconds) for stalled connections; not a cap on total download time.
DOWNLOAD_TIMEOUT_SECONDS = 60
# Attempts at one URL: an interrupted transfer resumes where it stopped.
DOWNLOAD_ATTEMPTS = 5
# Failures that leave a usable .partial behind.
_RESUMABLE = (DownloadIncomplete, IncompleteRead, TimeoutError, ConnectionError)


def resolve_github_release_asset_sha256(url: str) -> str | None:
    """Return GitHub's sha256 digest for a release asset URL, if applicable."""
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        return None

    parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
    if len(parts) == 6 and parts[2:4] == ["releases", "download"]:
        owner, repo, tag, asset_name = parts[0], parts[1], parts[4], parts[5]
        api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
    elif len(parts) == 6 and parts[2:5] == ["releases", "latest", "download"]:
        owner, repo, asset_name = parts[0], parts[1], parts[5]
        api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    else:
        return None

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "wgsextract-cli",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    req = Request(api_url, headers=headers)
    with urlopen(req, timeout=30) as response:
        release = json.loads(response.read().decode("utf-8"))

    for asset in release.get("assets", []):
        if asset.get("name") != asset_name:
            continue
        digest = str(asset.get("digest", ""))
        match = re.fullmatch(r"(?i)sha256:([a-f0-9]{64})", digest)
        if not match:
            raise ValueError(
                f"GitHub release asset {asset_name} did not include a sha256 digest."
            )
        return match.group(1).lower()

    raise ValueError(
        f"GitHub release asset metadata was not found for {asset_name} at {api_url}."
    )


def verify_download_sha256(path: str, expected_sha256: str | None) -> bool:
    """Verify a downloaded file against an expected SHA-256 digest."""
    if not expected_sha256:
        return True

    sha256 = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha256.update(chunk)
    except OSError as e:
        logging.error(f"Could not read downloaded file for checksum verification: {e}")
        return False

    actual_sha256 = sha256.hexdigest()
    if actual_sha256 == expected_sha256.lower():
        logging.info(f"Verified SHA-256 of {Path(path).name}: {actual_sha256}")
        return True

    logging.error("Downloaded file checksum mismatch.")
    logging.error(f"Expected SHA256: {expected_sha256.lower()}")
    logging.error(f"Actual SHA256:   {actual_sha256}")
    try:
        Path(path).unlink()
    except OSError:
        pass
    return False


def download_file(
    url: str,
    dest: str,
    progress_callback: ProgressCallback | None = None,
    cancel_event: CancelEvent | None = None,
    *,
    expected_sha256: str | None = None,
    resolve_release_digest: bool = True,
    use_cache: bool = True,
    use_mirrors: bool = True,
) -> bool:
    """Prefer audited, pinned mirrors; retain the original source as fallback."""
    require_download(Path(dest).name)
    from wgsextract_cli.core.resource_mirrors import (
        download_mirror,
        mirror_for,
        resources,
    )

    require_http_url(url, "download URL")
    if expected_sha256 is not None and not re.fullmatch(
        r"[a-fA-F0-9]{64}", expected_sha256
    ):
        raise WGSExtractError("Expected SHA256 must contain exactly 64 hex digits.")
    if use_mirrors and (entry := mirror_for(url)):
        if Path(dest + ".partial").exists():
            logging.info(
                "Resuming upstream partial for %s; skipping mirror.", entry["id"]
            )
        elif download_mirror(
            entry, dest, progress_callback, cancel_event, expected_sha256, use_cache
        ):
            return True
        if cancel_event and cancel_event.is_set():
            return False
        if expected_sha256 is None and not entry.get("upstream_mutable", False):
            expected_sha256 = entry["sha256"]
    ok = _download_file(
        url,
        dest,
        progress_callback,
        cancel_event,
        expected_sha256=expected_sha256,
        resolve_release_digest=resolve_release_digest,
        use_cache=use_cache,
    )
    if ok and use_mirrors and Path(dest + ".mirror").exists():
        try:
            shutil.rmtree(dest + ".mirror")
        except OSError as exc:
            logging.warning(
                "MIRROR_CLEANUP: upstream download installed, but mirror staging "
                "cleanup failed: %s",
                exc,
            )
    if (
        ok
        and use_mirrors
        and expected_sha256 is None
        and (entry := mirror_for(url))
        and entry.get("upstream_mutable", False)
    ):
        digest = hashlib.sha256()
        with open(dest, "rb") as source:
            for chunk in iter(lambda: source.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != entry["sha256"]:
            logging.warning(
                "UPSTREAM_VERSION_DRIFT: %s upstream differs from the mirrored "
                "snapshot; accepting current upstream bytes from %s.",
                entry["id"],
                url,
            )
    if not ok and (entry := resources().get(url)) and not entry["redistributable"]:
        logging.error(
            "UPSTREAM_ONLY: %s could not be downloaded from %s. %s "
            "No redistribution mirror is permitted; retry upstream or supply "
            "a licensed local copy.",
            entry["id"],
            url,
            entry["reason"],
        )
    return ok


def _download_file(
    url: str,
    dest: str,
    progress_callback: ProgressCallback | None = None,
    cancel_event: CancelEvent | None = None,
    *,
    expected_sha256: str | None = None,
    resolve_release_digest: bool = True,
    use_cache: bool = True,
) -> bool:
    """Resume .partial files; replace dest only after verification.

    Disabling release metadata lookup does not disable an explicit checksum.
    Disabling cache use bypasses both restoration and publication.
    """
    require_http_url(url, "download URL")
    partial_dest = dest + ".partial"
    if expected_sha256 is not None and not re.fullmatch(
        r"[a-fA-F0-9]{64}", expected_sha256
    ):
        raise WGSExtractError("Expected SHA256 must contain exactly 64 hex digits.")
    try:
        if expected_sha256 is not None:
            expected_sha256 = expected_sha256.lower()
        elif resolve_release_digest:
            expected_sha256 = resolve_github_release_asset_sha256(url)
    except OSError as e:
        logging.warning(
            "Could not resolve GitHub release asset checksum for %s: %s. "
            "Continuing without GitHub asset SHA-256 verification.",
            redact_urls(url),
            redact_urls(str(e), sources=(url,)),
        )
        expected_sha256 = None
    except ValueError as e:
        logging.error(
            "Could not resolve GitHub release asset checksum for %s: %s",
            redact_urls(url),
            redact_urls(str(e), sources=(url,)),
        )
        return False

    checksum_hint = f"sha256:{expected_sha256}" if expected_sha256 else None
    dest_path = Path(dest)
    partial_path = Path(partial_dest)
    segments_path = Path(partial_dest + ".segments")
    if partial_path.exists() and not segments_path.exists():
        _bind_single_partial(url, partial_path)
    if use_cache and not dest_path.exists() and not partial_path.exists():
        if restore_cached_download(
            url,
            dest_path,
            checksum_hint=checksum_hint,
            verify=lambda path: verify_download_sha256(str(path), expected_sha256),
        ):
            Path(partial_dest + ".source").unlink(missing_ok=True)
            return True

    # A large file over several connections (servers that cap each one), unless
    # an earlier single-connection attempt left a partial to resume.
    single_partial = partial_path.exists() and not segments_path.exists()
    if not single_partial:
        remote = segmented.ranged_revision(url)
        if remote is None and segments_path.exists():
            # A started segmented download is finished as one: its partial is
            # full-size with holes, which one connection would take as done.
            remote = segmented.saved_revision(str(segments_path), url)
        # Pieces are only safe from one version of the file, which needs a
        # validator to name it (#1225).
        size = remote.size if remote is not None and remote.validator else None
        if size is None and segments_path.exists():
            logging.info("Unfinished download has no usable state; starting over.")
            partial_path.unlink(missing_ok=True)
            segments_path.unlink(missing_ok=True)
        elif (
            remote is not None
            and size is not None
            and (size >= segmented.SEGMENTED_MIN_SIZE or segments_path.exists())
        ):
            try:
                verified = segmented.download_segmented(
                    url,
                    dest,
                    remote,
                    progress_callback,
                    cancel_event,
                    verify=lambda path: verify_download_sha256(path, expected_sha256),
                )
            except segmented.RangesUnsupported:
                logging.info("Server ignores byte ranges; using one connection.")
                for leftover in (partial_dest, partial_dest + ".segments"):
                    Path(leftover).unlink(missing_ok=True)
            except segmented.RemoteChanged as e:
                for leftover in (partial_dest, partial_dest + ".segments"):
                    Path(leftover).unlink(missing_ok=True)
                logging.error(
                    "Download error: %s changed on the server during the download "
                    "(or sent another byte range than asked); its pieces were "
                    "discarded. Run the command again to download it from the start.",
                    redact_urls(str(e), sources=(url,)),
                )
                return False
            except DownloadCancelled as e:
                logging.info(redact_urls(str(e), sources=(url,)))
                return False
            except (OSError, HTTPException) as e:
                logging.error(
                    "Download error: %s (run the command again to resume)",
                    redact_urls(str(e), sources=(url,)),
                )
                return False
            else:
                if verified:
                    Path(partial_dest + ".source").unlink(missing_ok=True)
                if verified and use_cache:
                    store_download_in_dev_cache(
                        url, dest_path, checksum_hint=checksum_hint
                    )
                return verified

    _bind_single_partial(url, partial_path)
    # Use curl only when it can surface its native progress bar directly.
    # Non-TTY runs should take the urllib path so progress is emitted as logs.
    curl_args = curl_progress_args()
    # A resume goes through urllib, which ties the bytes it appends to the
    # partial's version (If-Range); curl's -C - would append any version.
    if (
        progress_callback is None
        and cancel_event is None
        and "--progress-bar" in curl_args
        and not partial_path.exists()
    ):
        try:
            # Into dest.partial, so an interrupted curl never leaves a short
            # file at dest. -L follows redirects, --fail keeps HTTP error pages
            # out of the file. -D writes the reply's headers as they arrive,
            # so even an interrupted download records its version.
            cmd = ["curl", "-L", "--fail", *curl_args]
            cmd.extend(["-D", f"{partial_dest}.source", "-o", partial_dest, url])
            try:
                run_command(cmd, capture_output=False, redact_output_urls=True)
            finally:
                _record_curl_revision(url, partial_path)
            return _finish_download(
                url, dest, partial_dest, expected_sha256, checksum_hint, use_cache
            )
        except (OSError, subprocess.SubprocessError) as e:
            logging.warning(
                "curl download failed, falling back to urllib: %s",
                redact_urls(str(e), sources=(url,)),
            )

    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            return _urllib_download(
                url,
                dest,
                progress_callback,
                cancel_event,
                expected_sha256,
                checksum_hint,
                use_cache,
            )
        except _RESUMABLE as e:
            if attempt == DOWNLOAD_ATTEMPTS:
                logging.error(
                    f"Download error: {redact_urls(str(e), sources=(url,))} (gave up after {attempt} attempts; "
                    "run the command again to resume)"
                )
                return False
            logging.warning(
                f"Download interrupted ({redact_urls(str(e), sources=(url,))}); resuming "
                f"(attempt {attempt + 1} of {DOWNLOAD_ATTEMPTS})..."
            )
            time.sleep(min(30, 2**attempt))
    return False


def _read_source(partial: Path) -> tuple[str, segmented.Revision] | None:
    """The URL digest and version recorded beside a single-connection partial."""
    try:
        record = json.loads(Path(f"{partial}.source").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    revision = segmented.Revision.from_json(record)
    if revision is None or not isinstance(record.get("url_sha256"), str):
        return None
    return record["url_sha256"], revision


def _write_source(url: str, partial: Path, revision: segmented.Revision) -> None:
    record = {"url_sha256": hashlib.sha256(url.encode()).hexdigest()}
    Path(f"{partial}.source").write_text(
        json.dumps({**record, **revision.to_json()}) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _bind_single_partial(url: str, partial: Path) -> None:
    """Drop a partial from another URL, or of unknown source, and bind the next
    download to url, keeping the recorded version of a partial from it."""
    source = _read_source(partial)
    digest = hashlib.sha256(url.encode()).hexdigest()
    if source is None or source[0] != digest:
        if partial.exists():
            logging.info(
                "Partial download has a different or unknown source; restarting."
            )
            partial.unlink()
        source = (digest, segmented.Revision())
    _write_source(url, partial, source[1])


def _record_curl_revision(url: str, partial: Path) -> None:
    """Turn the headers curl wrote to partial.source (-D) into its record.

    A process killed before this leaves the raw headers, which _read_source
    can't read: the partial restarts, never mixing versions."""
    source = Path(f"{partial}.source")
    try:
        text = source.read_text(encoding="latin-1")
    except OSError:
        text = ""
    # The last reply after any redirects: its status line, then its headers.
    blocks = re.split(r"(?m)^(?=HTTP/)", text)
    status, _, headers = blocks[-1].partition("\n")
    revision = segmented.Revision()
    if status.split()[1:2] == ["200"]:
        revision = segmented.Revision.from_headers(
            email.parser.HeaderParser().parsestr(headers)
        )
    _write_source(url, partial, revision)


def _urllib_download(
    url: str,
    dest: str,
    progress_callback: ProgressCallback | None,
    cancel_event: CancelEvent | None,
    expected_sha256: str | None,
    checksum_hint: str | None,
    use_cache: bool = True,
) -> bool:
    """One attempt; an interrupted transfer raises one of _RESUMABLE and leaves
    dest.partial for the next attempt to resume."""
    partial_dest = dest + ".partial"
    partial_path = Path(partial_dest)
    headers = {"User-Agent": segmented.user_agent(url)}
    initial_size = 0
    source = _read_source(partial_path)
    known = source[1] if source is not None else segmented.Revision()

    if partial_path.exists() and partial_path.stat().st_size > 0:
        # Resume only the version that wrote the partial (#1225): If-Range
        # makes a server whose file changed send the whole new file. With
        # no validator, only a pinned checksum can catch a mix.
        if known.validator or expected_sha256:
            initial_size = partial_path.stat().st_size
            headers["Range"] = f"bytes={initial_size}-"
            if known.validator:
                headers["If-Range"] = known.validator
        else:
            logging.info(
                "%s: the server gave no ETag or Last-Modified to resume the "
                "partial download by; starting over.",
                Path(dest).name,
            )

    try:
        req = Request(url, headers=headers)
        try:
            response_cm = urlopen(req, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        except HTTPError as e:
            if e.code != 416 or initial_size == 0:
                raise
            e.close()
            if not _partial_is_complete(
                url, e.headers.get("Content-Range"), initial_size
            ):
                # The partial file does not fit the remote file; start over.
                partial_path.unlink()
                return _urllib_download(
                    url,
                    dest,
                    progress_callback,
                    cancel_event,
                    expected_sha256,
                    checksum_hint,
                    use_cache,
                )
            logging.info("%s is already fully downloaded.", Path(dest).name)
            return _finish_download(
                url, dest, partial_dest, expected_sha256, checksum_hint, use_cache
            )
        with response_cm as response:
            code = response.getcode()
            served = segmented.Revision.from_headers(response.info())
            changed = code == 206 and (
                range_start(response.info().get("Content-Range")) != initial_size
                or known.conflicts(served)
            )
            if not changed:
                # The whole file (200): it changed, or the server ignores ranges.
                whole = code != 206 or initial_size == 0
                if whole and initial_size > 0:
                    logging.info(
                        "Server sent the whole file instead of the rest; "
                        "starting from scratch."
                    )
                    initial_size = 0
                with open(partial_dest, "wb" if whole else "ab") as f:
                    # Only once the old version's bytes are gone (#1225).
                    if whole:
                        _write_source(url, partial_path, served)
                    copy_response_to_file(
                        response,
                        f,
                        initial_size=initial_size,
                        progress_callback=progress_callback,
                        progress_label=Path(dest).name,
                        cancel_event=cancel_event,
                    )

        if changed:
            logging.info(
                "%s changed on the server since the partial download; starting over.",
                Path(dest).name,
            )
            partial_path.unlink()
            return _urllib_download(
                url,
                dest,
                progress_callback,
                cancel_event,
                expected_sha256,
                checksum_hint,
                use_cache,
            )
        return _finish_download(
            url, dest, partial_dest, expected_sha256, checksum_hint, use_cache
        )
    except DownloadCancelled as e:
        logging.info(redact_urls(str(e), sources=(url,)))
    except _RESUMABLE:
        raise
    except URLError as e:
        if isinstance(e, HTTPError):
            e.close()
        if not isinstance(e, HTTPError) or retryable_http_status(e.code):
            raise ConnectionError(str(e)) from e
        logging.error("Download error: %s", redact_urls(str(e), sources=(url,)))
    except (
        OSError,
        HTTPException,
        subprocess.SubprocessError,
        WGSExtractError,
    ) as e:
        logging.error("Download error: %s", redact_urls(str(e), sources=(url,)))
    return False


def _partial_is_complete(
    url: str, content_range: str | None, partial_size: int
) -> bool:
    """A 416 reply to `Range: bytes=N-` means done when the remote size is N.

    The size is in the reply's Content-Range; servers that leave it out (such
    as Ensembl's) are asked with a HEAD request.
    """
    match = re.fullmatch(r"bytes \*/(\d+)", (content_range or "").strip())
    if match:
        return int(match.group(1)) == partial_size
    try:
        request = Request(
            url, method="HEAD", headers={"User-Agent": segmented.user_agent(url)}
        )
        with urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            length = response.headers.get("Content-Length")
    except (OSError, HTTPException):
        return False
    return length is not None and length.isdigit() and int(length) == partial_size


def _finish_download(
    url: str,
    dest: str,
    partial_dest: str,
    expected_sha256: str | None,
    checksum_hint: str | None,
    use_cache: bool = True,
) -> bool:
    if not verify_download_sha256(partial_dest, expected_sha256):
        Path(partial_dest + ".source").unlink(missing_ok=True)
        return False
    Path(partial_dest).replace(dest)
    Path(partial_dest + ".source").unlink(missing_ok=True)
    if use_cache:
        store_download_in_dev_cache(url, Path(dest), checksum_hint=checksum_hint)
    return True
