"""`qc relate` (#614): sex, relatedness and ancestry with somalier.

A tiny genome (chr1, chrX, chrY) with somalier-style sites on it, and BAMs of
a father, a mother, their son and an unrelated woman, each read carrying the
person's alleles at the sites. somalier, samtools and bcftools run for real.
"""

import hashlib
import io
import random
import sys
import tarfile
from pathlib import Path

import pytest

from tests.htslib_helpers import (
    bgzip_index,
    needs_htslib,
    needs_tools,
    sam_to_bam,
    write_fasta,
)
from wgsextract_cli import api
from wgsextract_cli.commands.qc.relate import UNCERTAIN, infer_sex
from wgsextract_cli.core import somalier_resources
from wgsextract_cli.core.dependencies import get_tool_path
from wgsextract_cli.core.utils import WGSExtractError, run_command

needs_somalier = needs_tools("somalier")
pytestmark = [
    needs_htslib,
    pytest.mark.skipif(sys.platform == "win32", reason="somalier is Unix-only"),
]

CONTIGS = {"chr1": 400_000, "chrX": 150_000, "chrY": 60_000}
STEPS = {"chr1": 200, "chrX": 300, "chrY": 1000}
SWAP = {"A": "C", "C": "G", "G": "T", "T": "A"}


def _person(rng, sites, male):
    """Two alleles (True = alt) per site; one on chrX and chrY for a male."""
    genotype = {}
    for site in sites:
        haploid = male and site[0] != "chr1"
        genotype[site] = [rng.random() < 0.5 for _ in range(1 if haploid else 2)]
    return genotype


def _son(rng, sites, father, mother):
    genotype = {}
    for site in sites:
        from_mother = rng.choice(mother[site])
        if site[0] == "chr1":
            genotype[site] = [rng.choice(father[site]), from_mother]
        elif site[0] == "chrX":
            genotype[site] = [from_mother]
        else:
            genotype[site] = father[site]
    return genotype


@pytest.fixture(scope="module")
def family(tmp_path_factory):
    """ref.fa, sites.vcf.gz and dad/mom/kid/stranger .bam (SM = file stem)."""
    work = tmp_path_factory.mktemp("family")
    rng = random.Random(614)
    seqs = {
        c: "".join(rng.choice("ACGT") for _ in range(n)) for c, n in CONTIGS.items()
    }
    write_fasta(work / "ref.fa", seqs)
    sites = [
        (c, pos, seqs[c][pos - 1], SWAP[seqs[c][pos - 1]])
        for c, step in STEPS.items()
        for pos in range(1000, CONTIGS[c] - 1000, step)
    ]
    vcf = work / "sites.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele frequency">\n'
        + "".join(f"##contig=<ID={c},length={n}>\n" for c, n in CONTIGS.items())
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        + "".join(f"{c}\t{p}\t.\t{r}\t{a}\t.\tPASS\tAF=0.5\n" for c, p, r, a in sites)
    )
    run_command([get_tool_path("bgzip"), "-f", str(vcf)])
    run_command([get_tool_path("tabix"), "-f", "-p", "vcf", f"{vcf}.gz"])
    dad, mom = _person(rng, sites, True), _person(rng, sites, False)
    people = {
        "dad": (True, dad),
        "mom": (False, mom),
        "kid": (True, _son(rng, sites, dad, mom)),
        "stranger": (False, _person(rng, sites, False)),
    }
    for name, (male, genotype) in people.items():
        lines = ["@HD\tVN:1.6\tSO:unsorted"]
        lines += [f"@SQ\tSN:{c}\tLN:{n}" for c, n in CONTIGS.items()]
        lines.append(f"@RG\tID:rg\tSM:{name}")
        for i, site in enumerate(sites):
            contig, pos, _ref, alt = site
            if contig == "chrY" and not male:
                continue
            alleles = genotype[site]
            for j in range(10 * len(alleles)):
                start = pos - rng.randint(5, 45)
                read = list(seqs[contig][start - 1 : start + 49])
                if alleles[j % len(alleles)]:
                    read[pos - start] = alt
                lines.append(
                    f"r{i}_{j}\t0\t{contig}\t{start}\t60\t50M\t*\t0\t0\t"
                    f"{''.join(read)}\t{'I' * 50}\tRG:Z:rg"
                )
        sam_to_bam(lines, work / f"{name}.bam")
        (work / f"{name}.sam").unlink()
    return work


