#!/bin/sh
# Index a deterministic synthetic genome (with N runs, ambiguous bases and a repeat)
# and map paired short reads and accurate long reads to it with wgse-minibwa and with
# minibwa built from unmodified upstream sources. Require identical indexes (with
# libsais and with the low-memory -l algorithm), identical alignments apart from the
# @PG version, accurate placements and identical output across thread counts.
# Usage: smoke.sh <wgse-minibwa> <minibwa-upstream> <work-dir>
set -eu
wgse=$1
upstream=$2
work=$3

rm -rf "$work"
mkdir -p "$work"
cd "$work"

awk 'BEGIN {
    srand(546)
    split("A C G T", base, " ")
    complement["A"] = "T"; complement["C"] = "G"
    complement["G"] = "C"; complement["T"] = "A"
    contigs = 2; length_ = 300000
    repeat_ = ""
    for (i = 0; i < 2000; i++) repeat_ = repeat_ base[int(rand() * 4) + 1]
    for (c = 1; c <= contigs; c++) {
        sequence[c] = ""
        for (i = 0; i < length_; i++) sequence[c] = sequence[c] base[int(rand() * 4) + 1]
        # One copy of the repeat on each contig, and an N run and an IUPAC base on chr2.
        sequence[c] = substr(sequence[c], 1, 100000 * c) repeat_ substr(sequence[c], 100000 * c + 2001)
        if (c == 2) sequence[c] = substr(sequence[c], 1, 250000) "NNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNR" substr(sequence[c], 250053)
        print ">chr" c > "reference.fa"
        for (i = 1; i <= length_; i += 60) print substr(sequence[c], i, 60) > "reference.fa"
    }
    for (p = 1; p <= 2000; p++) {
        c = int(rand() * contigs) + 1
        insert = 300 + int(rand() * 200)
        start = int(rand() * (length_ - insert - 60000)) + 1
        if (c == 2 && start > 240000) start -= 60000
        left = substitute(substr(sequence[c], start, 150), 3)
        right = reverse_complement(substitute(substr(sequence[c], start + insert - 150, 150), 3))
        name = "p" p "_chr" c "_" start
        printf "@%s/1\n%s\n+\n%s\n", name, left, quality(150) > "reads_1.fq"
        printf "@%s/2\n%s\n+\n%s\n", name, right, quality(150) > "reads_2.fq"
    }
    for (r = 1; r <= 100; r++) {
        c = int(rand() * contigs) + 1
        size = 2000 + int(rand() * 8000)
        start = int(rand() * (length_ - size - 60000)) + 1
        if (c == 2 && start > 240000 - size) start -= 60000
        read_ = substitute(substr(sequence[c], start, size), int(size / 500))
        if (rand() < 0.5) read_ = reverse_complement(read_)
        printf "@r%d_chr%d_%d\n%s\n+\n%s\n", r, c, start, read_, quality(size) > "long.fq"
    }
}
function substitute(read_sequence, n,    i, position) {
    for (i = 0; i < n; i++) {
        position = int(rand() * length(read_sequence)) + 1
        read_sequence = substr(read_sequence, 1, position - 1) base[int(rand() * 4) + 1] \
            substr(read_sequence, position + 1)
    }
    return read_sequence
}
function reverse_complement(s,    i, out) {
    out = ""
    for (i = length(s); i >= 1; i--) out = out complement[substr(s, i, 1)]
    return out
}
function quality(n,    q) {
    q = ""
    while (length(q) < n) q = q "IIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII"
    return substr(q, 1, n)
}'

# Index with both builds and both algorithms; every index must be the same.
"$upstream" index -t 4 reference.fa upstream 2> upstream.index.log
"$wgse" index -t 4 reference.fa wgse 2> wgse.index.log
"$wgse" index -l -b 1m reference.fa wgse-l 2> wgse-l.index.log
for index in wgse wgse-l; do
    cmp upstream.l2b "$index.l2b"
    cmp upstream.mbw "$index.mbw"
done

records() { grep -v '^@PG' "$1" > "$1.records"; }

"$upstream" map -t 4 upstream reads_1.fq reads_2.fq > upstream.pe.sam 2> upstream.pe.log
"$upstream" map -t 4 upstream long.fq > upstream.long.sam 2> upstream.long.log
for threads in 1 4; do
    "$wgse" map -t "$threads" wgse reads_1.fq reads_2.fq > "wgse.pe.t$threads.sam" 2> "wgse.pe.t$threads.log"
    "$wgse" map -t "$threads" wgse long.fq > "wgse.long.t$threads.sam" 2> "wgse.long.t$threads.log"
done
grep -q '^@PG	ID:minibwa	PN:minibwa	VN:[^	]*-wgse-' wgse.pe.t4.sam
for kind in pe long; do
    records "upstream.$kind.sam"
    for threads in 1 4; do
        records "wgse.$kind.t$threads.sam"
        cmp "upstream.$kind.sam.records" "wgse.$kind.t$threads.sam.records"
    done
done

# Primary alignments of every read must start within 50 bp of its origin, and the
# repeat must give ambiguous alignments (secondary, or primary with MAPQ 0).
check() {
    awk -F '\t' -v expected="$2" '
        /^@/ { next }
        !bit($2, 256) && !bit($2, 2048) && !bit($2, 128) {
            total++
            split($1, truth, "_")
            if ($3 == truth[2] && $4 >= truth[3] - 50 && $4 <= truth[3] + 50) correct++
        }
        function bit(flag, value) { return int(flag / value) % 2 }
        END {
            if (total != expected || correct < 0.97 * expected) {
                printf "%s: %d of %d expected reads placed correctly\n", FILENAME, correct, total > "/dev/stderr"
                exit 1
            }
        }' "$1"
}
check upstream.pe.sam 2000
check upstream.long.sam 100
awk -F '\t' '!/^@/ && int($2 / 4) % 2 == 0 && $5 == 0 { n++ } END { exit !(n > 0) }' upstream.pe.sam
echo "wgse-minibwa matches upstream minibwa 0.7 for indexing and paired and long reads"
