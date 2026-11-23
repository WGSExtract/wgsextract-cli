"""`vcf snp`, `vcf indel` and `vcf annotate` with bcftools on tiny data, and the
helpers that pick their inputs, ploidy and reference."""

import argparse
import gzip
import json
import logging
import os
import random
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.htslib_helpers import (
    bgzip_index,
    needs_htslib,
    run_wgsextract,
    sam_to_bam,
    vcf_calls,
    write_fasta,
)
from wgsextract_cli import api
from wgsextract_cli.commands.vcf import basic
from wgsextract_cli.core.config import settings
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.sample_ploidy import ploidy_args
from wgsextract_cli.core.utils import WGSExtractError, run_command, run_pipeline
from wgsextract_cli.core.variant_files import ensure_vcf_indexed
from wgsextract_cli.main import main

# A het SNP (A>G) at 500 and a het 2 bp deletion (CGT>C) at 700, each carried by
# half of 24 reads; the deletion's flanks differ, so it can't be left-shifted.
SNP_POS, DEL_POS = 500, 700


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    root = tmp_path_factory.mktemp("calls")
    rng = random.Random(11)
    seq = list("".join(rng.choice("ACGT") for _ in range(1000)))
    seq[SNP_POS - 1] = "A"
    seq[DEL_POS - 2 : DEL_POS + 4] = "ACGTCA"  # 699..704
    ref_seq = "".join(seq)
    ref = write_fasta(root / "ref.fa", {"chr1": ref_seq})

    def read(start, alt):
        """(CIGAR, bases) of a 100 bp read at ``start``, carrying its variant if alt."""
        s, cigar, bases = start - 1, "100M", ref_seq[start - 1 : start + 99]
        if alt and start < SNP_POS:
            k = SNP_POS - start
            bases = bases[:k] + "G" + bases[k + 1 :]
        elif alt:
            left = DEL_POS - start + 1  # bases up to and including POS
            cigar = f"{left}M2D{100 - left}M"
            bases = ref_seq[s:DEL_POS] + ref_seq[DEL_POS + 2 : s + 102]
        return cigar, bases

    lines = ["@SQ\tSN:chr1\tLN:1000"]
    for group, first in (("snp", 440), ("del", 650)):
        for i in range(24):
            start = first + 2 * i
            cigar, bases = read(start, alt=i % 2 == 0)
            lines.append(
                f"{group}{i}\t0\tchr1\t{start}\t60\t{cigar}\t*\t0\t0\t"
                f"{bases}\t{'I' * len(bases)}"
            )
    bam = sam_to_bam(lines, root / "sample.bam")
    return {"ref": str(ref), "bam": bam}


def _call(cmd, sample, outdir, *extra):
    run_wgsextract(
        "vcf", cmd, "--input", sample["bam"], "--ref", sample["ref"],
        "--outdir", str(outdir), "--ploidy", "GRCh38", *extra,
    )  # fmt: skip


def _mpileup_argv(vcf):
    with gzip.open(vcf, "rt") as source:
        command = next(
            line.split("=", 1)[1]
            for line in source
            if line.startswith(
                ("##bcftoolsCommand=mpileup ", "##bcftools_mpileupCommand=")
            )
        )
    return shlex.split(command.split("; Date=", 1)[0])


@needs_htslib
@pytest.mark.parametrize(
    ("cmd", "out", "expected"),
    [
        ("snp", "snps.vcf.gz", ("chr1", str(SNP_POS), "A", "G", "0/1")),
        ("indel", "indels.vcf.gz", ("chr1", str(DEL_POS), "CGT", "C", "0/1")),
    ],
)
def test_caller_finds_only_its_variant_type(sample, tmp_path, cmd, out, expected):
    _call(cmd, sample, tmp_path)
    assert vcf_calls(tmp_path / out) == [expected]
    assert (tmp_path / f"{out}.tbi").is_file()


