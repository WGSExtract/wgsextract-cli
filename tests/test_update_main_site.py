"""scripts/update_main_site.py moves wgsextract.github.io's v6 row forward only."""

import importlib.util
from datetime import date
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_main_site.py"
spec = importlib.util.spec_from_file_location("update_main_site", SCRIPT)
assert spec and spec.loader
site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site)

APP = "https://wgsextract.github.io/wgsextract-cli/app/"
README = f"""\
| Major | Minor | Date | md5 hash signature | User Manual (google doc) |
|:--|:-:|:-:|:-:|:-:|
| **[v5](https://get.wgse.io/x.zip)** | 48 | 30 Apr 2024 | (not yet available) | [v5](https://get.wgse.io/m) |
| **[v6]({APP})** | 0.6.6 | 6 Nov 2026 | (signed, self-checking installers) | N.A. |

v6 is a [bundled installer]({APP}).
"""


def test_moves_the_v6_row_to_the_release():
    text = site.updated(README, "0.8.6", date(2026, 11, 10))
    assert (
        f"| **[v6]({APP})** | 0.8.6 | 10 Nov 2026 | "
        "(signed, self-checking installers) | N.A. |"
    ) in text
    # Only that row changes.
    assert text.replace("0.8.6 | 10 Nov 2026", "0.6.6 | 6 Nov 2026") == README


@pytest.mark.parametrize("version", ["0.6.6", "0.6.5"])
def test_never_moves_back(version):
    assert site.updated(README, version, date(2026, 11, 10)) is None


def test_compares_versions_as_numbers():
    readme = README.replace("0.6.6", "0.9.0")
    assert site.updated(readme, "0.10.0", date(2026, 12, 1)) is not None


def test_keeps_the_files_line_endings(tmp_path):
    path = tmp_path / "README.md"
    path.write_bytes(README.replace("\n", "\r\n").encode())
    assert site.main(["update", str(path), "v0.8.6", "2026-11-10"]) == 0
    expected = README.replace("0.6.6 | 6 Nov 2026", "0.8.6 | 10 Nov 2026")
    assert path.read_bytes() == expected.replace("\n", "\r\n").encode()


def test_a_missing_row_is_an_error(tmp_path):
    path = tmp_path / "README.md"
    path.write_text(README.replace("[v6]", "[six]"))
    assert site.main(["update", str(path), "v0.8.6", "2026-11-10"]) == 1