def _relate(family, outdir, *inputs, **params):
    params.setdefault("sites", family / "sites.vcf.gz")
    return api.qc.relate(
        api.RelateParams(
            inputs=tuple(family / i if isinstance(i, str) else i for i in inputs),
            ref=family / "ref.fa",
            outdir=outdir,
            ancestry=False,
            **params,
        )
    )


def _table(path):
    header, *rows = Path(path).read_text().splitlines()
    names = header.split("\t")
    return [dict(zip(names, row.split("\t"), strict=True)) for row in rows]


def _output(result, role):
    [path] = [o.path for o in result.outputs if o.role == role]
    return path


def _pairs(result):
    rows = _table(_output(result, "relate_pairs"))
    return {frozenset((r["sample_a"], r["sample_b"])): r for r in rows}


@needs_somalier
def test_trio_relatedness_and_sex(family, tmp_path):
    """#614: parents and child are ~0.5 related, the parents ~0; sexes match."""
    result = _relate(family, tmp_path, "dad.bam", "mom.bam", "kid.bam", "stranger.bam")

    pairs = _pairs(result)
    for parent in ("dad", "mom"):
        pair = pairs[frozenset((parent, "kid"))]
        assert float(pair["relatedness"]) == pytest.approx(0.5, abs=0.1)
        assert pair["relationship"] == "parent-child"
    assert abs(float(pairs[frozenset(("dad", "mom"))]["relatedness"])) < 0.1
    assert pairs[frozenset(("kid", "stranger"))]["relationship"] == "unrelated"
    sexes = {r["sample"]: r["sex"] for r in _table(_output(result, "relate_samples"))}
    assert sexes == {
        "dad": "Male",
        "mom": "Female",
        "kid": "Male",
        "stranger": "Female",
    }
    assert result.metrics["related_pairs"] == 2
    assert result.metrics["flagged_pairs"] == 0
    assert not result.warnings
    assert (tmp_path / "relate.html").is_file()
    assert {
        Path(o.path).name for o in result.outputs if o.role == "somalier_sketch"
    } == {
        "dad.somalier",
        "mom.somalier",
        "kid.somalier",
        "stranger.somalier",
    }


@needs_somalier
def test_a_swapped_parent_is_flagged_against_the_pedigree(family, tmp_path):
    """#614: a pedigree naming the stranger as the mother flags that pair, and
    her sex if the pedigree says male."""
    first = _relate(
        family, tmp_path / "a", "dad.bam", "mom.bam", "kid.bam", "stranger.bam"
    )
    sketches = {
        Path(o.path).stem: o.path for o in first.outputs if o.role == "somalier_sketch"
    }
    ped = tmp_path / "trio.ped"
    # Space-separated, as PLINK writes .fam files.
    ped.write_text(
        "fam dad 0 0 1 -9\nfam stranger 0 0 1 -9\nfam kid dad stranger 1 -9\n"
    )

    result = _relate(
        family,
        tmp_path / "b",
        *(Path(sketches[n]) for n in ("dad", "stranger", "kid")),
        ped=ped,
    )

    codes = {
        (w.code, w.details.get("sample_b") or w.details.get("sample"))
        for w in result.warnings
    }
    assert ("relationship_mismatch", "stranger") in codes or (
        "relationship_mismatch",
        "kid",
    ) in codes
    assert ("ped_sex_mismatch", "stranger") in codes
    pairs = _pairs(result)
    assert pairs[frozenset(("kid", "stranger"))]["flagged"] == "true"
    assert pairs[frozenset(("dad", "kid"))]["flagged"] == "false"