@needs_htslib
@pytest.mark.parametrize("kind", ["snp", "indel"])
def test_982_baq_opt_in_records_choice_and_preserves_default_calls(
    sample, tmp_path, monkeypatch, kind
):
    monkeypatch.setitem(settings, "baq", False)
    bodies = {}
    cls = api.VcfSnpParams if kind == "snp" else api.VcfIndelParams
    call = api.vcf.snp if kind == "snp" else api.vcf.indel
    for mode in (None, False, True):
        output = tmp_path / str(mode)
        result = call(
            cls(
                input=Path(sample["bam"]),
                ref=Path(sample["ref"]),
                outdir=output,
                ploidy="GRCh38",
                threads=1,
                baq=mode,
                reference_evidence="gvcf",
            )
        )
        assert result.provenance.params["baq"] is bool(mode)
        vcf = output / f"{kind}s.vcf.gz"
        bodies[mode] = run_command(
            ["bcftools", "view", "-H", str(vcf)], capture_output=True
        ).stdout
        assert vcf_calls(vcf) == [
            ("chr1", "500", "A", "G", "0/1")
            if kind == "snp"
            else ("chr1", "700", "CGT", "C", "0/1")
        ]
        for called in (vcf, output / "bcftools.g.vcf.gz"):
            argv = _mpileup_argv(called)
            # contract: heuristic BAQ is enabled only by omitting mpileup -B (#982).
            assert ("-B" not in argv) is bool(mode)
            assert "-D" not in argv and "-E" not in argv
    assert bodies[None] == bodies[False]


@needs_htslib
@pytest.mark.parametrize("kind", ["snp", "indel"])
def test_982_cli_baq_overrides_persistent_choice(sample, tmp_path, monkeypatch, kind):
    from wgsextract_cli.cli.dispatch import dispatch_command
    from wgsextract_cli.cli.parser import build_parser, parse_args

    for configured, flag in ((True, "--no-baq"), (False, "--baq")):
        monkeypatch.setitem(settings, "baq", configured)
        for extra in ((), (flag,)):
            output = tmp_path / f"{configured}-{bool(extra)}"
            args, explicit = parse_args(
                *build_parser(),
                [
                    "vcf", kind, "--input", sample["bam"], "--ref", sample["ref"],
                    "--outdir", str(output), "--ploidy", "GRCh38",
                    "--threads", "1", *extra,
                ],
            )  # fmt: skip
            dispatch_command(args, explicit)
            argv = _mpileup_argv(output / f"{kind}s.vcf.gz")
            expected = not configured if extra else configured
            # contract: CLI/config BAQ selection must reach the real mpileup (#982).
            assert ("-B" not in argv) is expected
            manifest = json.loads((output / f"{kind}s.vcf.manifest.json").read_text())
            assert manifest["provenance"]["params"]["baq"] is expected


@needs_htslib
@pytest.mark.parametrize("kind", ["snp", "indel"])
def test_982_ultima_profile_cannot_silently_disable_requested_baq(
    sample, tmp_path, monkeypatch, kind
):
    header = run_command(
        ["samtools", "view", "-H", sample["bam"]], capture_output=True
    ).stdout
    header_path = tmp_path / "ultima.sam"
    header_path.write_text(header + "@RG\tID:ultima\tPL:ULTIMA\tSM:sample\n")
    bam = tmp_path / "ultima.bam"
    with bam.open("wb") as target:
        run_command(
            ["samtools", "reheader", str(header_path), sample["bam"]], stdout=target
        )
    run_command(["samtools", "index", str(bam)])
    monkeypatch.setitem(settings, "baq", True)
    output = tmp_path / "rejected"
    with pytest.raises(WGSExtractError, match="Ultima.*disables BAQ"):
        _call(
            kind,
            {"bam": str(bam), "ref": sample["ref"]},
            output,
            "--reference-evidence",
            "gvcf",
        )
    assert not output.exists()
    _call(kind, {"bam": str(bam), "ref": sample["ref"]}, tmp_path, "--no-baq")
    argv = _mpileup_argv(tmp_path / f"{kind}s.vcf.gz")
    # contract: rejecting the opt-in must not change Ultima's default profile.
    assert argv[argv.index("-X") + 1] == "ultima" and "-B" in argv


