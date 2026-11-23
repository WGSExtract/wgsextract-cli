"""Scientific regressions from PR #1026's review of issue #863."""

import json
from pathlib import Path

import numpy as np
import pytest

from tests.ancestry_helpers import NAME, build
from tests.home_isolation import state_directory_setting
from tests.htslib_helpers import bgzip_index, needs_htslib, write_fasta
from wgsextract_cli import api
from wgsextract_cli.commands.qc.ancestry import vcf_genotypes
from wgsextract_cli.core.ancestry_reference import AncestryReference
from wgsextract_cli.core.results import fingerprint
from wgsextract_cli.main import main

FORMATS = (
    '##INFO=<ID=END,Number=1,Type=Integer,Description="End">\n'
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">\n'
    '##FORMAT=<ID=MIN_DP,Number=1,Type=Integer,Description="Minimum depth">\n'
    '##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Quality">\n'
)


def _vcf(path: Path, header: str, rows: list[str], sample: bool = True) -> str:
    path.write_text(
        "##fileformat=VCFv4.2\n"
        + header
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"
        + ("\tFORMAT\tS" if sample else "")
        + "\n"
        + "\n".join(rows)
        + "\n"
    )
    return bgzip_index(path)


@needs_htslib
@pytest.mark.parametrize("model", ["gq", "depth_only"])
def test_1026_ancestry_quality_floor_does_not_depend_on_genotype(
    tmp_path: Path, model: str
) -> None:
    world = build(
        tmp_path / "panel",
        [1, 0, 0, 0, 0, 0, 0],
        contigs=("chr1",),
        sites_per_contig=9,
        length=4000,
    )
    header = "##contig=<ID=1>\n" + FORMATS
    if model == "depth_only":
        from wgsextract_cli.core.reference_evidence import Provenance

        header += Provenance("bcftools", "test", model).header() + "\n"
    rows = []
    for i, (_, pos, ref, alt) in enumerate(world["sites"]):
        depth, gq = [(3, 50), (30, 3), (30, 50)][i // 3]
        gt = ("0/0", "0/1", "1/1")[i % 3]
        rows.append(
            f"1\t{pos}\t.\t{ref}\t{alt}\t50\tPASS\t.\tGT:DP:GQ\t{gt}:{depth}:{gq}"
        )
    vcf = _vcf(tmp_path / "sample.g.vcf", header, rows)
    work = tmp_path / "work"
    work.mkdir()
    genotypes = vcf_genotypes(
        vcf,
        AncestryReference(world["reflib"] / "ancestry" / NAME),
        work,
        build="hg38",
        evidence=vcf,
        reference_fasta=str(world["fasta"]),
    )
    expected = [6, 7, 8] if model == "gq" else [3, 4, 5, 6, 7, 8]
    assert genotypes.rows.tolist() == expected
    assert np.argmax(genotypes.likelihoods, axis=1).tolist() == [
        i % 3 for i in expected
    ]


@needs_htslib
@pytest.mark.parametrize("with_fasta", [True, False])
def test_1026_ancestry_does_not_fill_incompatible_or_unverified_reference(
    tmp_path: Path, with_fasta: bool
) -> None:
    world = build(
        tmp_path / "panel",
        [1, 0, 0, 0, 0, 0, 0],
        contigs=("chr1",),
        sites_per_contig=4,
        length=4000,
    )
    rows = []
    for i, (_, pos, ref, alt) in enumerate(world["sites"]):
        # Two anchors spell REF explicitly; two sites are inside blocks.
        start = pos if i < 2 else pos - 1
        base = (alt if i == 1 else ref) if i < 2 else world["seqs"]["chr1"][start - 1]
        rows.append(
            f"chr1\t{start}\t.\t{base}\t<NON_REF>\t.\tPASS\tEND={pos}"
            "\tGT:MIN_DP:GQ\t0/0:30:50"
        )
    # The last panel REF is incompatible with the supplied FASTA.
    panel = world["reflib"] / "ancestry" / NAME
    with np.load(panel) as data:
        arrays = {key: data[key] for key in data.files}
    arrays["ref"][3] = world["sites"][3][3].encode()
    np.savez_compressed(panel, **arrays)
    vcf = _vcf(
        tmp_path / "sample.g.vcf",
        "##contig=<ID=chr1,length=4000>\n" + FORMATS,
        rows,
    )
    work = tmp_path / "work"
    work.mkdir()
    genotypes = vcf_genotypes(
        vcf,
        AncestryReference(panel),
        work,
        build="hg38",
        evidence=vcf,
        reference_fasta=str(world["fasta"]) if with_fasta else None,
    )
    assert genotypes.rows.tolist() == ([0, 2] if with_fasta else [0])


@needs_htslib
@pytest.mark.parametrize("marker", ["wgseReferenceEvidence", "GVCFBlock"])
def test_1026_qc_ancestry_recognizes_point_only_gvcf_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marker: str
) -> None:
    from wgsextract_cli.core import ancestry_reference

    world = build(tmp_path / "panel", [1, 0, 0, 0, 0, 0, 0])
    monkeypatch.setattr(ancestry_reference, "SHA256", world["sha256"])
    rows = [
        f"chr{c}\t{pos}\t.\t{ref}\t.\t.\tPASS\t.\tGT:DP:GQ\t0/0:0:50"
        for c, pos, ref, _ in world["sites"]
    ]
    vcf = _vcf(
        tmp_path / "sample.vcf",
        "".join(f"##contig=<ID={c},length=400000>\n" for c in world["contigs"])
        + f"##{marker}=<ID=reference>\n"
        + FORMATS.replace(
            '##INFO=<ID=END,Number=1,Type=Integer,Description="End">\n', ""
        ),
        rows,
    )
    result = api.qc.ancestry(
        api.QcAncestryParams(
            input=Path(vcf),
            ref=world["fasta"],
            build="hg38",
            bootstrap=0,
            threads=1,
            outdir=tmp_path / "out",
        ),
        check=False,
    )
    assert result.exit_code == 1
    assert "0" in str(result.error) and "sites" in str(result.error)


