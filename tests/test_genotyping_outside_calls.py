from pathlib import Path

from wgsextract_cli.commands.genotyping import common


def test_outside_calls_are_utf8_and_an_empty_call_removes_a_stale_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "s.cyp2d6.pharmcat.tsv"
    assert common.write_outside_calls(str(path), [("CYP2D6", "*1/*2x≥3")])
    assert path.read_bytes() == "CYP2D6\t*1/*2x≥3\n".encode()
    assert common.write_outside_calls(str(path), []) is None
    assert not path.exists()