@needs_htslib
@pytest.mark.parametrize("kind", ["snp", "indel"])
@pytest.mark.parametrize("version", ["1.12", "unknown"])
def test_982_heuristic_baq_refuses_old_or_unknown_bcftools(
    sample, tmp_path, monkeypatch, kind, version
):
    from wgsextract_cli.core.utils import MissingDependencyError

    # Boundary: the installed tool's reported version, not its command execution.
    monkeypatch.setattr(basic, "get_tool_version", lambda tool: version)
    output = tmp_path / "rejected"
    with pytest.raises(MissingDependencyError, match="bcftools 1.13"):
        _call(kind, sample, output, "--baq", "--reference-evidence", "gvcf")
    assert not output.exists()
    _call(kind, sample, tmp_path, "--no-baq")
    assert vcf_calls(tmp_path / f"{kind}s.vcf.gz")


@needs_htslib
@pytest.mark.parametrize("cmd", ["snp", "indel"])
def test_caller_warns_when_its_region_has_no_variants(sample, tmp_path, caplog, cmd):
    with caplog.at_level(logging.WARNING):
        _call(cmd, sample, tmp_path, "-r", "chr1:1-100")
    assert vcf_calls(tmp_path / f"{cmd}s.vcf.gz") == []
    assert "No variants were called in chr1:1-100" in caplog.text


@needs_htslib
def test_caller_applies_the_ploidy_file(sample, tmp_path):
    ploidy = tmp_path / "ploidy.txt"
    ploidy.write_text("chr1 1 1000 M 1\nchr1 1 1000 F 1\n")
    run_wgsextract(
        "vcf", "snp", "--input", sample["bam"], "--ref", sample["ref"],
        "--outdir", str(tmp_path), "--ploidy-file", str(ploidy),
    )  # fmt: skip
    # Diploid, the site is a 0/1 het; haploid, half the reads carrying G is no call.
    assert vcf_calls(tmp_path / "snps.vcf.gz") == []


@needs_htslib
def test_vcf_indel_default_prior_keeps_low_support_het_indel_issue_860(tmp_path):
    """#860: `vcf indel` calls with a flat prior (-P 0), which raised indel
    recall on GIAB HG002 at equal precision. A 2 bp deletion in 3 of 30 reads
    must stay in indels.vcf.gz; bcftools' default prior drops it."""
    rng = random.Random(11)
    seq = list("".join(rng.choice("ACGT") for _ in range(1000)))
    seq[DEL_POS - 2 : DEL_POS + 4] = "ACGTCA"
    ref_seq = "".join(seq)
    ref = write_fasta(tmp_path / "ref.fa", {"chr1": ref_seq})
    lines = ["@SQ\tSN:chr1\tLN:1000"]
    for i in range(30):
        start, cigar = 640 + 2 * i % 50, "100M"
        bases = ref_seq[start - 1 : start + 99]
        if i < 3:
            left = DEL_POS - start + 1
            cigar = f"{left}M2D{100 - left}M"
            bases = ref_seq[start - 1 : DEL_POS] + ref_seq[DEL_POS + 2 : start + 101]
        lines.append(
            f"r{i}\t{16 * (i % 2)}\tchr1\t{start}\t60\t{cigar}\t*\t0\t0\t"
            f"{bases}\t{'I' * len(bases)}"
        )
    bam = sam_to_bam(lines, tmp_path / "low.bam")

    _call("indel", {"bam": bam, "ref": str(ref)}, tmp_path)
    assert vcf_calls(tmp_path / "indels.vcf.gz") == [
        ("chr1", str(DEL_POS), "CGT", "C", "0/1")
    ]

    # The fixture must discriminate: the same pileup under bcftools' default
    # prior (1.1e-3) calls nothing.
    bcftools = get_tool_path("bcftools")
    default_vcf = tmp_path / "default-prior.vcf"
    with default_vcf.open("wb") as out:
        run_pipeline(
            [
                [bcftools, "mpileup", "-B", "-C", "50", "-f", str(ref), bam],
                [bcftools, "call", "-m", "-v"],
            ],
            stdout=out,
        )
    calls = default_vcf.read_text().splitlines()
    assert [line for line in calls if not line.startswith("#")] == []