@needs_somalier
def test_the_same_person_twice_is_flagged(family, tmp_path):
    """#614: two kits of one person are reported as the same person, even with
    the same sample name in both."""
    copy = tmp_path / "dad_kit2.bam"
    copy.write_bytes((family / "dad.bam").read_bytes())
    Path(f"{copy}.bai").write_bytes((family / "dad.bam.bai").read_bytes())

    result = _relate(family, tmp_path / "out", "dad.bam", copy, "mom.bam")

    [warning] = [w for w in result.warnings if w.code == "same_person"]
    pair = {warning.details["sample_a"], warning.details["sample_b"]}
    assert pair == {"dad_dad", "dad_kit2_dad"}
    assert warning.details["relatedness"] > 0.9


@needs_somalier
def test_a_sex_override_that_the_reads_contradict_is_flagged(family, tmp_path):
    """#614: the somalier sex is recorded beside the sample's sex, and a
    disagreement is a warning."""
    folder = tmp_path / "me"
    folder.mkdir()
    for suffix in ("", ".bai"):
        (folder / f"dad.bam{suffix}").write_bytes(
            (family / f"dad.bam{suffix}").read_bytes()
        )
    bam = folder / "dad.bam"
    api.sample.set_overrides(api.SampleSetParams(input=bam, sex="Female"))

    result = _relate(family, tmp_path / "out", bam, "mom.bam")

    [warning] = [w for w in result.warnings if w.code == "sex_mismatch"]
    assert warning.details == {
        "sample": "dad",
        "file": str(bam),
        "inferred": "Male",
        "recorded": "Female",
    }
    shown = api.sample.show(api.SampleShowParams(input=bam)).details
    assert "somalier_sex" in str(shown) and "Male" in str(shown)


def _variants_vcf(family, folder, name):
    """A provider-style VCF of the person's variants only (bcftools call -v)."""
    vcf = folder / f"{name}.vcf.gz"
    bcftools = get_tool_path("bcftools")
    run_command(
        [
            "sh",
            "-c",
            f"{bcftools} mpileup -f {family / 'ref.fa'} -T {family / 'sites.vcf.gz'}"
            f" -a AD,DP -Ou {family / f'{name}.bam'} | {bcftools} call -mv -Oz"
            f" -o {vcf}",
        ]
    )
    run_command([get_tool_path("tabix"), "-p", "vcf", str(vcf)])
    return vcf


def _relatedness(result, a, b):
    return float(_pairs(result)[frozenset((a, b))]["relatedness"])


@needs_somalier
def test_variant_only_vcfs_relate_and_their_sketches_reuse_the_same(family, tmp_path):
    """#614 review: a VCF lists only variants, so its missing sites are
    hom-ref; a sketch reused from that run gives the same relatedness."""
    vcfs = [_variants_vcf(family, tmp_path, n) for n in ("dad", "kid", "stranger")]

    first = _relate(family, tmp_path / "vcf", *vcfs)
    sketches = [o.path for o in first.outputs if o.role == "somalier_sketch"]
    again = _relate(family, tmp_path / "again", *map(Path, sketches))

    assert _relatedness(first, "dad", "kid") == pytest.approx(0.5, abs=0.1)
    assert _pairs(first)[frozenset(("dad", "kid"))]["relationship"] == "parent-child"
    assert abs(_relatedness(first, "kid", "stranger")) < 0.1
    for pair in (("dad", "kid"), ("kid", "stranger"), ("dad", "stranger")):
        assert _relatedness(again, *pair) == _relatedness(first, *pair)
    sexes = {r["sample"]: r["sex"] for r in _table(_output(first, "relate_samples"))}
    assert sexes == {"dad": "Male", "kid": "Male", "stranger": "Female"}


