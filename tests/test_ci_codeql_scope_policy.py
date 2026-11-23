"""CodeQL PR language scoping keeps every analysis category (#1373, merge-queue starvation)."""

import re as re

from scripts.ci_codeql_scope import LANGUAGES as LANGUAGES
from tests.test_github_actions_policy import WORKFLOWS


def test_1373_codeql_pr_scoping_never_drops_a_category() -> None:
    text = (WORKFLOWS / "codeql.yml").read_text(encoding="utf-8")
    job = text[text.index("\n  analyze:\n") :]
    header = job[: job.index("    steps:")]
    matrix = re.search(r"language: \[([^\]]+)\]", header)
    assert matrix is not None
    # Every language the scope script knows runs as a matrix leg on every PR.
    assert {item.strip() for item in matrix[1].split(",")} == set(LANGUAGES)
    job_gate = re.search(r"^    if: (.*)$", header, re.MULTILINE)
    assert job_gate is None or "language" not in job_gate[1]
    # Unchanged languages re-upload the base analysis under the same category,
    # so the CodeQL check never turns neutral with "configurations not found".
    categories = re.findall(r'category: "(/language:[^"]+)"', job)
    assert categories == ["/language:${{ matrix.language }}"] * 2
    assert "github/codeql-action/upload-sarif@" in job
    fallback = "steps.reuse.outcome == 'failure'"
    for step in ("Initialize CodeQL", "Perform CodeQL analysis"):
        body = job[job.index(f"name: {step}") :].split("\n      - ", 1)[0]
        assert fallback in body and "steps.base.outcome == 'skipped'" in body
