#!/usr/bin/env bash
set -euo pipefail
baseline=$1
candidate=$2
out=$3
mkdir -p "$out"
awk 'BEGIN {
    for (c=1; c<=2; c++) {
        print ">chr" c
        for (i=0; i<256; i++) printf "ACGT"
        print ""
    }
}' > "$out/ref.fa"
awk 'BEGIN {
    print "@HD\tVN:1.6\tSO:coordinate"
    print "@SQ\tSN:chr1\tLN:1024"
    print "@SQ\tSN:chr2\tLN:1024"
    print "@RG\tID:s\tSM:sample\tPL:ILLUMINA"
    for (c=1; c<=2; c++)
        for (i=1; i<=80; i++)
            printf "r%d-%d\t0\tchr%d\t%d\t60\t20M\t*\t0\t0\tACGTACGTACGTACGTACGT\tIIIIIIIIIIIIIIIIIIII\tRG:Z:s\tNM:i:0\tMD:Z:20\n", c, i, c, 4*i+1
}' > "$out/input.sam"
"$baseline" view -b -o "$out/input.bam" "$out/input.sam"
for allocator in system mimalloc; do
    exe=$baseline
    if [[ $allocator == mimalloc ]]; then exe=$candidate; fi
    MIMALLOC_SHOW_STATS=1 "$exe" view -C --output-fmt-option version=3.1 \
        -@ 2 -T "$out/ref.fa" -o "$out/$allocator.cram" "$out/input.bam" \
        2> "$out/$allocator.stderr"
    "$baseline" view -T "$out/ref.fa" "$out/$allocator.cram" > "$out/$allocator.sam"
done
test -s "$out/system.sam"
cmp "$out/system.sam" "$out/mimalloc.sam"
grep -q 'subproc 0' "$out/mimalloc.stderr"
grep -q 'peak commit:' "$out/mimalloc.stderr"
if grep -q 'subproc 0' "$out/system.stderr"; then
    echo 'The system control unexpectedly uses mimalloc' >&2
    exit 1
fi