@needs_somalier
def test_a_vcf_and_a_bam_relate(family, tmp_path):
    """#614 review: a parent's VCF against a child's BAM is still ~0.5."""
    vcf = _variants_vcf(family, tmp_path, "dad")

    result = _relate(family, tmp_path / "out", vcf, "kid.bam", "stranger.bam")

    assert _relatedness(result, "dad", "kid") == pytest.approx(0.5, abs=0.1)
    assert abs(_relatedness(result, "dad", "stranger")) < 0.1


@needs_somalier
def test_a_renamed_sketch_keeps_its_sample(family, tmp_path):
    """#614 review: a .somalier file renamed by its user is matched by the
    sample inside it: sex and the PED check still apply."""
    first = _relate(family, tmp_path / "a", "dad.bam", "kid.bam")
    sketches = {
        Path(o.path).stem: Path(o.path)
        for o in first.outputs
        if o.role == "somalier_sketch"
    }
    renamed = tmp_path / "from-uncle-bob.somalier"
    renamed.write_bytes(sketches["dad"].read_bytes())
    ped = tmp_path / "f.ped"
    ped.write_text("f dad 0 0 2 -9\nf kid dad 0 1 -9\n")

    result = _relate(family, tmp_path / "b", renamed, sketches["kid"], ped=ped)

    rows = {r["sample"]: r for r in _table(_output(result, "relate_samples"))}
    assert rows["dad"]["sex"] == "Male"
    assert rows["dad"]["input"] == str(renamed)
    assert [
        w.details["sample"] for w in result.warnings if w.code == "ped_sex_mismatch"
    ] == ["dad"]
    assert _pairs(result)[frozenset(("dad", "kid"))]["expected_relatedness"] == "0.500"


@needs_somalier
def test_sketches_from_a_run_put_results_beside_it(family, tmp_path):
    """#614 review: rerunning on <run>/somalier/*.somalier writes into <run>,
    not <run>/somalier."""
    _relate(family, tmp_path, "dad.bam", "kid.bam")
    sketches = sorted((tmp_path / "somalier").glob("*.somalier"))

    result = api.qc.relate(
        api.RelateParams(
            inputs=tuple(sketches), sites=family / "sites.vcf.gz", ancestry=False
        )
    )

    assert Path(_output(result, "relate_pairs")).parent == tmp_path
    assert not (tmp_path / "somalier" / "relate.pairs.tsv").exists()
    assert not (tmp_path / "somalier" / "somalier").exists()


@needs_somalier
def test_ancestry_without_the_1kg_sketches_says_how_to_install_them(family, tmp_path):
    result = api.qc.relate(
        api.RelateParams(
            inputs=(family / "dad.bam",),
            ref=family / "ref.fa",
            outdir=tmp_path,
            ancestry=True,
        ),
        check=False,
    )
    assert result.status == "failed"
    assert "ref somalier --install" in result.error.message


def test_windows_is_refused_with_a_reason(family, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    result = api.qc.relate(
        api.RelateParams(inputs=(family / "dad.bam",), outdir=tmp_path), check=False
    )
    assert result.status == "failed"
    assert "not available on Windows" in result.error.message


# --- ref somalier: pinned downloads (the network is faked) ----------------


@pytest.fixture
def fake_somalier_downloads(monkeypatch):
    """Serve stand-ins for the sites, labels and the sketch archive, pinned to
    their digests. Call it, optionally with extra archive members."""
    monkeypatch.setattr(somalier_resources, "SKETCH_COUNT", 3)
    payloads = {}

    def serve(extra=None):
        members = {f"1kg-somalier/S{i}.somalier": b"sketch" for i in range(3)}
        members.update(extra or {})
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            for name, data in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))

        def pin(resource, data):
            payloads[resource.url] = data
            digest = hashlib.sha256(data).hexdigest()
            return somalier_resources.Resource(resource.name, resource.url, digest)

        sites = {
            k: pin(r, r.name.encode()) for k, r in somalier_resources.SITES.items()
        }
        labels = somalier_resources.LABELS
        monkeypatch.setattr(somalier_resources, "SITES", sites)
        monkeypatch.setattr(somalier_resources, "LABELS", pin(labels, b"labels"))
        sketches = pin(somalier_resources.SKETCHES, buffer.getvalue())
        monkeypatch.setattr(somalier_resources, "SKETCHES", sketches)
        content = {
            name.rpartition("/")[2]: hashlib.sha256(data).hexdigest()
            for name, data in members.items()
        }
        monkeypatch.setattr(
            somalier_resources,
            "SKETCHES_CONTENT_SHA256",
            somalier_resources.content_digest(content),
        )

    def fetch(url, dest, *args, expected_sha256=None, **kwargs):
        data = payloads[url]
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            return False
        Path(dest).write_bytes(data)
        return True

    monkeypatch.setattr(somalier_resources, "download_file", fetch)
    return serve


