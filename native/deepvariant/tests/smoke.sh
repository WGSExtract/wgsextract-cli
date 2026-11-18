#!/bin/sh
# Call variants on a deterministic synthetic genome with planted SNPs and
# require each one in the VCF as a PASS call with the planted genotype, and a
# PL value for every genotype of every gVCF record; then
# run the stages `run --dry_run=true` prints, on a copy with only a .csi
# index and a BED selection of every contig, and require the same VCF. With
# the ONT_R104 weights, call long reads too and require the planted SNPs with
# the het ones phased, in phase sets.
# Usage: smoke.sh <wgse-deepvariant> <weights-dir> <work-dir>
# Needs samtools on PATH.
set -eu
# shellcheck source=native/tests/seeded_random.sh
. "$(dirname "$0")/../../tests/seeded_random.sh"
binary=$1
weights=$(cd "$2" && pwd)
work=$3

rm -rf "$work"
mkdir -p "$work"
cd "$work"

# Three 30 kb contigs; two at 30x, one without reads.
awk "$seeded_random"'BEGIN {
    seed(647)
    split("A C G T", base, " ")
    contigs = 3; n = 30000; read = 150; insert = 400; depth = 30
    printf "@HD\tVN:1.6\tSO:unsorted\n" > "reads.sam"
    for (c = 1; c <= contigs; c++) printf "@SQ\tSN:chr%d\tLN:%d\n", c, n > "reads.sam"
    printf "@RG\tID:rg\tSM:smoke\n" > "reads.sam"
    quality = ""
    for (i = 0; i < read; i++) quality = quality "F"
    for (c = 1; c <= contigs; c++) {
        delete ref; delete alt; delete hom
        for (i = 1; i <= n; i++) ref[i] = base[int(rnd() * 4) + 1]
        print ">chr" c > "reference.fa"
        line = ""
        for (i = 1; i <= n; i++) { line = line ref[i]; if (i % 60 == 0) { print line > "reference.fa"; line = "" } }
        if (c == 3) continue
        for (i = 3000; i < n - 1000; i += 3000) {
            alt[i] = ref[i] == "A" ? "G" : "A"
            hom[i] = (i / 3000) % 2 == 0
            printf "chr%d\t%d\t%s\t%s\t%s\n", c, i, ref[i], alt[i], hom[i] ? "1/1" : "0/1" > "truth.tsv"
        }
        pairs = int(n * depth / (2 * read))
        for (p = 0; p < pairs; p++) {
            start = int(rnd() * (n - insert - 2)) + 1
            hap = int(rnd() * 2)
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
    --output_vcf=calls.vcf.gz --output_gvcf=calls.g.vcf.gz \
    --intermediate_results_dir=inter --num_shards=2 > run.log 2>&1 ||
    { tail -20 run.log; exit 1; }

# PL is Number=G: one value per diploid genotype of REF and every ALT, <*>
# included, in reference blocks and in variant records alike (#999).
gzip -dc calls.g.vcf.gz | awk -F '\t' '
    !/^#/ {
        n = split($5, alt, ",") + 1
        split($9, k, ":"); split($10, v, ":")
        for (i in k) if (k[i] == "PL") pl = split(v[i], p, ",")
        kind = $5 == "<*>" ? "block" : "variant"
        seen[kind]++
        if (pl != n * (n + 1) / 2) { print "smoke: " $1 ":" $2 " " $4 " " $5 " has " pl " PL values, not " n * (n + 1) / 2 > "/dev/stderr"; bad++ }
        pl = 0
    }
    END {
        if (!seen["block"] || !seen["variant"]) { print "smoke: the gVCF lacks reference blocks or variant records" > "/dev/stderr"; exit 1 }
        exit (bad > 0)
    }'

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

# #1103: staged execution must carry PAR/haploid policy to both reference
# confidence generation and final calling, without changing diploid chr2.
printf 'chr1\t0\t9000\n' > par.bed
"$binary" run --model_type=WGS --ref=reference.fa --reads=reads.bam \
    --checkpoint="$weights/wgs.dvw" --small_model_path="$weights/wgs_small_weights" \
    --output_vcf=haploid.vcf.gz --output_gvcf=haploid.g.vcf.gz \
    --haploid_contigs=chr1 --par_regions_bed=par.bed \
    --intermediate_results_dir=haploid --num_shards=2 \
    --dry_run=true > haploid-plan.txt
awk '/^\*\*\*\*\* Running the command:\*\*\*\*\*$/ { getline; print }' haploid-plan.txt > haploid-stages.sh
test "$(wc -l < haploid-stages.sh)" -eq 3
mkdir -p haploid
sh -e haploid-stages.sh > haploid.log 2>&1 || { tail -20 haploid.log; exit 1; }
gzip -dc haploid.vcf.gz | awk -F '\t' '
    !/^#/ {
        split($10, f, ":"); gt = f[1]; gsub(/\|/, "/", gt)
        if ($1 == "chr1" && $2 > 9000 && gt ~ /^[0-9]+\/[0-9]+$/) {
            split(gt, a, "/")
            if (a[1] != a[2]) bad++
        }
        if ($1 == "chr1" && $2 == 3000 && gt == "0/1" && $7 == "PASS") par++
        if ($1 == "chr1" && $2 == 12000 && gt == "1/1" && $7 == "PASS") haploid++
        if ($1 == "chr2" && $2 == 3000 && gt == "0/1" && $7 == "PASS") diploid++
    }
    END { exit (bad || haploid != 1 || par != 1 || diploid != 1) }'
