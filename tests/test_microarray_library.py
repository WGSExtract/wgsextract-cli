"""microarray on a library set up the normal way (`ref bootstrap` + a genome).

The test genome ships its own target file, so the rest of the suite never ran
microarray against a library: there it once resolved the bootstrap's chrY-only
SNP set as its target and wrote kits of nothing but no-calls. This builds a small
library with the bootstrap's layout (chip templates, no target file) and runs the
command end to end.
"""

import gzip
import hashlib
import json
import random
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tests.home_isolation import state_directory_setting
from tests.htslib_helpers import bgzip_index, needs_htslib, sam_to_bam, write_fasta
from tests.http_server import serving
from wgsextract_cli import api
from wgsextract_cli.commands.microarray import PACKAGED_ASSETS
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.microarray import targets

pytestmark = needs_htslib

# rsID, GRCh37 position on chr1, and the sample's genotype there.
MARKERS = [("rs101", 1000, "AC"), ("rs102", 2000, "CC"), ("rs103", 3000, "AA")]


def _library(root: Path) -> tuple[Path, str]:
    """A reference library: genomes/hg19.fa (a 5 kb chr1, each marker an A) and
    the 23andMe v3 chip template, as `ref bootstrap` lays them out."""
    rng = random.Random(7)
    seq = [rng.choice("ACGT") for _ in range(5000)]
    for _, pos, _ in MARKERS:
        seq[pos - 1] = "A"
    genomes = root / "genomes"
    genomes.mkdir(parents=True)
    fasta = write_fasta(genomes / "hg19.fa", {"chr1": "".join(seq)}, assembly="GRCh37")
    templates = root / "microarray" / "raw_file_templates"
    (templates / "head").mkdir(parents=True)
    (templates / "body").mkdir()
    (templates / "head" / "23andMe_V3.txt").write_text(
        "# rsid\tchromosome\tposition\tgenotype\n"
    )
    (templates / "body" / "23andMe_V3.txt").write_text(
        "".join(f"{rsid}\t1\t{pos}\t\n" for rsid, pos, _ in MARKERS)
    )
    # The bootstrap's Y-SNP set: once resolved as the genome-wide target.
    y_snps = root / "snps_hg19.vcf"
    y_snps.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chrY,length=59373566>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chrY\t7000000\tSYN_Y1\tG\tA\t.\tPASS\t.\n"
    )
    bgzip_index(y_snps)
    return fasta, "".join(seq)


def _sample_bam(root: Path, seq: str) -> str:
    """40 reads over each marker carrying the MARKERS genotypes."""
    lines = ["@SQ\tSN:chr1\tLN:5000", "@RG\tID:s\tSM:sample"]
    for rsid, pos, genotype in MARKERS:
        for i in range(40):
            start = pos - 60 + i
            read = list(seq[start - 1 : start - 1 + 100])
            read[pos - start] = genotype[i % 2]
            lines.append(
                f"{rsid}_{i}\t0\tchr1\t{start}\t60\t100M\t*\t0\t0\t{''.join(read)}\t"
                f"{'I' * 100}\tRG:Z:s"
            )
    return sam_to_bam(lines, root / "sample.bam")


def _identity_chain(path: Path) -> None:
    """A liftover chain under which the 5 kb chr1 keeps its coordinates."""
    path.write_bytes(
        gzip.compress(b"chain 1 chr1 5000 + 0 5000 chr1 5000 + 0 5000 1\n5000\n\n")
    )


def _shadow_packaged_bodies(library: Path) -> None:
    """Keep automatic target building, but use tiny local bodies for its union."""
    relative = Path("microarray/raw_file_templates/body")
    local = library / relative
    for bundled in (PACKAGED_ASSETS / relative).glob("*.gz"):
        name = bundled.name.removesuffix(".gz").removesuffix(".delta")
        body = local / name
        if not body.exists():
            body.write_text("rs9001\t1\t4000\n")


def test_microarray_builds_its_target_and_calls_the_chip_markers(
    tmp_path: Path,
) -> None:
    library = tmp_path / "reference"
    fasta, seq = _library(library)
    _shadow_packaged_bodies(library)
    bam = _sample_bam(tmp_path, seq)
    config = tmp_path / "config.toml"
    config.write_text(
        f'reference_library = "{library.as_posix()}"\n'
        + state_directory_setting(tmp_path)
    )
    outdir = tmp_path / "out"

    subprocess.run(
        [sys.executable, "-m", "wgsextract_cli.main", "--config", str(config)]
        + ["microarray", "--formats", "23andme_v3", "--input", bam]
        + ["--ref", str(fasta), "--outdir", str(outdir)],
        check=True,
    )

    # The genome-wide target was built from the templates and kept in the library.
    assert (library / "microarray" / "All_SNPs_hg19_ref.tab.gz.tbi").is_file()
    kit = {
        line.split("\t")[0]: line.split("\t")[3].strip()
        for line in (outdir / "sample_23andMe_V3.txt").read_text().splitlines()
        if not line.startswith("#")
    }
    assert kit == {rsid: genotype for rsid, _, genotype in MARKERS}