def test_caller_requires_input(tmp_path):
    with pytest.raises(WGSExtractError, match="--input is required"):
        run_wgsextract("vcf", "freebayes", "--outdir", str(tmp_path))


@needs_htslib
def test_caller_requires_a_reference(sample, tmp_path):
    with pytest.raises(WGSExtractError, match="--ref is required for variant calling"):
        run_wgsextract(
            "vcf", "snp", "--input", sample["bam"], "--outdir", str(tmp_path)
        )
    assert list(tmp_path.iterdir()) == []


def test_missing_ploidy_file_is_reported(tmp_path):
    lib = argparse.Namespace(ploidy_file=None, build=None)
    with pytest.raises(WGSExtractError, match="--ploidy-file"):
        ploidy_args(None, str(tmp_path / "nope.txt"), lib)  # type: ignore[arg-type]


def test_explicit_ploidy_wins_over_the_librarys_file():
    lib = argparse.Namespace(ploidy_file="/lib/ploidy.txt", build="hg38")
    assert ploidy_args("1", None, lib) == ["--ploidy", "1"]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("vcf_input", "input_path", "explicit", "expected"),
    [
        ("given.vcf.gz", "in.vcf.gz", set(), "given.vcf.gz"),
        ("default.vcf.gz", "in.vcf.gz", {"input"}, "in.vcf.gz"),
        ("default.vcf.gz", "in.vcf.gz", {"input", "vcf_input"}, "default.vcf.gz"),
        ("default.vcf.gz", "in.vcf.gz", set(), "default.vcf.gz"),
        (None, "in.vcf.gz", set(), "in.vcf.gz"),
        (None, None, set(), None),
    ],
)
def test_vcf_input_selection(monkeypatch, vcf_input, input_path, explicit, expected):
    monkeypatch.setitem(settings, "default_input_vcf", "default.vcf.gz")
    args = argparse.Namespace(
        input=input_path, vcf_input=vcf_input, _explicit_dests=explicit
    )
    assert basic._select_vcf_input(args) == expected


@needs_htslib
def test_plain_reference_uses_and_indexes_the_uncompressed_sibling(tmp_path):
    plain = tmp_path / "ref.fa"
    plain.write_text(">chr1\nACGT\n")
    with gzip.open(tmp_path / "ref.fa.gz", "wt") as f:
        f.write(">chr1\nACGT\n")
    with basic.plain_reference(str(tmp_path / "ref.fa.gz"), str(tmp_path)) as ref:
        assert ref == str(plain)
        assert Path(f"{plain}.fai").is_file()
    with basic.plain_reference(str(plain), str(tmp_path)) as ref:
        assert ref == str(plain)


def test_plain_reference_reports_a_corrupt_gzip(tmp_path):
    bad = tmp_path / "ref.fa.gz"
    bad.write_bytes(b"not gzip")
    with pytest.raises(WGSExtractError, match="uncompressed reference"):
        with basic.plain_reference(str(bad), str(tmp_path)):
            pass
    assert [p.name for p in tmp_path.iterdir()] == ["ref.fa.gz"]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("#CHROM\tPOS\tRSID\tHG\n", "CHROM,POS,ID,INFO/HG"),
        ("chr\tpos\tother\tid\n", "CHROM,POS,-,ID"),
        ("name\tvalue\n", "ID"),  # no CHROM/POS columns: the default
    ],
)
def test_annotation_columns_from_a_tab_header(tmp_path, header, expected):
    tab = tmp_path / "ann.tab.gz"
    with gzip.open(tab, "wt") as f:
        f.write(header)
    assert basic._annotation_columns(str(tab)) == expected


@needs_htslib
def test_annotation_columns_of_an_unreadable_vcf_default_to_id(tmp_path):
    vcf = tmp_path / "ann.vcf"
    vcf.write_text("not a vcf\n")
    assert basic._annotation_columns(str(vcf)) == "ID"


def _vcf(path: Path, records: list[str], info: str = "") -> str:
    path.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=1000>\n"
        + info
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        + "".join(f"{r}\n" for r in records)
    )
    return bgzip_index(path)