@needs_htslib
@pytest.mark.parametrize("consumer", ["clinvar", "analyze"])
@pytest.mark.parametrize("evidence_alt", ["A", "A,<NON_REF>"])
def test_1026_clinvar_evidence_uses_normalized_compatible_full_spans(
    tmp_path: Path, consumer: str, evidence_alt: str
) -> None:
    """Review: END, repeat left-alignment, incompatible hg19 chrM and
    lengthless evidence headers must not produce false reference claims."""
    ref = write_fasta(
        tmp_path / "reference.fa",
        {"chr1": "C" + "A" * 118 + "CG" * 90 + "C", "chrM": "A" * 16571},
    )
    calls = _vcf(
        tmp_path / "calls.vcf",
        "##contig=<ID=chr1,length=300>\n" + FORMATS,
        ["chr1\t100\t.\tAA\tA\t50\tPASS\t.\tGT:DP:GQ\t0/1:30:50"],
    )
    evidence = _vcf(
        tmp_path / "calls.g.vcf",
        "##contig=<ID=1>\n##contig=<ID=MT>\n" + FORMATS,
        [
            "1\t1\t.\tC\t<NON_REF>\t.\tPASS\tEND=99\tGT:MIN_DP:GQ\t0/0:30:50",
            f"1\t100\t.\tAA\t{evidence_alt}\t50\tPASS\t.\tGT:DP:GQ\t0/1:30:50",
            "1\t150\t.\tC\t<NON_REF>\t.\tPASS\tEND=150\tGT:MIN_DP:GQ\t0/0:30:50",
            "MT\t1\t.\tA\t<NON_REF>\t.\tPASS\tEND=100\tGT:MIN_DP:GQ\t0/0:30:50",
        ],
    )
    (tmp_path / "calls.manifest.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "outputs": [
                    {"path": path, "role": role, "fingerprint": fingerprint(path)}
                    for path, role in [
                        (calls, "variants"),
                        (evidence, "reference_evidence"),
                    ]
                ],
            }
        )
    )
    clinvar = _vcf(
        tmp_path / "clinvar.vcf",
        "##contig=<ID=1,length=300>\n##contig=<ID=MT,length=16569>\n"
        '##INFO=<ID=CLNSIG,Number=.,Type=String,Description="Significance">\n'
        '##INFO=<ID=END,Number=1,Type=Integer,Description="End">\n',
        [
            "1\t5\tp\tA\tG\t.\tPASS\tCLNSIG=Pathogenic",
            "1\t90\tdel\tAA\tA\t.\tPASS\tCLNSIG=Pathogenic",
            "1\t150\tsv\tC\t<DEL>\t.\tPASS\tCLNSIG=Pathogenic;END=200",
            "MT\t20\tmito\tA\tG\t.\tPASS\tCLNSIG=Pathogenic",
        ],
        sample=False,
    )
    if consumer == "clinvar":
        result = api.vcf.clinvar(
            api.VcfClinvarParams(
                vcf_input=Path(calls),
                clinvar_file=Path(clinvar),
                ref=ref,
                outdir=tmp_path / "out",
                threads=1,
            )
        )
        details = result.details
    else:
        config = tmp_path / "config.toml"
        config.write_text(
            f"clinvar_vcf_path = {json.dumps(clinvar)}\n"
            + state_directory_setting(tmp_path)
        )
        main(
            [
                "--config",
                str(config),
                "analyze",
                "comprehensive",
                "--vcf-inputs",
                calls,
                "--ref",
                str(ref),
                "--outdir",
                str(tmp_path / "out"),
                "--threads",
                "1",
            ]
        )
        details = next(
            json.loads(path.read_text())["details"]
            for path in (tmp_path / "out").glob("*.manifest.json")
            if "confidence" in (json.loads(path.read_text()).get("details") or {})
        )
    checked = details["confidence"][0]["reference_evidence"]["checked"]
    assert checked["clinvar_pathogenic_sites"] == {
        "sites": 3,
        "states": {"callable_reference": 1, "uncovered": 1, "variant": 1},
    }
    assert checked["calls"] == {"sites": 1, "states": {"variant": 1}}
