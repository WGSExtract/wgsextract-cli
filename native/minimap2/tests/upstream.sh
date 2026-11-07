#!/bin/sh
# Compare wgse-minimap2 with a build of the unmodified upstream minimap2 sources on a
# repeat-rich synthetic genome: segmental duplications (diverged and inverted copies, within
# and across chromosomes), short, satellite-like and long tandem repeats, N gaps and soft
# masking. For map-hifi, map-ont, map-pb and sr, the indexes each builds must be
# byte-identical, and the SAM records (all but @PG) of reads drawn from the whole genome
# must be identical, whether wgse-minimap2 maps the FASTA or loads upstream's index.
# Usage: upstream.sh <wgse-minimap2> <minimap2-upstream> <work-dir>
set -eu
wgse=$1
upstream=$2
work=$3

rm -rf "$work"
mkdir -p "$work"
cd "$work"

awk 'BEGIN {
    srand(537)
    split("A C G T", base, " ")
    complement["A"] = "T"; complement["C"] = "G"; complement["G"] = "C"; complement["T"] = "A"
    complement["a"] = "t"; complement["c"] = "g"; complement["g"] = "c"; complement["t"] = "a"
    complement["N"] = "N"
    d1 = random(15000); d2 = random(20000)
    sequence[1] = random(60000) d1 random(40000) mutate(repeat(random(30), 300), 0.02) \
        random(40000) mutate(d1, 0.01) random(30000) mutate(repeat(random(171), 60), 0.03) \
        random(40000) reverse_complement(mutate(d1, 0.005)) random(30000) \
        mutate(repeat(random(2500), 6), 0.005) random(40000) d2 random(20000) \
        mutate(d2, 0.002) random(30000)
    sequence[2] = random(50000) mutate(d2, 0.02) random(5000) repeat("N", 2000) random(45000) \
        tolower(mutate(d1, 0.015)) random(50000)
    contigs = 2
    for (c = 1; c <= contigs; c++) {
        print ">chr" c > "reference.fa"
        for (i = 1; i <= length(sequence[c]); i += 60) print substr(sequence[c], i, 60) > "reference.fa"
        sequence[c] = toupper(sequence[c])
    }
    long_reads("hifi.fq", 150, 0.002, 0.002)
    long_reads("ont.fq", 150, 0.03, 0.03)
    long_reads("pb.fq", 150, 0.02, 0.08)
    for (p = 1; p <= 3000; p++) {
        c = int(rand() * contigs) + 1
        insert = 300 + int(rand() * 200)
        start = int(rand() * (length(sequence[c]) - insert)) + 1
        left = mutate(substr(sequence[c], start, 150), 0.01)
        right = reverse_complement(mutate(substr(sequence[c], start + insert - 150, 150), 0.01))
        printf "@p%d\n%s\n+\n%s\n", p, left, quality(150) > "reads_1.fq"
        printf "@p%d\n%s\n+\n%s\n", p, right, quality(150) > "reads_2.fq"
    }
}
function random(n,    s, piece, i) {
    s = ""
    while (n > 0) {
        piece = ""
        for (i = 0; i < 100 && i < n; i++) piece = piece base[int(rand() * 4) + 1]
        s = s piece; n -= i
    }
    return s
}
function repeat(unit, copies,    s) {
    s = ""
    while (copies-- > 0) s = s unit
    return s
}
function mutate(s, rate,    n, position) {
    for (n = int(length(s) * rate); n > 0; n--) {
        position = int(rand() * length(s)) + 1
        s = substr(s, 1, position - 1) base[int(rand() * 4) + 1] substr(s, position + 1)
    }
    return s
}
# Reads of 2-12 kb from either strand with substitutions and short indels.
function long_reads(file, count, substitution, indel,    r, c, size, start, read, i, b, out, piece) {
    for (r = 1; r <= count; r++) {
        c = int(rand() * contigs) + 1
        size = 2000 + int(rand() * 10000)
        start = int(rand() * (length(sequence[c]) - size)) + 1
        read = substr(sequence[c], start, size)
        out = ""; piece = ""
        for (i = 1; i <= size; i++) {
            b = substr(read, i, 1)
            if (rand() < indel) {
                if (rand() < 0.5) continue
                piece = piece base[int(rand() * 4) + 1]
            }
            if (rand() < substitution) b = base[int(rand() * 4) + 1]
            piece = piece b
            if (length(piece) >= 100) { out = out piece; piece = "" }
        }
        out = out piece
        if (rand() < 0.5) out = reverse_complement(out)
        printf "@r%d\n%s\n+\n%s\n", r, out, quality(length(out)) > file
    }
}
function reverse_complement(s,    i, out, piece) {
    out = ""; piece = ""
    for (i = length(s); i >= 1; i--) {
        piece = piece complement[substr(s, i, 1)]
        if (length(piece) >= 100) { out = out piece; piece = "" }
    }
    return out piece
}
function quality(n,    q) {
    q = ""
    while (length(q) < n) q = q "IIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII"
    return substr(q, 1, n)
}'

records() { grep -v '^@PG' "$1" > "$1.records"; }

for preset in map-hifi map-ont map-pb sr; do
    case $preset in
        sr) reads="reads_1.fq reads_2.fq" ;;
        *) reads=${preset#map-}.fq ;;
    esac
    "$upstream" -t 4 -x "$preset" -d "upstream.$preset.mmi" reference.fa 2> "upstream.$preset.index.log"
    "$wgse" -t 4 -x "$preset" -d "wgse.$preset.mmi" reference.fa 2> "wgse.$preset.index.log"
    cmp "upstream.$preset.mmi" "wgse.$preset.mmi"
    # shellcheck disable=SC2086 # $reads holds one or two file names
    "$upstream" -t 4 -ax "$preset" reference.fa $reads > "upstream.$preset.sam" 2> "upstream.$preset.log"
    # shellcheck disable=SC2086
    "$wgse" -t 4 -ax "$preset" reference.fa $reads > "wgse.$preset.sam" 2> "wgse.$preset.log"
    # shellcheck disable=SC2086
    "$wgse" -t 4 -ax "$preset" "upstream.$preset.mmi" $reads > "wgse.$preset.mmi.sam" 2> "wgse.$preset.mmi.log"
    for sam in "upstream.$preset.sam" "wgse.$preset.sam" "wgse.$preset.mmi.sam"; do records "$sam"; done
    cmp "upstream.$preset.sam.records" "wgse.$preset.sam.records"
    cmp "upstream.$preset.sam.records" "wgse.$preset.mmi.sam.records"
    # The repeats must give ambiguous alignments (secondary, or primary with MAPQ 0).
    awk -F '\t' '!/^@/ && int($2 / 4) % 2 == 0 && $5 == 0 { n++ } END { exit !(n > 0) }' \
        "upstream.$preset.sam"
done
echo "wgse-minimap2 matches upstream minimap2 for map-hifi, map-ont, map-pb and sr"
