"""Classify mtDNA called on CHM13v2.0's chrM by mapping it onto the rCRS (#832).

CHM13's chrM is the rCRS rotated 576 bp, with three more differences (an
extra C after rCRS 310, no 3107N, 10208 C for T), so positions can't simply
be shifted. A sample matching CHM13 at 10208 has no call there yet carries
10208C against the rCRS, so mapping needs a call at every position, not
variants alone: calls at all sites go through the pinned alignment
(``mito_reference``) and come out as rCRS variants, indels left-aligned
against the rCRS.
"""

from __future__ import annotations

from pathlib import Path

from wgsextract_cli.core.mito_reference import (
    chm13_to_rcrs,
    pinned_sequences,
    rcrs_variants,
    sequence_md5,
)
from wgsextract_cli.core.utils import WGSExtractError, run_command

# ALT alleles that stand for "no other allele" in all-sites VCFs and gVCFs.
_NO_ALT = {".", "<*>", "<NON_REF>", "<X>"}


def _rows(vcf: str, fields: str) -> list[list[str]]:
    query = ["bcftools", "query", "-f", fields, vcf]
    out = run_command(query, capture_output=True).stdout
    return [line.split("\t") for line in out.splitlines() if line]


def _allele(gt: str) -> int | None:
    """The allele a genotype gives the sample: its only one, or for a mixed
    (heteroplasmic) call its ALT. None when it is a no-call."""
    called = [int(a) for a in gt.replace("|", "/").split("/") if a.isdigit()]
    if not called:
        return None
    return max(called, key=lambda a: (called.count(a), a))


def _sample_bases(vcf: str) -> tuple[dict[int, str], str]:
    """The bases the one sample of ``vcf`` has at each called CHM13 position,
    and its name.

    Reference calls (``bcftools call`` without ``-v``, or gVCF blocks) cover
    their span; a SNP or indel call replaces the bases it spans.
    """
    samples = run_command(["bcftools", "query", "-l", vcf], capture_output=True)
    names = samples.stdout.split()
    if len(names) != 1:
        raise WGSExtractError(
            "Mapping CHM13 chrM calls onto the rCRS needs a single-sample VCF; "
            f"{vcf} has {len(names)} samples."
        )
    chm13 = pinned_sequences()[1]
    reference: dict[int, str] = {}
    changes: list[tuple[int, str, str]] = []
    for pos, end, ref, alts, gt in _rows(vcf, "%POS\t%END\t%REF\t%ALT[\t%GT]\n"):
        allele = _allele(gt)
        alleles = [ref, *alts.split(",")]
        if allele is None or allele >= len(alleles) or alleles[allele] in _NO_ALT:
            continue
        if alleles[allele].startswith("<"):  # a symbolic allele, such as <DEL>
            continue
        start = int(pos)
        if allele == 0:
            for q in range(start, max(int(end), start + len(ref) - 1) + 1):
                if 1 <= q <= len(chm13):
                    reference[q] = chm13[q - 1]
        else:
            changes.append((start, ref.upper(), alleles[allele].upper()))
    bases = dict(reference)
    # SNPs first, so an indel that overlaps one decides the bases it spans.
    for start, ref, alt in sorted(changes, key=lambda c: len(c[1]) != len(c[2])):
        # Multi-allelic records pad shorter alleles with REF's tail.
        while len(ref) > 1 and len(alt) > 1 and ref[-1] == alt[-1]:
            ref, alt = ref[:-1], alt[:-1]
        while len(ref) > 1 and len(alt) > 1 and ref[0] == alt[0]:
            start, ref, alt = start + 1, ref[1:], alt[1:]
        if len(ref) == len(alt):
            for i, base in enumerate(alt):
                bases[start + i] = base
            continue
        span = range(start + 1, start + len(ref))
        if ref[0] == alt[0] and len(ref) == 1:  # an insertion after start
            bases[start] = bases.get(start, ref) + alt[1:]
        elif ref[0] != alt[0]:
            bases[start] = alt
        for q in span:
            bases[q] = ""
    return bases, names[0]


def has_reference_calls(vcf: str) -> bool:
    """Whether ``vcf`` calls sites as the reference (all sites or a gVCF), so
    a position without a variant is known to match it."""
    for alts, gt in _rows(vcf, "%ALT[\t%GT]\n"):
        if alts in _NO_ALT or _allele(gt) == 0:
            return True
    return False


