"""The release only becomes the latest when the lookup is conclusive (#1229)."""

import json
import subprocess
import time
from collections.abc import Iterator

import pytest

from scripts.ci_release_latest import main

REPO = "owner/repo"


def release(
    tag: str, *, draft: bool = False, prerelease: bool = False
) -> dict[str, object]:
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease}


def api_ok(*pages: list[dict[str, object]]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, json.dumps(list(pages)), "")


def api_error(message: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 1, "", message)


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[object]]:
    """Queue of gh API responses (the network boundary); the clock never waits."""
    responses: list[object] = []

    def run(*args: object, **kwargs: object) -> object:
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    yield responses
    assert not responses, "unused API responses"


def decide(capsys: pytest.CaptureFixture[str], tag: str) -> tuple[int, str, str]:
    code = main(["--repo", REPO, tag])
    captured = capsys.readouterr()
    return code, captured.out.strip(), captured.err


def test_1229_transient_api_failure_does_not_promote_an_older_tag(
    github: list[object], capsys: pytest.CaptureFixture[str]
) -> None:
    github += [
        api_error("gh: Server Error (HTTP 502)"),
        subprocess.TimeoutExpired("gh", 60),
        api_ok([release("v0.9.0"), release("v0.8.9")]),
    ]
    assert decide(capsys, "v0.8.10")[:2] == (0, "false")


@pytest.mark.parametrize(
    "malformed",
    [
        "<html>bad gateway",
        "[]",
        "[{}]",
        '[[{"tag_name": "v0.9.0", "draft": "false", "prerelease": false}]]',
        '[[{"tag_name": "v0.9.0", "prerelease": false}]]',
    ],
)
def test_1229_lookup_that_keeps_failing_stops_without_a_decision(
    github: list[object], capsys: pytest.CaptureFixture[str], malformed: str
) -> None:
    github += [api_error("gh: Server Error (HTTP 503)")] * 3
    github.append(subprocess.CompletedProcess([], 0, malformed, ""))
    code, out, err = decide(capsys, "v0.8.10")
    assert (code, out) == (1, "")
    assert "::error::" in err


def test_first_release_becomes_the_latest(
    github: list[object], capsys: pytest.CaptureFixture[str]
) -> None:
    github.append(api_ok([]))
    assert decide(capsys, "v0.1.0")[:2] == (0, "true")


@pytest.mark.parametrize(
    ("tag", "published", "latest"),
    [
        ("v0.8.10", [release("v0.8.9"), release("v0.8.1")], "true"),
        ("v0.8.9", [release("v0.8.10")], "false"),
        ("v1.0.0", [release("v0.10.0")], "true"),
        (
            "v0.8.10",
            [
                release("v0.9.0", draft=True),
                release("v0.9.0rc1", prerelease=True),
                release("yleaf-polarity-v2"),
            ],
            "true",
        ),
    ],
)
def test_newer_published_version_keeps_the_latest(
    github: list[object],
    capsys: pytest.CaptureFixture[str],
    tag: str,
    published: list[dict[str, object]],
    latest: str,
) -> None:
    github.append(api_ok(published))
    assert decide(capsys, tag)[:2] == (0, latest)


def test_newer_release_on_a_later_page_keeps_the_latest(
    github: list[object], capsys: pytest.CaptureFixture[str]
) -> None:
    github.append(api_ok([release("v0.8.1")] * 100, [release("v0.9.0")]))
    assert decide(capsys, "v0.8.10")[:2] == (0, "false")
