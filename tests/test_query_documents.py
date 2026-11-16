"""The public lookup boundary must not turn opaque loaded values back into Any."""

import subprocess
import sys
from pathlib import Path

from wgsextract_cli.server import queries

ROOT = Path(__file__).resolve().parents[1]


def test_929_lookup_documents_require_narrowing_and_preserve_typed_pages(
    tmp_path: Path,
) -> None:
    source = tmp_path / "lookup_contract.py"
    source.write_text(
        f"""
from typing_extensions import assert_type
from {queries.__name__} import QUERIES, query_variants
from wgsextract_cli.server.lookup_inputs import VariantInputs
from wgsextract_cli.server.operations import PathGuard
from wgsextract_cli.server.transport import VariantPage

guard = PathGuard([])
document = QUERIES["read_manifest"].run(guard, {{"path": "sample.manifest.json"}})
assert_type(document, dict[str, object])
assert_type(document["extension"], object)
page = query_variants(guard, VariantInputs(path="variants.tsv"))
assert_type(page, VariantPage)
""",
        encoding="utf-8",
    )
    checked = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--cache-dir",
            str(tmp_path / "mypy-cache"),
            str(source),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
