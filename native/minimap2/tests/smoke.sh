#!/bin/sh
# Map deterministic synthetic HiFi-like, ONT-like and paired short reads to a
# synthetic genome; require accurate placements, identical output from a FASTA
# reference and a saved index, identical output across repeated runs, and with the
# build's GPU (--metal or --cuda), where it is usable.
# Usage: smoke.sh <wgse-minimap2> <work-dir>
set -eu
# shellcheck source=native/tests/seeded_random.sh
. "$(dirname "$0")/../../tests/seeded_random.sh"
binary=$1
work=$2

rm -rf "$work"
mkdir -p "$work"
cd "$work"

awk "$seeded_random"'BEGIN {
    seed(364)
    split("A C G T", base, " ")
    complement["A"] = "T"; complement["C"] = "G"
    complement["G"] = "C"; complement["T"] = "A"
    contigs = 2; length_ = 300000
    for (c = 1; c <= contigs; c++) {
        sequence[c] = ""
        for (i = 0; i < length_; i++) sequence[c] = sequence[c] base[int(rnd() * 4) + 1]
        print ">chr" c > "reference.fa"
        for (i = 1; i <= length_; i += 60) print substr(sequence[c], i, 60) > "reference.fa"
    }
    long_reads("hifi.fq", 200, 0.002, 0.002)
    long_reads("ont.fq", 200, 0.03, 0.03)
    for (p = 1; p <= 2000; p++) {
        c = int(rnd() * contigs) + 1
        insert = 300 + int(rnd() * 200)
        start = int(rnd() * (length_ - insert)) + 1
        left = substitute(substr(sequence[c], start, 150), 3)
        right = reverse_complement(substitute(substr(sequence[c], start + insert - 150, 150), 3))
        name = "p" p "_chr" c "_" start
        printf "@%s/1\n%s\n+\n%s\n", name, left, quality(150) > "reads_1.fq"
        printf "@%s/2\n%s\n+\n%s\n", name, right, quality(150) > "reads_2.fq"
    }
}
# Reads of 2-12 kb from either strand with substitutions and short indels.
function long_reads(file, count, substitution, indel,    r, c, size, start, read, i, b, out) {
    for (r = 1; r <= count; r++) {
        c = int(rnd() * contigs) + 1
        size = 2000 + int(rnd() * 10000)
        start = int(rnd() * (length_ - size)) + 1
        read = substr(sequence[c], start, size)
        out = ""
        for (i = 1; i <= size; i++) {
            b = substr(read, i, 1)
            if (rnd() < indel) {
                if (rnd() < 0.5) continue
                out = out base[int(rnd() * 4) + 1]
            }
            if (rnd() < substitution) b = base[int(rnd() * 4) + 1]
            out = out b
        }
        strand = rnd() < 0.5 ? "+" : "-"
        if (strand == "-") out = reverse_complement(out)
        printf "@r%d_chr%d_%d_%s\n%s\n+\n%s\n", r, c, start, strand, out, quality(length(out)) > file
    }
}
function substitute(read_sequence, n,    i, position) {
    for (i = 0; i < n; i++) {
        position = int(rnd() * length(read_sequence)) + 1
        read_sequence = substr(read_sequence, 1, position - 1) base[int(rnd() * 4) + 1] \
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

# Primary alignments of every read must start within 50 bp of its origin.
check() {
    awk -F '\t' -v expected="$2" '
        /^@/ { next }
        !bit($2, 256) && !bit($2, 2048) && !bit($2, 128) {
            total++
            split($1, truth, "_")
            if ($3 == truth[2] && $4 >= truth[3] - 50 && $4 <= truth[3] + 50) correct++
        }
        END {
            printf "%s: reads=%d correctly placed=%d\n", FILENAME, total, correct
            exit !(total == expected && correct >= expected * 0.99)
        }
        function bit(flags, value) { return int(flags / value) % 2 }' "$1"
}
records() { grep -v '^@PG' "$1" > "$1.records"; }

for preset in map-hifi map-ont; do
    reads=${preset#map-}.fq
    "$binary" -t 4 -ax "$preset" reference.fa "$reads" > "$preset.sam" 2> "$preset.log"
    grep -q '^@PG	ID:minimap2	PN:minimap2	VN:[^	]*-wgse-' "$preset.sam"
    check "$preset.sam" 200
    "$binary" -x "$preset" -d "$preset.mmi" reference.fa 2> "$preset.index.log"
    "$binary" -t 4 -ax "$preset" "$preset.mmi" "$reads" > "$preset.mmi.sam" 2> "$preset.mmi.log"
    "$binary" -t 4 -ax "$preset" "$preset.mmi" "$reads" > "$preset.again.sam" 2> "$preset.again.log"
    for sam in "$preset.sam" "$preset.mmi.sam" "$preset.again.sam"; do records "$sam"; done
    cmp "$preset.sam.records" "$preset.mmi.sam.records"
    cmp "$preset.sam.records" "$preset.again.sam.records"
done
"$binary" -t 4 -ax sr reference.fa reads_1.fq reads_2.fq > sr.sam 2> sr.log
check sr.sam 2000
echo "minimap2 alignments verified"

# --metal or --cuda must write the same alignments, with gap fills on the GPU, where the
# build's GPU is usable.
"$binary" capabilities --json > capabilities.json
gpu=""
if grep -q '"metal_available":true' capabilities.json; then gpu=--metal; fi
if grep -q '"cuda_available":true' capabilities.json; then gpu=--cuda; fi
if [ -n "$gpu" ]; then
    for preset in map-hifi map-ont; do
        "$binary" "$gpu" -t 4 -ax "$preset" "$preset.mmi" "${preset#map-}.fq" \
            > "$preset.gpu.sam" 2> "$preset.gpu.log"
        records "$preset.gpu.sam"
        cmp "$preset.sam.records" "$preset.gpu.sam.records"
        grep -Eq 'on the GPU \([1-9][0-9]* used\)' "$preset.gpu.log"
        # @PG CL records that the GPU was used (PR #559 review).
        grep -q "^@PG	ID:minimap2	.*	CL:minimap2 $gpu -t 4 -ax $preset " "$preset.gpu.sam"
    done
    echo "minimap2 $gpu alignments verified"
fi
