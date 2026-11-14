"""microarray reuses an output only when the inputs that made it are unchanged.

Each output (the combined kit, each vendor kit) has a ``.inputs.json`` sidecar
recording the run's inputs. A rerun with the same inputs reuses it; a changed
reference or option, a newly requested format, or a missing or corrupt sidecar
regenerates exactly what is affected.
"""

import gzip
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.htslib_helpers import needs_htslib, write_fasta
from tests.smoke_utils import state_directory_setting
from tests.test_microarray_library import MARKERS, _library, _sample_bam

pytestmark = needs_htslib

V3 = "sample_23andMe_V3.txt"
V5 = "sample_23andMe_V5_2025.txt"
KIT = "sample_CombinedKit.txt"
CALLS = "sample_combined.vcf.gz"


@pytest.fixture
def run(tmp_path: Path) -> Any:
    """``run(*args)`` runs microarray on a small library's sample BAM and
    returns the output directory."""
    library = tmp_path / "reference"
    fasta, seq = _library(library)
    templates = library / "microarray" / "raw_file_templates"
    for part in ("head", "body"):
        (templates / part / "23andMe_V5_2025.txt").write_bytes(
            (templates / part / "23andMe_V3.txt").read_bytes()
        )
    bam = _sample_bam(tmp_path, seq)
    config = tmp_path / "config.toml"
    config.write_text(
        f'reference_library = "{library.as_posix()}"\n'
        + state_directory_setting(tmp_path)
    )
    outdir = tmp_path / "out"

    def microarray(*args: str, formats: str = "23andme_v3") -> Path:
        subprocess.run(
            [sys.executable, "-m", "wgsextract_cli.main", "--config", str(config)]
            + ["microarray", "--formats", formats, "--input", bam]
            + ["--ref", str(fasta), "--outdir", str(outdir), *args],
            check=True,
        )
        return outdir

    microarray.fasta = fasta  # type: ignore[attr-defined]
    return microarray


def _stamps(outdir: Path, *names: str) -> dict[str, int]:
    return {name: (outdir / name).stat().st_mtime_ns for name in names}


def _reused(outdir: Path) -> dict[str, bool]:
    """Each output file in the result manifest, and whether it was reused."""
    manifest = json.loads((outdir / "sample_microarray.manifest.json").read_text())
    return {
        Path(o["path"]).name: bool(o["metrics"].get("reused"))
        for o in manifest["outputs"]
    }


def _genotypes(path: Path) -> dict[str, str]:
    return {
        line.split("\t")[0]: line.split("\t")[3].strip()
        for line in path.read_text().splitlines()
        if not line.startswith("#")
    }


def test_unchanged_inputs_reuse_every_output(run: Any) -> None:
    outdir = run()
    assert (outdir / f"{V3}.inputs.json").is_file()
    assert (outdir / f"{KIT}.inputs.json").is_file()
    before = _stamps(outdir, V3, KIT, CALLS)

    run()

    assert _stamps(outdir, V3, KIT, CALLS) == before
    reused = _reused(outdir)
    assert reused[V3] and reused[KIT]
    assert _genotypes(outdir / V3) == {rsid: gt for rsid, _, gt in MARKERS}


def test_a_changed_option_regenerates(run: Any) -> None:
    outdir = run()

    run("--min-dp", "1000")

    # Every marker has 40 reads, so a minimum depth of 1000 no-calls them all.
    assert set(_genotypes(outdir / V3).values()) == {"--"}
    assert not _reused(outdir)[V3]
    sidecar = json.loads((outdir / f"{V3}.inputs.json").read_text())
    assert sidecar["inputs"]["quality"]["min_dp"] == 1000


def test_a_changed_reference_regenerates(run: Any) -> None:
    outdir = run()
    before = _stamps(outdir, V3, KIT)
    stat = run.fasta.stat()
    os.utime(run.fasta, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))

    run()

    after = _stamps(outdir, V3, KIT)
    assert all(after[name] != before[name] for name in before)
    reused = _reused(outdir)
    assert not reused[V3] and not reused[KIT]


def test_a_new_format_generates_only_that_format(run: Any) -> None:
    outdir = run()
    before = _stamps(outdir, V3, KIT, CALLS)

    run(formats="23andme_v3,23andme_v5_2025")

    # Converted from the kept combined kit: no genotypes were called again.
    assert _stamps(outdir, V3, KIT, CALLS) == before
    assert _genotypes(outdir / V5) == {rsid: gt for rsid, _, gt in MARKERS}
    reused = _reused(outdir)
    assert reused[V3] and reused[KIT] and not reused[V5]
    assert (outdir / f"{V5}.inputs.json").is_file()

    run(formats="23andme_v3,23andme_v5_2025")
    assert _reused(outdir)[V5]


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_a_missing_or_corrupt_sidecar_regenerates(run: Any, damage: str) -> None:
    outdir = run()
    before = _stamps(outdir, V3, KIT)
    sidecar = outdir / f"{V3}.inputs.json"
    if damage == "missing":
        sidecar.unlink()
    else:
        sidecar.write_text("{not json")

    run()

    after = _stamps(outdir, V3, KIT)
    assert after[V3] != before[V3]
    assert after[KIT] == before[KIT]  # its own sidecar is intact
    assert not _reused(outdir)[V3]
    assert json.loads(sidecar.read_text())["inputs"]["unit"] == "23andMe_V3"


def test_issue_853_reused_hg38_outputs_keep_the_liftover_counts(
    tmp_path: Path,
) -> None:
    """Issue 853: a rerun that reuses hg38 vendor kits still reports how the
    liftover placed the sites, including the ambiguous one it left out."""
    library = tmp_path / "reference"
    _, seq = _library(library)
    identity = "chain 1 chr1 5000 + 0 5000 chr1 5000 + 0 5000 1\n5000\n\n"
    (library / "hg19ToHg38.over.chain.gz").write_bytes(gzip.compress(identity.encode()))
    # rs103 (chr1:3000) gets a second hg19 placement.
    (library / "microarray" / "hg38ToHg19.over.chain.gz").write_bytes(
        gzip.compress(
            (
                identity
                + "chain 1 chr1 5000 + 2990 3010 chr1 5000 + 4000 4020 2\n20\n\n"
            ).encode()
        )
    )
    fasta = write_fasta(library / "genomes" / "hs38.fa", {"chr1": seq})
    bam = _sample_bam(tmp_path, seq)
    config = tmp_path / "config.toml"
    config.write_text(
        f'reference_library = "{library.as_posix()}"\n'
        + state_directory_setting(tmp_path)
    )
    outdir = tmp_path / "out"
    manifests = []
    for _ in range(2):
        subprocess.run(
            [sys.executable, "-m", "wgsextract_cli.main", "--config", str(config)]
            + ["microarray", "--formats", "23andme_v3", "--input", bam]
            + ["--ref", str(fasta), "--outdir", str(outdir)],
            check=True,
        )
        manifests.append(
            json.loads((outdir / "sample_microarray.manifest.json").read_text())
        )

    assert _genotypes(outdir / V3) == {"rs101": "AC", "rs102": "CC", "rs103": "--"}
    assert _reused(outdir)[V3]
    for manifest in manifests:
        metrics = manifest["metrics"]
        assert (metrics["liftover_lifted"], metrics["liftover_ambiguous"]) == (2, 1)
        assert [
            w["details"]
            for w in manifest["warnings"]
            if w["code"] == "liftover_ambiguous"
        ] == [{"ambiguous": 1, "collision": 0}]