@needs_htslib
def test_annotate_copies_ids_and_hg_from_the_annotation_vcf(tmp_path):
    sample = _vcf(
        tmp_path / "sample.vcf",
        ["chr1\t100\t.\tA\tG\t50\tPASS\t.", "chr1\t200\t.\tC\tT\t50\tPASS\t."],
    )
    ann = _vcf(
        tmp_path / "ann.vcf",
        ["chr1\t100\trs1\tA\tG\t.\t.\tHG=R1b"],
        '##INFO=<ID=HG,Number=1,Type=String,Description="Haplogroup">\n',
    )
    outdir = tmp_path / "out"
    outdir.mkdir()
    run_wgsextract(
        "vcf", "annotate", "--input", sample, "--ann-vcf", ann, "--outdir", str(outdir)
    )
    out = outdir / "annotated.vcf.gz"
    query = ["bcftools", "query", "-f", "%POS %ID %INFO/HG\n", str(out)]
    assert run_command(query, capture_output=True).stdout.splitlines() == [
        "100 rs1 R1b",
        "200 . .",
    ]
    assert Path(f"{out}.tbi").is_file()


@needs_htslib
def test_annotate_without_an_annotation_file_fails(tmp_path):
    sample = _vcf(tmp_path / "sample.vcf", ["chr1\t100\t.\tA\tG\t50\tPASS\t."])
    with pytest.raises(WGSExtractError, match="--ann-vcf is required"):
        run_wgsextract("vcf", "annotate", "--input", sample)


def test_annotate_requires_input():
    with pytest.raises(WGSExtractError, match="--input is required"):
        run_wgsextract("vcf", "annotate", "--ann-vcf", "ann.vcf.gz")


def _write_index_test_vcf(vcf: Path, *positions: int, length: int = 100000) -> None:
    plain = vcf.with_suffix("")
    plain.write_text(
        f"##fileformat=VCFv4.2\n##contig=<ID=chr1,length={length}>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        + "".join(f"chr1\t{pos}\trs{pos}\tA\tG\t50\tPASS\t.\n" for pos in positions),
        encoding="utf-8",
    )
    with vcf.open("wb") as output:
        run_command(["bgzip", "-c", plain], stdout=output)


@pytest.fixture
def rewritten_vcf(tmp_path):
    folder = tmp_path / "input"
    folder.mkdir()
    vcf = folder / "sample.vcf.gz"
    _write_index_test_vcf(vcf, 100)
    run_command(["tabix", "-p", "vcf", vcf])
    run_command(["tabix", "-C", "-p", "vcf", vcf])
    _write_index_test_vcf(vcf, 50000)
    for ext in ("tbi", "csi"):
        os.utime(f"{vcf}.{ext}", (1_000_000, 1_000_000))
    return vcf


def _index_test_query(vcf: str, position: int = 50000) -> str:
    return run_command(
        ["bcftools", "query", "-r", f"chr1:{position}-{position}", "-f", "%ID\n", vcf],
        capture_output=True,
    ).stdout


@needs_htslib
@pytest.mark.parametrize("stale", ["csi", "tbi", "both", "short-name-csi"])
def test_1231_refreshes_coexisting_indexes_after_vcf_rewrite(rewritten_vcf, stale):
    vcf = rewritten_vcf
    if stale != "both":
        options = ["-C"] if stale == "tbi" else []
        run_command(["tabix", "-f", *options, "-p", "vcf", vcf])

    csi = Path(f"{vcf}.csi")
    if stale == "short-name-csi":
        csi = csi.rename(vcf.with_suffix(".csi"))
    stale_index = f"{vcf}.tbi" if stale == "tbi" else str(csi)
    assert _index_test_query(f"{vcf}##idx##{stale_index}") == ""
    ensure_vcf_indexed(vcf)

    assert _index_test_query(str(vcf)) == "rs50000\n"
    for index in (f"{vcf}.tbi", csi):
        assert _index_test_query(f"{vcf}##idx##{index}") == "rs50000\n"


