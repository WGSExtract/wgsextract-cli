#!/usr/bin/env bash
# Usage: haplotypecaller-neon.sh SIDECAR_JAR OUT_DIR
# Run from an installed wgsextract environment with samtools/bcftools/tabix.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SIDECAR=$(realpath "$1")
mkdir -p "$2"
OUT=$(cd "$2" && pwd)
python3 "$HERE/make_fixture.py" "$OUT/input"
mkdir -p "$OUT/tmp"
printf 'temp_directory = "%s"\nstate_directory = "%s"\nmygenomes_library = "%s"\n' \
    "$OUT/tmp" "$OUT/state" "$OUT/mygenomes" \
    > "$OUT/config.toml"
# Keep all resources present but unloadable: preflight succeeds and GATK itself
# must recover from the failed native load, not a mocked runner or helper.
python3 - "$SIDECAR" "$OUT/unloadable.jar" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as source, zipfile.ZipFile(sys.argv[2], "w") as dest:
    for name in source.namelist():
        dest.writestr(name, b"unloadable" if name.endswith(".dylib") else source.read(name))
PY
for mode in java neon fallback; do
    backend=()
    [ "$mode" != neon ] || backend=(--pair-hmm-neon "$SIDECAR")
    [ "$mode" != fallback ] || backend=(--pair-hmm-neon "$OUT/unloadable.jar")
    wgsextract --config "$OUT/config.toml" vcf gatk \
        --input "$OUT/input/reads.bam" --ref "$OUT/input/ref.fa" \
        --threads 4 --reference-evidence gvcf --outdir "$OUT/$mode" \
        ${backend[@]+"${backend[@]}"} > "$OUT/$mode.log" 2>&1
    for kind in vcf g.vcf; do
        bcftools view -H "$OUT/$mode/gatk.$kind.gz" > "$OUT/$mode.$kind.body"
    done
done
for mode in neon fallback; do
    cmp "$OUT/java.g.vcf.body" "$OUT/$mode.g.vcf.body"
    cmp "$OUT/java.vcf.body" "$OUT/$mode.vcf.body"
done
grep -q 'Using Arm NEON PairHMM kernels' "$OUT/neon.log"
grep -q 'Falling back.*LOGLESS_CACHING' "$OUT/fallback.log"
bcftools query -f '%CHROM\t%POS\t%REF\t%ALT[\t%GT]\n' "$OUT/neon/gatk.vcf.gz" \
    > "$OUT/calls.tsv"
printf 'chr1\t1000\tC\tA\t0/1\nchr1\t2000\tG\tGA\t0/1\nchr1\t2999\tAT\tA\t0/1\n' \
    > "$OUT/expected.tsv"
cmp "$OUT/expected.tsv" "$OUT/calls.tsv"
echo "NEON/Java/fallback: exact SNP/insertion/deletion VCF and gVCF parity"