def test_ref_somalier_installs_and_deletes(tmp_path, fake_somalier_downloads):
    fake_somalier_downloads()
    result = api.ref.somalier(api.RefSomalierParams(ref=tmp_path))

    folder = tmp_path / "somalier"
    assert (folder / "sites.hg38.vcf.gz").is_file()
    assert sorted(p.name for p in (folder / "1kg-somalier").glob("*.somalier")) == [
        "S0.somalier",
        "S1.somalier",
        "S2.somalier",
    ]
    assert not (folder / "1kg.somalier.tar.gz").exists()
    assert {o.role for o in result.outputs} == {"reference_resource"}
    labels, sketches = somalier_resources.require_ancestry(str(tmp_path))
    assert labels.is_file() and len(sketches) == 3

    api.ref.somalier(api.RefSomalierParams(ref=tmp_path, delete=True))
    assert not folder.exists()


def test_ref_somalier_refuses_an_archive_with_paths_outside(
    tmp_path, fake_somalier_downloads
):
    fake_somalier_downloads({"1kg-somalier/../../escape.somalier": b"x"})
    result = api.ref.somalier(api.RefSomalierParams(ref=tmp_path), check=False)

    assert result.status == "failed"
    assert "Unexpected entry" in result.error.message
    assert not (tmp_path / "escape.somalier").exists()
    assert not (tmp_path / "somalier" / "1kg-somalier").exists()


def test_a_modified_sites_file_is_refused(tmp_path, fake_somalier_downloads):
    fake_somalier_downloads()
    api.ref.somalier(api.RefSomalierParams(ref=tmp_path))
    sites = tmp_path / "somalier" / "sites.hg38.vcf.gz"
    sites.write_bytes(b"tampered")
    resource = somalier_resources.SITES["hg38", True]

    with pytest.raises(WGSExtractError, match="SHA256 mismatch"):
        somalier_resources.require(str(tmp_path), resource)


def test_a_changed_1kg_sketch_is_refused(tmp_path, fake_somalier_downloads):
    """#614 review: the labelled sketches are checked on every use."""
    fake_somalier_downloads()
    api.ref.somalier(api.RefSomalierParams(ref=tmp_path))
    somalier_resources.require_ancestry(str(tmp_path))
    (tmp_path / "somalier" / "1kg-somalier" / "S1.somalier").write_bytes(b"changed")

    with pytest.raises(WGSExtractError, match="changed file, S1.somalier"):
        somalier_resources.require_ancestry(str(tmp_path))


def test_deps_check_says_somalier_is_unavailable_on_windows(monkeypatch):
    """#614: on Windows, deps check lists somalier as not built for it."""
    from wgsextract_cli.core.tool_catalog import platform_unavailable_tools

    assert "somalier" not in platform_unavailable_tools()
    monkeypatch.setattr(sys, "platform", "win32")
    assert "somalier" in platform_unavailable_tools()


# --- review of 160a0850: VCF coverage, names and publishing ----------------