@needs_htslib
@pytest.mark.parametrize("existing", [False, True], ids=["missing", "stale"])
def test_1231_uses_csi_when_rewritten_positions_exceed_tbi(rewritten_vcf, existing):
    vcf = rewritten_vcf
    if not existing:
        for ext in ("tbi", "csi"):
            Path(f"{vcf}.{ext}").unlink()
    _write_index_test_vcf(vcf, 600_000_000, length=700_000_000)
    ensure_vcf_indexed(vcf)

    assert _index_test_query(str(vcf), 600_000_000) == "rs600000000\n"
    assert not Path(f"{vcf}.tbi").exists()


@needs_htslib
@pytest.mark.skipif(os.name == "nt", reason="Uses a POSIX tool wrapper")
def test_1231_index_refresh_fails_when_source_changes_mid_build(
    rewritten_vcf, tmp_path, monkeypatch
):
    vcf = rewritten_vcf
    ann = _vcf(tmp_path / "ann.vcf", ["chr1\t100\trs1\tA\tG\t.\t.\t."])
    replacement = tmp_path / "replacement.vcf.gz"
    _write_index_test_vcf(replacement, 50000, 60000)
    tabix = get_tool_path("tabix")
    assert tabix is not None
    wrapper = tmp_path / "tabix"
    # Run real tabix, then replace the input after the first index build.
    wrapper.write_text(
        "#!/bin/sh\n"
        f'{shlex.quote(tabix)} "$@" || exit $?\n'
        'case "$*" in\n'
        f'  *"-p vcf"*) [ ! -f {shlex.quote(str(replacement))} ] || '
        f"mv {shlex.quote(str(replacement))} {shlex.quote(str(vcf))} ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)

    result = subprocess.run(
        [
            sys.executable, "-m", "wgsextract_cli.main", "vcf", "annotate",
            "--input", str(vcf), "--ann-vcf", ann, "--outdir", str(tmp_path),
        ], capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 1
    assert "changed while indexing" in result.stderr
    assert not (tmp_path / "annotated.vcf.gz").exists()
    assert _index_test_query(str(vcf), 60000) == ""


@needs_htslib
def test_1231_retries_after_a_backdated_rewrite_during_publication(
    rewritten_vcf, tmp_path, monkeypatch
):
    vcf = rewritten_vcf
    replacement = tmp_path / "replacement.vcf.gz"
    _write_index_test_vcf(replacement, 90000)
    os.utime(replacement, (900_000, 900_000))
    replace = Path.replace

    def swap_after_publish(path, target):
        result = replace(path, target)
        if Path(target) == Path(f"{vcf}.csi"):
            replace(replacement, vcf)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", swap_after_publish)
        with pytest.raises(WGSExtractError, match="changed while indexing"):
            ensure_vcf_indexed(vcf)

    ensure_vcf_indexed(vcf)
    assert _index_test_query(str(vcf), 90000) == "rs90000\n"


@needs_htslib
def test_1231_refresh_does_not_overwrite_a_siblings_short_index(rewritten_vcf):
    vcf = rewritten_vcf
    Path(f"{vcf}.csi").unlink()
    sibling = vcf.with_suffix(".bgz")
    _write_index_test_vcf(sibling, 90000)
    run_command(["tabix", "-C", "-p", "vcf", sibling])
    Path(f"{sibling}.csi").rename(sibling.with_suffix(".csi"))

    with pytest.raises(WGSExtractError, match="share the index name"):
        ensure_vcf_indexed(vcf)

    assert _index_test_query(str(sibling), 90000) == "rs90000\n"


@needs_htslib
@pytest.mark.parametrize(
    ("name", "other"), [("KIT.VCF.GZ", "KIT.VCF.BGZ"), ("KIT.VCF.BGZ", "KIT.VCF.GZ")]
)
def test_issue_1243_upper_case_siblings_sharing_a_short_index_are_refused(
    tmp_path, name, other
):
    # A sibling was looked up in lower case only, so on case-sensitive disks
    # KIT.VCF.GZ overwrote KIT.VCF.BGZ's short index.
    vcf, sibling = tmp_path / name, tmp_path / other
    _write_index_test_vcf(vcf, 50000)
    _write_index_test_vcf(sibling, 90000)
    run_command(["tabix", "-C", "-p", "vcf", sibling])
    Path(f"{sibling}.csi").rename(tmp_path / "KIT.VCF.csi")

    with pytest.raises(WGSExtractError, match="share the index name"):
        ensure_vcf_indexed(vcf)

    assert _index_test_query(str(sibling), 90000) == "rs90000\n"


@needs_htslib
def test_issue_1243_upper_case_vcf_refreshes_its_own_short_index(tmp_path):
    # Its own short index isn't a sibling's, whatever case the disk folds.
    vcf = tmp_path / "KIT.VCF.BGZ"
    _write_index_test_vcf(vcf, 100)
    run_command(["tabix", "-C", "-p", "vcf", vcf])
    short = Path(f"{vcf}.csi").rename(tmp_path / "KIT.VCF.csi")
    _write_index_test_vcf(vcf, 50000)
    os.utime(short, (1_000_000, 1_000_000))

    ensure_vcf_indexed(vcf)

    assert _index_test_query(str(vcf)) == "rs50000\n"


@needs_htslib
@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions")
def test_1231_refresh_preserves_restrictive_index_permissions(rewritten_vcf):
    vcf = rewritten_vcf
    csi = Path(f"{vcf}.csi")
    csi.chmod(0o600)
    previous = os.umask(0o022)
    try:
        ensure_vcf_indexed(vcf)
    finally:
        os.umask(previous)

    assert _index_test_query(str(vcf)) == "rs50000\n"
    assert stat.S_IMODE(csi.stat().st_mode) == 0o600


@needs_htslib
def test_1231_refresh_preserves_compressed_annotation_table_support(tmp_path):
    sample = _vcf(tmp_path / "sample.vcf", ["chr1\t100\t.\tA\tG\t50\tPASS\t."])
    table = tmp_path / "annotation.tab"
    table.write_text(
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t100\trs123\tA\tG\t.\t.\t.\n",
        encoding="utf-8",
    )
    compressed = tmp_path / "annotation.tab.gz"
    with compressed.open("wb") as output:
        run_command(["bgzip", "-c", table], stdout=output)

    run_wgsextract(
        "vcf", "annotate", "--input", sample, "--ann-vcf", str(compressed),
        "--cols", "CHROM,POS,ID,REF,ALT", "--outdir", str(tmp_path),
    )  # fmt: skip

    assert _index_test_query(str(tmp_path / "annotated.vcf.gz"), 100) == "rs123\n"


@needs_htslib
@pytest.mark.parametrize("problem", ["unsorted", "read-only"])
def test_1231_failed_refresh_stops_annotation(rewritten_vcf, tmp_path, capsys, problem):
    vcf = rewritten_vcf
    assert _index_test_query(str(vcf)) == ""
    ann = _vcf(tmp_path / "ann.vcf", ["chr1\t100\trs1\tA\tG\t.\t.\t."])
    outdir = tmp_path / "out"
    outdir.mkdir()
    if problem == "unsorted":
        _write_index_test_vcf(vcf, 50000, 100)
    elif os.name == "nt":
        pytest.skip("Windows chmod doesn't restrict directory writes")

    old_indexes = {p: p.read_bytes() for p in vcf.parent.glob("*.vcf.gz.*")}
    try:
        if problem == "read-only":
            vcf.parent.chmod(0o555)
            if os.access(vcf.parent, os.W_OK):
                pytest.skip("This account can write to the read-only fixture")
        with pytest.raises(SystemExit) as failure:
            main(
                ["vcf", "annotate", "--input", str(vcf), "--ann-vcf", ann,
                 "--outdir", str(outdir)]
            )  # fmt: skip
        assert failure.value.code == 1
        error = capsys.readouterr().err
        assert vcf.name in error
        assert (
            "coordinate-sorted" if problem == "unsorted" else "writable directory"
        ) in error
        assert not (outdir / "annotated.vcf.gz").exists()
        assert all(path.read_bytes() == data for path, data in old_indexes.items())
    finally:
        if problem == "read-only":
            vcf.parent.chmod(0o755)
