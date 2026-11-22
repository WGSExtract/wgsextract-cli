#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Usage: haplotypecaller.sh GATK_JAR SIDECAR_JAR OUT_DIR [THREADS]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
GATK=$(realpath "$1")
SIDECAR=$(realpath "$2")
OUT=$(realpath -m "$3")
THREADS=${4:-1}
mkdir -p "$OUT/tmp"
python3 "$HERE/make_fixture.py" "$OUT/input"
COMMON=(-Xmx1g "-XX:ActiveProcessorCount=$THREADS" "-Djava.io.tmpdir=$OUT/tmp")
for mode in stock cuda; do
    backend=()
    [ "$mode" != cuda ] || backend=("-Xbootclasspath/a:$SIDECAR")
    java "${backend[@]}" "${COMMON[@]}" -jar "$GATK" HaplotypeCaller \
        -R "$OUT/input/ref.fa" -I "$OUT/input/reads.bam" -O "$OUT/$mode.g.vcf.gz" \
        -ERC GVCF --native-pair-hmm-threads "$THREADS" \
        --pair-hmm-implementation AVX_LOGLESS_CACHING_OMP \
        > "$OUT/$mode.hc.log" 2>&1
    java "${COMMON[@]}" -jar "$GATK" GenotypeGVCFs \
        -R "$OUT/input/ref.fa" -V "$OUT/$mode.g.vcf.gz" -O "$OUT/$mode.vcf.gz" \
        > "$OUT/$mode.genotype.log" 2>&1
    bcftools view -H "$OUT/$mode.g.vcf.gz" > "$OUT/$mode.gvcf.body"
    bcftools view -H "$OUT/$mode.vcf.gz" > "$OUT/$mode.vcf.body"
done
cmp "$OUT/stock.gvcf.body" "$OUT/cuda.gvcf.body"
cmp "$OUT/stock.vcf.body" "$OUT/cuda.vcf.body"
grep -Eq 'completed [1-9][0-9]* GPU read/haplotype pairs' "$OUT/cuda.hc.log"
bcftools query -f '%CHROM\t%POS\t%REF\t%ALT[\t%GT]\n' "$OUT/cuda.vcf.gz" > "$OUT/calls.tsv"
printf 'chr1\t1000\tC\tA\t0/1\nchr1\t2000\tG\tGA\t0/1\nchr1\t2999\tAT\tA\t0/1\n' \
    > "$OUT/expected.tsv"
cmp "$OUT/expected.tsv" "$OUT/calls.tsv"
echo "Exact HaplotypeCaller VCF/gVCF parity: SNP, insertion and deletion"