gzip -dc haploid.g.vcf.gz | awk -F '\t' '
    !/^#/ {
        end = $2 + length($4) - 1
        n = split($8, info, ";")
        for (i = 1; i <= n; i++) if (info[i] ~ /^END=/) end = substr(info[i], 5) + 0
        if ($2 != last[$1] + 1) bad++
        last[$1] = end
        seen[$1]++
    }
    END { exit (bad || last["chr1"] != 30000 || last["chr2"] != 30000 ||
                last["chr3"] != 30000 || !seen["chr1"] || !seen["chr2"] ||
                !seen["chr3"]) }'
echo "smoke: haploid non-PAR calls, retained PAR/diploid hets, gap-free gVCF"

[ -f "$weights/ont_r104.dvw" ] || exit 0
# 60x of 8 kb reads (30x at the outer SNPs), long enough to join the het SNPs
# 6 kb apart, from the same genome's two haplotypes; the het SNPs are
# all on haplotype 1.
awk -F '\t' "$seeded_random"'BEGIN {
    seed(739)
    read = 8000; depth = 60
    while ((getline line < "reference.fa.fai") > 0) { split(line, f, "\t"); len[f[1]] = f[2]; order[++contigs] = f[1] }
    while ((getline line < "truth.tsv") > 0) { split(line, f, "\t"); alt[f[1], f[2]] = f[4]; hom[f[1], f[2]] = f[5] == "1/1" }
    printf "@HD\tVN:1.6\tSO:unsorted\n" > "ont.sam"
    for (c = 1; c <= contigs; c++) printf "@SQ\tSN:%s\tLN:%d\n", order[c], len[order[c]] > "ont.sam"
    printf "@RG\tID:rg\tSM:smoke\n" > "ont.sam"
    quality = ""
    for (i = 0; i < read; i++) quality = quality "5"
    name = ""
    while ((getline line < "reference.fa") > 0) {
        if (line ~ /^>/) { name = substr(line, 2); seq[name] = ""; continue }
        seq[name] = seq[name] line
    }
    for (c = 1; c <= contigs; c++) {
        contig = order[c]; n = len[contig]
        for (r = 0; r < int(n * depth / read); r++) {
            pos = int(rnd() * (n - read)) + 1
            hap = int(rnd() * 2)
            s = ""
            for (i = pos; i < pos + read; i++) {
                b = substr(seq[contig], i, 1)
                s = s (((contig, i) in alt) && (hom[contig, i] || hap) ? alt[contig, i] : b)
            }
            printf "%sr%d\t0\t%s\t%d\t60\t%dM\t*\t0\t0\t%s\t%s\tRG:Z:rg\n", contig, r, contig, pos, read, s, quality > "ont.sam"
        }
    }
}'
samtools sort -o ont.bam ont.sam
samtools index ont.bam
"$binary" run --model_type=ONT_R104 --ref=reference.fa --reads=ont.bam \
    --checkpoint="$weights/ont_r104.dvw" --small_model_path="$weights/ont_r104_small_weights" \
    --output_vcf=ont.vcf.gz --intermediate_results_dir=ont --num_shards=2 > ont.log 2>&1 ||
    { tail -20 ont.log; exit 1; }
gzip -dc ont.vcf.gz | awk -F '\t' '!/^#/ && $7 == "PASS" {
    split($9, k, ":"); split($10, v, ":")
    delete f
    for (i in k) f[k[i]] = v[i]
    gt = f["GT"]; phased = gt ~ /\|/ && f["PS"] ~ /^[1-9][0-9]*$/; gsub(/\|/, "/", gt)
    if (gt == "1/0") gt = "0/1"
    print $1 "\t" $2 "\t" $4 "\t" $5 "\t" gt "\t" (gt == "0/1" && phased)
}' > ont-called.tsv
# Within a read length of the contigs' ends, too few reads join a het SNP to
# its neighbour for the model to call it with confidence: require the SNPs
# between them, with every het one phased, and no PASS call that wasn't planted.
cut -f1-5 ont-called.tsv | sort > ont-pass.tsv
sort truth.tsv | comm -13 - ont-pass.tsv > ont-wrong.tsv
awk -F '\t' '$2 > 8000 && $2 < 22000' truth.tsv | sort | comm -23 - ont-pass.tsv > ont-missed.tsv
inner_hets=$(awk -F '\t' '$2 > 8000 && $2 < 22000 && $5 == "0/1"' truth.tsv | wc -l | tr -d ' ')
phased=$(awk -F '\t' '$2 > 8000 && $2 < 22000 && $6' ont-called.tsv | wc -l | tr -d ' ')
if [ -s ont-wrong.tsv ] || [ -s ont-missed.tsv ] || [ "$phased" -ne "$inner_hets" ]; then
    cat ont-wrong.tsv ont-missed.tsv >&2
    echo "smoke: ONT_R104 made $(wc -l < ont-wrong.tsv | tr -d ' ') wrong calls, missed $(wc -l < ont-missed.tsv | tr -d ' ') planted SNPs and phased $phased of $inner_hets het ones" >&2
    exit 1
fi
echo "smoke: ONT_R104 called $(wc -l < ont-pass.tsv | tr -d ' ') of $(wc -l < truth.tsv | tr -d ' ') planted SNPs, the $inner_hets het ones away from the contigs' ends phased"
