#!/bin/bash
set -euo pipefail
original=$1
candidate=$2
out=$3
mkdir -p "$out"
perl -e 'print ">chr1\n", "ACGT" x 500, "\n";' > "$out/ref.fa"
for sample in one two; do
    perl -e '
        my $sample = shift;
        my $ref = "ACGT" x 500;
        print "\@HD\tVN:1.6\tSO:coordinate\n\@SQ\tSN:chr1\tLN:2000\n";
        print "\@RG\tID:$sample\tSM:$sample\n";
        for my $i (0..59) {
            my $pos = 1 + int($i / 20) * 25;
            my $seq = substr($ref, $pos - 1, 120);
            substr($seq, 80 - $pos, 1, "A") if $i % 3 != 0;
            my $cigar = "120M";
            if ($i % 11 == 0) { substr($seq, 60, 0, "C"); $cigar = "60M1I60M"; }
            print join("\t", "$sample-$i", 0, "chr1", $pos, 60, $cigar,
                "*", 0, 0, $seq, "I" x length($seq), "RG:Z:$sample"), "\n";
        }
    ' "$sample" > "$out/$sample.sam"
done
head -n 3 "$out/one.sam" > "$out/empty.sam"
for version in original candidate; do
    binary=$original
    if [[ $version == candidate ]]; then binary=$candidate; fi
    for samples in one two empty; do
        inputs=("$out/one.sam")
        if [[ $samples == two ]]; then inputs+=("$out/two.sam"); fi
        if [[ $samples == empty ]]; then inputs=("$out/empty.sam"); fi
        "$binary" mpileup -Ou -f "$out/ref.fa" "${inputs[@]}" |
            "$binary" call -mv -Ov > "$out/$version-$samples.vcf"
        sed '/^#/d' "$out/$version-$samples.vcf" > "$out/$version-$samples.records"
        if [[ $samples == empty ]]; then
            test ! -s "$out/$version-$samples.records"
        else
            test -s "$out/$version-$samples.records"
        fi
    done
done
for samples in one two empty; do
    expected=$(sha256sum "$out/original-$samples.records" | cut -d' ' -f1)
    test "$expected" = "$(sha256sum "$out/candidate-$samples.records" | cut -d' ' -f1)"
done
echo "bcftools allocator: single/multisample and empty-input records match the CRT oracle"
