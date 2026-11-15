"""Available evidence and non-app requirements remain subject to the oracle."""

from pathlib import Path

import pytest

from scripts.ci_windows_evidence import main


@pytest.mark.parametrize("app_result", ["failure", "cancelled"])
def test_missing_app_evidence_is_omitted_only_after_upstream_app_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], app_result: str
) -> None:
    names = [
        "install-lab-windows",
        "install-lab-windows-app-tree",
        "install-lab-windows-sabotage-noop-installer",
    ]
    assert main(["--root", str(tmp_path), "--app-job-result", app_result, *names]) == 0
    captured = capsys.readouterr()
    assert captured.out.split() == [names[0], names[2]]
    assert "ci-evidence-skipped" in captured.err


@pytest.mark.parametrize("app_result", ["success", "skipped"])
def test_missing_app_evidence_is_still_required_without_an_app_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], app_result: str
) -> None:
    name = "install-lab-windows-app-tree"
    assert main(["--root", str(tmp_path), "--app-job-result", app_result, name]) == 0
    assert capsys.readouterr().out.split() == [name]


def test_available_even_invalid_app_evidence_is_always_sent_to_the_oracle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    present = "install-lab-windows-app-pinned"
    path = tmp_path / present / "results"
    path.mkdir(parents=True)
    (path / "lab-run.json").write_text("invalid evidence must be judged, not hidden")
    missing = "install-lab-windows-app-tree"
    assert (
        main(["--root", str(tmp_path), "--app-job-result", "failure", missing, present])
        == 0
    )
    assert capsys.readouterr().out.split() == [present]
