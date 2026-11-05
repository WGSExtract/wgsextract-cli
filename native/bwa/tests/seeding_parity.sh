#!/bin/sh
# Exercise all three seeding rounds, divergent reads and each fallback path.
set -eu
aligner=$1
benchmark=$2
work=$3
mkdir -p "$work"
cd "$work"

awk 'BEGIN {
    srand(364)
    split("A C G T", base, " ")
    for (i = 1; i <= 120000; ++i) random = random base[int(rand() * 4) + 1]
    for (i = 1; i <= 24000; ++i) tandem = tandem "ACGT"
    for (i = 1; i <= 96000; ++i) homo = homo "A"
    print ">random\n" random "\n>tandem\n" tandem "\n>homopolymer\n" homo > "reference.fa"
    split("1 18 19 20 28 29 30 100 150 250 511 512 513 900", lengths, " ")
    for (r = 1; r <= 2048; ++r) {
        n = lengths[1 + (r % 14)]
        start = 1 + int(rand() * 110000)
        seq = substr(random, start, n)
        if (r % 7 == 0) seq = substr(tandem, 1 + r % 4, n)
        if (r % 11 == 0) seq = substr(homo, 1, n)
        if (r % 3 == 0) {
            for (i = 15; i < n; i += 23)
                seq = substr(seq, 1, i - 1) base[int(rand() * 4) + 1] substr(seq, i + 1)
        }
        if (r % 5 == 0) {
            for (i = 1; i <= n; i += 19) seq = substr(seq, 1, i - 1) "N" substr(seq, i + 1)
        }
        qual = ""
        for (i = 1; i <= n; ++i) qual = qual "I"
        printf "@read%d\n%s\n+\n%s\n", r, seq, qual > "reads.fq"
    }
}'
"$aligner" index reference.fa > index.log 2>&1
# Preserve exit 77 when the test runner has no Metal device.
"$benchmark" reference.fa reads.fq --reads 2048 --batch 257 --verify-only > parity.log 2>&1
cat parity.log
grep -Eq 'length=[1-9][0-9]*' parity.log
"$benchmark" reference.fa reads.fq --reads 2048 --batch 257 --verify-only \
    --scratch-limit 1 > scratch-fallback.log 2>&1
cat scratch-fallback.log
grep -Eq 'scratch=[1-9][0-9]*' scratch-fallback.log
"$benchmark" reference.fa reads.fq --reads 2048 --batch 257 --verify-only \
    --output-limit 1 > output-fallback.log 2>&1
cat output-fallback.log
grep -Eq 'output=[1-9][0-9]*' output-fallback.log

# Tiny indexes exercise sentinel/block boundaries and absent DNA symbols.
awk 'BEGIN {
    for (i = 1; i <= 127; ++i) seq = seq "A"
    print ">tiny\n" seq > "tiny.fa"
    split("A C G T N", base, " ")
    for (b = 1; b <= 5; ++b) {
        for (n = 18; n <= 151; ++n) {
            read = ""; quality = ""
            for (i = 1; i <= n; ++i) { read = read base[b]; quality = quality "I" }
            printf "@tiny%d_%d\n%s\n+\n%s\n", b, n, read, quality > "tiny.fq"
        }
    }
}'
"$aligner" index tiny.fa > tiny-index.log 2>&1
"$benchmark" tiny.fa tiny.fq --reads 1000 --batch 257 --verify-only > tiny-parity.log 2>&1
cat tiny-parity.log

"$benchmark" reference.fa reads.fq --reads 2048 --batch 257 --verify-only \
    --concurrent --scratch-limit 1 --output-limit 1 > concurrent-parity.log 2>&1
cat concurrent-parity.log
grep -q 'concurrent_parity=exact' concurrent-parity.log