def test_1052_microarray_chain_download_needs_consent_and_then_reuses_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "reference"
    _, sequence = _library(library)
    _shadow_packaged_bodies(library)
    fasta = write_fasta(
        library / "genomes" / "hs38.fa", {"chr1": sequence}, assembly="GRCh38"
    )
    bam = _sample_bam(tmp_path, sequence)
    _identity_chain(library / "microarray" / "hg38ToHg19.over.chain.gz")
    chain = tmp_path / "chain.gz"
    _identity_chain(chain)
    body = chain.read_bytes()
    settings["reference_library"] = str(library)
    params = api.MicroarrayParams(
        input=Path(bam),
        ref=fasta,
        formats="23andme_v3",
        outdir=tmp_path / "out",
        threads=1,
    )
    with serving(body) as server:
        monkeypatch.setattr(
            targets,
            "HG19_TO_HG38_CHAIN",
            targets.ChainSource(
                "hg19ToHg38.over.chain.gz",
                (server.url_for("hg19ToHg38.over.chain.gz"),),
                hashlib.sha256(body).hexdigest(),
            ),
        )
        refused = api.microarray.generate(params, check=False)
        assert refused.exit_code == 2
        assert refused.error and "--download" in refused.error.message
        assert not (library / "hg19ToHg38.over.chain.gz").exists()
        permitted = api.microarray.generate(replace(params, download=True), check=False)
        assert permitted.exit_code == 0, permitted.error
    cached = api.microarray.generate(
        replace(params, outdir=tmp_path / "cached"), check=False
    )
    assert cached.exit_code == 0, cached.error
    for folder in ("out", "cached"):
        kit = {
            line.split("\t")[0]: line.split("\t")[3].strip()
            for line in (tmp_path / folder / "sample_23andMe_V3.txt")
            .read_text()
            .splitlines()
            if not line.startswith("#")
        }
        assert kit == {rsid: genotype for rsid, _, genotype in MARKERS}


def test_ref_outside_the_library_still_finds_the_library_liftover_chain(
    tmp_path: Path,
) -> None:
    """#429: `microarray --ref /elsewhere/hs38.fa` on hg38 input failed with
    "none was found in the reference library" although the configured library
    held microarray/hg38ToHg19.over.chain.gz: the FASTA's directory had become
    the library."""
    library = tmp_path / "reference"
    _, seq = _library(library)
    _shadow_packaged_bodies(library)
    _identity_chain(library / "hg19ToHg38.over.chain.gz")
    _identity_chain(library / "microarray" / "hg38ToHg19.over.chain.gz")
    (tmp_path / "elsewhere").mkdir()
    fasta = write_fasta(
        tmp_path / "elsewhere" / "hs38.fa", {"chr1": seq}, assembly="GRCh38"
    )
    bam = _sample_bam(tmp_path, seq)
    config = tmp_path / "config.toml"
    config.write_text(
        f'reference_library = "{library.as_posix()}"\n'
        + state_directory_setting(tmp_path)
    )
    outdir = tmp_path / "out"

    subprocess.run(
        [sys.executable, "-m", "wgsextract_cli.main", "--config", str(config)]
        + ["microarray", "--formats", "23andme_v3", "--input", bam]
        + ["--ref", str(fasta), "--outdir", str(outdir)],
        check=True,
    )

    kit = {
        line.split("\t")[0]: line.split("\t")[3].strip()
        for line in (outdir / "sample_23andMe_V3.txt").read_text().splitlines()
        if not line.startswith("#")
    }
    assert kit == {rsid: genotype for rsid, _, genotype in MARKERS}