def _region_vcf(family, folder, name, region, out_name):
    """A provider VCF of ``name``'s variants in ``region`` only."""
    (folder / "full").mkdir(exist_ok=True)
    full = _variants_vcf(family, folder / "full", name)
    vcf = folder / out_name
    bcftools = get_tool_path("bcftools")
    run_command([bcftools, "view", "-r", region, "-Oz", "-o", str(vcf), str(full)])
    run_command([get_tool_path("tabix"), "-p", "vcf", str(vcf)])
    return vcf


@needs_somalier
def test_a_one_region_vcf_is_not_filled_and_claims_no_relationship(family, tmp_path):
    """#614 re-review: a VCF of one region against the same person's and the
    son's genome BAMs: its missing sites aren't made hom-ref, no relationship
    is claimed from its called sites, and it is warned about."""
    vcf = _region_vcf(family, tmp_path, "dad", "chr1:1-60000", "dad_part.vcf.gz")

    result = _relate(family, tmp_path / "out", vcf, "dad.bam", "kid.bam")

    [warning] = [w for w in result.warnings if w.code == "vcf_called_sites_only"]
    assert warning.details["sample"] == "dad_part_dad"
    assert warning.details["site_ratio"] < 0.3
    pairs = _pairs(result)
    for other in ("dad_dad", "kid"):
        pair = pairs[frozenset(("dad_part_dad", other))]
        assert pair["relationship"] in ("too few shared sites", UNCERTAIN)
        assert pair["flagged"] == "false"
    assert not [w for w in result.warnings if w.code == "same_person"]
    rows = {r["sample"]: r for r in _table(_output(result, "relate_samples"))}
    assert rows["dad_part_dad"]["called_sites_only"] == "true"
    assert rows["dad_dad"]["called_sites_only"] == "false"


@needs_somalier
def test_a_gvcf_is_refused(family, tmp_path):
    """#614 re-review: gVCF no-call blocks can't be told from reference."""
    gvcf = tmp_path / "dad.g.vcf.gz"
    bcftools = get_tool_path("bcftools")
    run_command(
        [
            "sh",
            "-c",
            f"{bcftools} mpileup -f {family / 'ref.fa'} -a AD,DP -Ou "
            f"{family / 'dad.bam'} | {bcftools} call -m --gvcf 0 -Oz -o {gvcf}",
        ]
    )
    run_command([get_tool_path("tabix"), "-p", "vcf", str(gvcf)])

    result = api.qc.relate(
        api.RelateParams(
            inputs=(gvcf,),
            ref=family / "ref.fa",
            outdir=tmp_path / "out",
            sites=family / "sites.vcf.gz",
            ancestry=False,
        ),
        check=False,
    )

    assert result.status == "failed"
    assert "gVCF" in result.error.message


@needs_somalier
def test_another_record_at_a_site_doesnt_hide_its_snp(family, tmp_path):
    """#614 re-review: a second record at a site's position (another ALT,
    as in split multiallelics) doesn't replace the record of the site's SNP."""
    vcf = _variants_vcf(family, tmp_path, "dad")
    plain = tmp_path / "decoyed.vcf"
    lines = []
    text = run_command(
        [get_tool_path("bcftools"), "view", str(vcf)], capture_output=True
    ).stdout
    for line in text.splitlines():
        if line.startswith("#"):
            lines.append(line)
            continue
        fields = line.split("\t")
        lines.append(line)
        decoy = [c for c in "ACGT" if c not in (fields[3], fields[4])][0]
        lines.append(
            "\t".join(
                [
                    *fields[:4],
                    decoy,
                    *fields[5:9],
                    "0/0:30,0" if fields[8] == "GT:AD" else fields[9],
                ]
            )
        )
    plain.write_text("\n".join(lines) + "\n")
    decoyed = bgzip_index(plain)

    first = _relate(family, tmp_path / "a", vcf, "kid.bam")
    second = _relate(family, tmp_path / "b", decoyed, "kid.bam")

    assert _relatedness(second, "dad", "kid") == _relatedness(first, "dad", "kid")
    assert not [w for w in second.warnings if w.code == "vcf_called_sites_only"]


