#!/bin/sh
# Call variants on a deterministic synthetic genome with planted SNPs and
# require each one in the VCF as a PASS call with the planted genotype; then
# run the stages `run --dry_run=true` prints, on a copy with only a .csi
# index and a BED selection of every contig, and require the same VCF.
# Usage: smoke.sh <wgse-deepvariant> <weights-dir> <work-dir>
# Needs samtools on PATH.
set -eu
binary=$1
weights=$(cd "$2" && pwd)
work=$3

rm -rf "$work"
mkdir -p "$work"
cd "$work"

# Two 30 kb contigs; 30x of 2x150 bp pairs; a het and a hom-alt SNP every 3 kb.
awk 'BEGIN {
    srand(647)
    split("A C G T", base, " ")
    contigs = 2; n = 30000; read = 150; insert = 400; depth = 30
    printf "@HD\tVN:1.6\tSO:unsorted\n" > "reads.sam"
    for (c = 1; c <= contigs; c++) printf "@SQ\tSN:chr%d\tLN:%d\n", c, n > "reads.sam"
    printf "@RG\tID:rg\tSM:smoke\n" > "reads.sam"
    quality = ""
    for (i = 0; i < read; i++) quality = quality "F"
    for (c = 1; c <= contigs; c++) {
        delete ref; delete alt; delete hom
        for (i = 1; i <= n; i++) ref[i] = base[int(rand() * 4) + 1]
        print ">chr" c > "reference.fa"
        line = ""
        for (i = 1; i <= n; i++) { line = line ref[i]; if (i % 60 == 0) { print line > "reference.fa"; line = "" } }
        for (i = 3000; i < n - 1000; i += 3000) {
            alt[i] = ref[i] == "A" ? "G" : "A"
            hom[i] = (i / 3000) % 2 == 0
            printf "chr%d\t%d\t%s\t%s\t%s\n", c, i, ref[i], alt[i], hom[i] ? "1/1" : "0/1" > "truth.tsv"
        }
        pairs = int(n * depth / (2 * read))
        for (p = 0; p < pairs; p++) {
            start = int(rand() * (n - insert - 2)) + 1
            hap = int(rand() * 2)
            for (m = 0; m < 2; m++) {
                pos = m == 0 ? start : start + insert - read
                s = ""
                for (i = pos; i < pos + read; i++) s = s ((i in alt) && (hom[i] || hap) ? alt[i] : ref[i])
                flag = m == 0 ? 99 : 147
                mpos = m == 0 ? start + insert - read : start
                tlen = m == 0 ? insert : -insert
                printf "c%dp%d\t%d\tchr%d\t%d\t60\t%dM\t=\t%d\t%d\t%s\t%s\tRG:Z:rg\n", c, p, flag, c, pos, read, mpos, tlen, s, quality > "reads.sam"
            }
        }
    }
}'
samtools faidx reference.fa
samtools sort -o reads.bam reads.sam
samtools index reads.bam

"$binary" run --model_type=WGS --ref=reference.fa --reads=reads.bam \
    --checkpoint="$weights/wgs.dvw" --small_model_path="$weights/wgs_small_weights" \
    --output_vcf=calls.vcf.gz --intermediate_results_dir=inter --num_shards=2 > run.log 2>&1 ||
    { tail -20 run.log; exit 1; }

gzip -dc calls.vcf.gz | awk -F '\t' '!/^#/ && $7 == "PASS" { split($10, f, ":"); print $1 "\t" $2 "\t" $4 "\t" $5 "\t" f[1] }' > called.tsv
if ! diff truth.tsv called.tsv; then
    echo "smoke: PASS calls differ from the planted SNPs" >&2
    exit 1
fi
cp reads.bam staged.bam
samtools index -c staged.bam
awk -F '\t' '{ print $1 "\t0\t" $2 }' reference.fa.fai > all.bed
"$binary" run --model_type=WGS --ref=reference.fa --reads=staged.bam --regions=all.bed \
    --checkpoint="$weights/wgs.dvw" --small_model_path="$weights/wgs_small_weights" \
    --output_vcf=staged.vcf.gz --intermediate_results_dir=staged --num_shards=2 \
    --dry_run=true > plan.txt
awk '/^\*\*\*\*\* Running the command:\*\*\*\*\*$/ { getline; print }' plan.txt > stages.sh
test "$(wc -l < stages.sh)" -eq 3
mkdir -p staged
sh -e stages.sh > staged.log 2>&1 || { tail -20 staged.log; exit 1; }
gzip -dc calls.vcf.gz | grep -v '^##' > calls.body
gzip -dc staged.vcf.gz | grep -v '^##' > staged.body
if ! cmp -s calls.body staged.body; then
    echo "smoke: the staged commands called differently from run" >&2
    exit 1
fi
echo "smoke: $(wc -l < truth.tsv | tr -d ' ') planted SNPs called; the staged commands agree"