def test_1380_downloads_reverse_chain_with_an_existing_hg38_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "reference"
    _, sequence = _library(library)
    fasta = write_fasta(library / "genomes" / "hs38.fa", {"chr1": sequence})
    forward_chain = library / "hg19ToHg38.over.chain.gz"
    _identity_chain(forward_chain)
    body = forward_chain.read_bytes()
    target = tmp_path / "targets.tab.gz"
    with target.open("wb") as compressed:
        subprocess.run(
            [get_tool_path("bgzip"), "-c"],
            input=(
                "#CHROM\tPOS\tID\n"
                + "".join(f"chr1\t{pos}\t{rsid}\n" for rsid, pos, _ in MARKERS)
            ).encode(),
            stdout=compressed,
            check=True,
        )
    subprocess.run(
        [get_tool_path("tabix"), "-s1", "-b2", "-e2", str(target)], check=True
    )
    vcf = tmp_path / "variants.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=5000>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Read Depth">\n'
        '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype Quality">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tsample\n"
        "chr1\t1000\trs101\tA\tC\t.\tPASS\t.\tGT:DP:GQ\t0/1:40:99\n"
    )
    settings["reference_library"] = str(library)
    params = api.MicroarrayParams(
        input=Path(bgzip_index(vcf)),
        ref=fasta,
        ref_vcf_tab=target,
        formats="23andme_v3",
        outdir=tmp_path / "out",
        threads=1,
    )
    chain_name = "hg38ToHg19.over.chain.gz"
    installed = library / chain_name
    with serving(data=None, files={chain_name: body}) as server:
        monkeypatch.setitem(
            targets._CHAINS_TO_GRCH37,
            "GRCh38",
            targets.ChainSource(
                chain_name,
                (server.url_for(chain_name),),
                hashlib.sha256(body).hexdigest(),
            ),
        )
        refused = api.microarray.generate(params, check=False)
        assert refused.exit_code == 1
        assert refused.error and chain_name in refused.error.message
        assert "--download" in refused.error.message
        assert not server.paths
        assert not installed.exists()

        permitted = api.microarray.generate(replace(params, download=True), check=False)
        assert permitted.exit_code == 0, permitted.error
        assert chain_name in server.get_paths
        assert installed.read_bytes() == body

        requests = len(server.paths)
        cached = api.microarray.generate(params, check=False)
        assert cached.exit_code == 0, cached.error
        assert len(server.paths) == requests

    kit = tmp_path / "out" / "variants_23andMe_V3.txt"
    assert "rs101\t1\t1000\tAC" in kit.read_text().splitlines()


def test_a_missing_liftover_chain_names_the_directories_searched(
    tmp_path: Path,
) -> None:
    """#429: the error said only "the reference library", not where it looked."""
    library = tmp_path / "reference"
    _, seq = _library(library)
    _identity_chain(library / "hg19ToHg38.over.chain.gz")
    (tmp_path / "elsewhere").mkdir()
    fasta = write_fasta(
        tmp_path / "elsewhere" / "hs38.fa", {"chr1": seq}, assembly="GRCh38"
    )
    bam = _sample_bam(tmp_path, seq)
    config = tmp_path / "config.toml"
    config.write_text(
        f'reference_library = "{library.as_posix()}"\n'
        + state_directory_setting(tmp_path)
    )

    result = subprocess.run(
        [sys.executable, "-m", "wgsextract_cli.main", "--config", str(config)]
        + ["microarray", "--formats", "23andme_v3", "--input", bam]
        + ["--ref", str(fasta), "--outdir", str(tmp_path / "out"), "--json"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, result.stderr
    error = json.loads(result.stdout)["error"]
    assert error["kind"] == "failure"
    message = error["message"].replace("\\", "/")
    assert "hg38ToHg19.over.chain.gz" in message
    assert (tmp_path / "elsewhere").as_posix() in message
    assert library.as_posix() in message


def test_files_beside_an_outside_fasta_come_before_the_library(
    tmp_path: Path,
) -> None:
    """#429 review: a folder an older CLI used as the library (chip templates and
    chains beside the FASTA) keeps working once a configured library, without
    those chains, is the fallback; its target is built from its own templates,
    beside it, rather than reused from the library."""
    library = tmp_path / "reference"
    _library(library)
    elsewhere = tmp_path / "elsewhere"
    _, seq = _library(elsewhere)
    _shadow_packaged_bodies(elsewhere)
    _identity_chain(elsewhere / "hg19ToHg38.over.chain.gz")
    _identity_chain(elsewhere / "microarray" / "hg38ToHg19.over.chain.gz")
    fasta = write_fasta(elsewhere / "hs38.fa", {"chr1": seq}, assembly="GRCh38")
    bam = _sample_bam(tmp_path, seq)
    config = tmp_path / "config.toml"
    config.write_text(
        f'reference_library = "{library.as_posix()}"\n'
        + state_directory_setting(tmp_path)
    )
    outdir = tmp_path / "out"

    subprocess.run(
        [sys.executable, "-m", "wgsextract_cli.main", "--config", str(config)]
        + ["microarray", "--formats", "23andme_v3", "--input", bam]
        + ["--ref", str(fasta), "--outdir", str(outdir)],
        check=True,
    )

    kit = {
        line.split("\t")[0]: line.split("\t")[3].strip()
        for line in (outdir / "sample_23andMe_V3.txt").read_text().splitlines()
        if not line.startswith("#")
    }
    assert kit == {rsid: genotype for rsid, _, genotype in MARKERS}
    assert (elsewhere / "microarray" / "All_SNPs_hg38_ref.tab.gz").is_file()
    assert not list(library.rglob("*.chain.gz"))
    assert not (library / "microarray" / "All_SNPs_hg38_ref.tab.gz").exists()