@needs_somalier
def test_a_bam_of_a_sketched_sample_is_compared_with_the_sketch(family, tmp_path):
    """#614 review: a second kit of a person whose sketch is given is
    prefixed rather than refused, and found to be the same person."""
    first = _relate(family, tmp_path / "a", "dad.bam", "kid.bam")
    [dad] = [Path(o.path) for o in first.outputs if Path(o.path).name == "dad.somalier"]

    result = _relate(family, tmp_path / "b", dad, "dad.bam")

    [warning] = [w for w in result.warnings if w.code == "same_person"]
    assert {warning.details["sample_a"], warning.details["sample_b"]} == {
        "dad",
        "dad_dad",
    }


def _reheader(family, tmp_path, name, rg_lines):
    """dad.bam with its @RG lines replaced by ``rg_lines``."""
    samtools = get_tool_path("samtools")
    header = run_command(
        [samtools, "view", "-H", str(family / "dad.bam")], capture_output=True
    ).stdout
    kept = [line for line in header.splitlines() if not line.startswith("@RG")]
    (tmp_path / "h.sam").write_text("\n".join([*kept, *rg_lines]) + "\n")
    bam = tmp_path / name
    with open(bam, "wb") as out:
        run_command(
            [samtools, "reheader", str(tmp_path / "h.sam"), str(family / "dad.bam")],
            stdout=out,
        )
    run_command([samtools, "index", str(bam)])
    return bam


@needs_somalier
def test_a_bam_without_a_sample_name_is_named_after_its_file(family, tmp_path):
    """#614 review: somalier is told the name chosen for a BAM without SM."""
    bam = _reheader(family, tmp_path, "nameless.bam", ["@RG\tID:rg"])

    result = _relate(family, tmp_path / "out", bam, "kid.bam")

    assert (
        _pairs(result)[frozenset(("nameless", "kid"))]["relationship"] == "parent-child"
    )


@needs_somalier
@pytest.mark.parametrize(
    ("rg_lines", "message"),
    [
        (["@RG\tID:rg\tSM:a", "@RG\tID:rg2\tSM:b"], "several samples"),
        (["@RG\tID:rg\tSM:../../escape"], "path separators"),
    ],
)
def test_ambiguous_or_unsafe_sample_names_are_refused(
    family, tmp_path, rg_lines, message
):
    """#614 review: a BAM of several samples, or a sample name that would
    place somalier's sketch file elsewhere, is refused before sketching."""
    bam = _reheader(family, tmp_path, "odd.bam", rg_lines)

    result = api.qc.relate(
        api.RelateParams(
            inputs=(bam,),
            ref=family / "ref.fa",
            outdir=tmp_path / "out",
            sites=family / "sites.vcf.gz",
            ancestry=False,
        ),
        check=False,
    )

    assert result.status == "failed"
    assert message in result.error.message
    assert not list(tmp_path.rglob("escape.somalier"))