def map_to_rcrs(vcf: str, chr_m: str, out: str) -> dict[int, str]:
    """Write the rCRS variants of the sample ``vcf`` calls at all sites on
    CHM13's chrM to ``out`` (bgzipped, indels left-aligned against the rCRS);
    returns the sample's bases at each called CHM13 position."""
    bases, sample = _sample_bases(vcf)
    tmp = Path(out).parent
    rcrs_fa = _rcrs_fasta(tmp, chr_m)
    lines = [
        "##fileformat=VCFv4.2",
        _contig(chr_m),
        "##source=wgsextract: CHM13v2.0 chrM calls mapped onto the rCRS",
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}",
    ]
    lines += [
        f"{chr_m}\t{pos}\t.\t{ref}\t{alt}\t.\tPASS\t.\tGT\t1"
        for pos, ref, alt in rcrs_variants(bases)
    ]
    plain = tmp / "rcrs_unnormalised.vcf"
    plain.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    run_command(["bcftools", "norm", "-f", rcrs_fa, "-Oz", "-o", out, str(plain)])
    return bases


def site_depths(vcf: str) -> dict[int, str]:
    """INFO/DP at each position of ``vcf`` (bcftools call output)."""
    return {int(pos): dp for pos, dp in _rows(vcf, "%POS\t%INFO/DP\n") if dp != "."}


def lift_heteroplasmies(
    calls: str, bases: dict[int, str], depths: dict[int, str], chr_m: str, out: str
) -> str:
    """Map mutserve's CHM13 heteroplasmy calls (``calls``) onto the rCRS for
    haplocheck, into ``out``. Where CHM13 differs from the rCRS, ``bases``
    (all-sites calls) give the homoplasmies mutserve didn't report because the
    sample matches CHM13 there; ``depths`` gives their depth."""
    lift = chm13_to_rcrs()
    meta: list[str] = []
    columns = ""
    records: list[tuple[int, list[str]]] = []
    seen: set[int] = set()
    text = run_command(["bcftools", "view", calls], capture_output=True).stdout
    for line in text.splitlines():
        if line.startswith("#"):
            if line.startswith("#CHROM"):
                columns = line
            elif not line.startswith("##contig"):
                meta.append(line)
            continue
        fields = line.split("\t")
        start, ref = int(fields[1]), fields[3]
        span = [lift.get(q) for q in range(start, start + len(ref))]
        mapped = [s for s in span if s]
        # Skip records over CHM13's extra C, or indels over a site where it
        # differs from the rCRS: haplocheck weighs SNP heteroplasmies.
        if len(mapped) < len(span) or (
            len(ref) > 1 and any(r != c for _p, r, c in mapped)
        ):
            continue
        p, r, c = mapped[0]
        seen.add(start)
        if r != c:
            flipped = _flip(fields, r, c)
            if flipped is None:
                continue
            fields = flipped
        fields[0], fields[1], fields[3] = chr_m, str(p), r + ref[1:]
        records.append((p, fields))
    for q, (p, r, c) in lift.items():
        if r != c and q not in seen and bases.get(q) == c:
            row = [chr_m, str(p), ".", r, c, ".", "PASS", ".", "GT:AF:DP"]
            records.append((p, [*row, f"1:1.0:{depths.get(q, 0)}"]))
    lines = [*meta, _contig(chr_m), columns]
    lines += ["\t".join(f) for _p, f in sorted(records, key=lambda r: r[0])]
    plain = Path(out).with_suffix("")
    plain.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    run_command(["bcftools", "view", "-Oz", "-o", out, str(plain)])
    return out


def _contig(chr_m: str) -> str:
    rcrs = pinned_sequences()[0]
    return f"##contig=<ID={chr_m},length={len(rcrs)},md5={sequence_md5(rcrs)}>"


def _flip(fields: list[str], rcrs_base: str, chm13_base: str) -> list[str] | None:
    """A mutserve call at a site where CHM13's base isn't the rCRS's, restated
    against the rCRS base; None when the sample has only the rCRS base."""
    if fields[4] != rcrs_base:
        return fields  # a third allele: an rCRS variant all the same
    keys = fields[8].split(":")
    values = dict(zip(keys, fields[9].split(":"), strict=False))
    if values.get("GT") == "1":
        return None
    values["GT"] = {"0/1": "1/0", "1/0": "0/1"}.get(values.get("GT", ""), "0/1")
    if "AF" in values:
        values["AF"] = f"{1 - float(values['AF']):.3f}"
    return [*fields[:4], chm13_base, *fields[5:9], ":".join(values[k] for k in keys)]


def _rcrs_fasta(tmp: Path, chr_m: str) -> str:
    rcrs = pinned_sequences()[0]
    path = tmp / "rcrs.fa"
    lines = [rcrs[i : i + 70] for i in range(0, len(rcrs), 70)]
    path.write_text(
        f">{chr_m}\n" + "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )
    return str(path)
