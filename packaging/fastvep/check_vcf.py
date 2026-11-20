"""Exercise the installed package's database builder and ACMG annotator offline."""

import shutil
import subprocess
import sys
from pathlib import Path


def check_annotation_sources(stderr: str) -> None:
    """Allow only the smoke fixture's deliberately absent RepeatMasker track."""
    for line in stderr.splitlines():
        if "sources the classifier reads are not loaded:" not in line:
            continue
        missing = line.partition("not loaded:")[2].strip()
        if missing != "RepeatMasker repeat intervals (BP3).":
            raise SystemExit(
                f"Required fastVEP annotation source not loaded: {missing}"
            )


def main() -> None:
    fixtures = Path("out/fixtures")
    shutil.copytree(
        Path(__file__).resolve().parent / "fixtures", fixtures, dirs_exist_ok=True
    )
    sa = Path("out/sa")
    sa.mkdir(parents=True, exist_ok=True)
    sources = {
        "clinvar": "clinvar.vcf",
        "clinvar_protein": "variant_summary.txt",
        "gnomad": "gnomad.vcf",
        "gnomad_genes": "constraint.tsv",
        "omim": "clingen.tsv",
        "revel": "revel.csv",
        "spliceai": "spliceai.vcf",
        "phylop": "phylop.wigFix",
    }
    for source, name in sources.items():
        subprocess.run(
            ["fastvep", "sa-build", "--source", source, "-i", str(fixtures / name),
             "-o", str(sa / source), "--assembly", "GRCh38", "--no-progress"],
            check=True,
        )  # fmt: skip
    output = Path("out/annotated.vcf")
    annotated = subprocess.run(
        ["fastvep", "annotate", "-i", str(fixtures / "in_vep.vcf"),
         "-o", str(output), "--gff3", str(fixtures / "genes.gff3"),
         "--fasta", str(fixtures / "genome.fa"), "--sa-dir", str(sa),
         "--acmg", "--pick-allele", "--no-progress"],
        check=True,
        capture_output=True,
        text=True,
    )  # fmt: skip
    print(annotated.stdout, end="")
    print(annotated.stderr, end="", file=sys.stderr)
    check_annotation_sources(annotated.stderr)
    text = output.read_text()
    header = next(
        line for line in text.splitlines() if line.startswith("##INFO=<ID=CSQ")
    )
    fields = header.split("Format: ", 1)[1].split('"', 1)[0].split("|")
    classes = {}
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        columns = line.split("\t")
        info = dict(item.partition("=")[::2] for item in columns[7].split(";"))
        entries = [
            dict(zip(fields, row.split("|"), strict=True))
            for row in info["CSQ"].split(",")
        ]
        classes[int(columns[1])] = entries
    stop = classes[1017][0]
    if stop["ACMG"] != "LP" or "PVS1" not in stop["ACMG_CRITERIA"]:
        raise SystemExit(f"Stop-gain classification missing: {stop}")
    if classes[1150][0]["ACMG"] != "B":
        raise SystemExit("Common allele must be benign")
    if len(classes[2500]) != 2:
        raise SystemExit("Multi-allelic record lost an allele")
    print("fastVEP package: database build and ACMG VCF smoke passed")


if __name__ == "__main__":
    main()