@needs_somalier
def test_sketches_swapped_by_name_are_refused_before_any_is_overwritten(
    family, tmp_path
):
    """#614 review: one.somalier holding sample two and two.somalier holding
    one would overwrite each other in somalier/."""
    first = _relate(family, tmp_path / "a", "dad.bam", "kid.bam")
    sketches = {
        Path(o.path).stem: Path(o.path)
        for o in first.outputs
        if o.role == "somalier_sketch"
    }
    folder = tmp_path / "run" / "somalier"
    folder.mkdir(parents=True)
    (folder / "kid.somalier").write_bytes(sketches["dad"].read_bytes())
    (folder / "dad.somalier").write_bytes(sketches["kid"].read_bytes())
    before = {p.name: p.read_bytes() for p in folder.iterdir()}

    result = api.qc.relate(
        api.RelateParams(
            inputs=tuple(sorted(folder.iterdir())),
            sites=family / "sites.vcf.gz",
            ancestry=False,
        ),
        check=False,
    )

    assert result.status == "failed"
    assert "would be saved as" in result.error.message
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_ancestry_with_custom_sites_is_refused(family, tmp_path):
    """#614 review: the 1000 Genomes sketches are of somalier's own sites."""
    result = api.qc.relate(
        api.RelateParams(
            inputs=(family / "dad.bam",),
            outdir=tmp_path,
            sites=family / "sites.vcf.gz",
            ancestry=True,
        ),
        check=False,
    )

    assert result.status == "failed"
    assert "--sites" in result.error.message


def test_a_few_chrx_calls_dont_decide_the_sex():
    """#614 review: one chrX call isn't enough to call the sex."""
    sparse = {
        "X_hom_ref": "0",
        "X_het": "1",
        "X_hom_alt": "0",
        "gt_depth_mean": "30",
        "Y_depth_mean": "0",
    }
    assert infer_sex(sparse) == ("Unknown", "low")


def _with_header(vcf, out, old, new):
    """``vcf`` with one header line changed, bgzipped and indexed."""
    text = run_command(
        [get_tool_path("bcftools"), "view", str(vcf)], capture_output=True
    ).stdout
    assert old in text
    plain = out.with_suffix("")
    plain.write_text(text.replace(old, new, 1))
    return bgzip_index(plain, out)


@needs_somalier
def test_an_exome_like_vcf_is_not_filled(family, tmp_path):
    """#614 final review: an exome VCF reaches somalier's (exome-friendly)
    sites but has few records across the genome; its missing sites aren't
    made hom-ref and its pairs are uncertain."""
    vcf = _variants_vcf(family, tmp_path, "dad")
    # The same calls in a genome whose chr1 is human-sized: ~6 records/Mb.
    exome = _with_header(
        vcf,
        tmp_path / "dad_exome.vcf.gz",
        "##contig=<ID=chr1,length=400000>",
        "##contig=<ID=chr1,length=248956422>",
    )

    result = _relate(family, tmp_path / "out", exome, "kid.bam")

    [warning] = [w for w in result.warnings if w.code == "vcf_called_sites_only"]
    assert warning.details["records_per_mb"] < 100
    assert warning.details["site_ratio"] > 0.8
    pair = _pairs(result)[frozenset(("dad", "kid"))]
    assert pair["relationship"] == UNCERTAIN


@needs_somalier
def test_an_all_sites_vcf_keeps_its_calls_without_a_warning(family, tmp_path):
    """#614 final review: an all-sites VCF (hom-ref calls written out) isn't
    filled, but its hom-ref calls are real: no called-sites-only warning, and
    its pairs aren't labelled uncertain."""
    vcf = tmp_path / "dad_all.vcf.gz"
    bcftools = get_tool_path("bcftools")
    run_command(
        [
            "sh",
            "-c",
            f"{bcftools} mpileup -f {family / 'ref.fa'} -T {family / 'sites.vcf.gz'}"
            f" -a AD,DP -Ou {family / 'dad.bam'} | {bcftools} call -m -Oz -o {vcf}",
        ]
    )
    run_command([get_tool_path("tabix"), "-p", "vcf", str(vcf)])

    result = _relate(family, tmp_path / "out", vcf, "kid.bam", "stranger.bam")

    assert not [w for w in result.warnings if w.code == "vcf_called_sites_only"]
    rows = {r["sample"]: r for r in _table(_output(result, "relate_samples"))}
    assert rows["dad"]["called_sites_only"] == "false"
    assert _pairs(result)[frozenset(("dad", "kid"))]["relationship"] == "parent-child"
    assert _pairs(result)[frozenset(("dad", "stranger"))]["relationship"] == "unrelated"
