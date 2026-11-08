#!/bin/sh
# Compare wgse-bwa-mem3 with a build of the unmodified upstream bwa-mem3 sources on a
# repeat-rich synthetic genome: segmental duplications (diverged and inverted copies, within
# and across chromosomes), satellite-like and long tandem repeats, an N gap and soft masking.
# The indexes each builds must be byte-identical, and the SAM records (all but @PG) of paired
# and single-end reads must be identical in each output mode: --compat=bwa-mem,
# --compat=bwa-mem2, bwa-mem3's own records, and --fast.
# Usage: upstream.sh <wgse-bwa-mem3> <bwa-mem3-upstream> <work-dir>
set -eu
wgse=$1
upstream=$2
work=$3

rm -rf "$work"
mkdir -p "$work/upstream" "$work/wgse"
cd "$work"

awk 'BEGIN {
    srand(542)
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

build_index() {
    cp reference.fa "$1/reference.fa"
    "$2" index "$1/reference.fa" > "$1/index.log" 2>&1
}
build_index upstream "$upstream"
build_index wgse "$wgse"
for suffix in amb ann bwt.2bit.64 pac; do
    cmp "upstream/reference.fa.$suffix" "wgse/reference.fa.$suffix"
done

for mode in bwa-mem bwa-mem2 plain fast; do
    case $mode in
        plain) options="" ;;
        fast) options=--fast ;;
        *) options=--compat=$mode ;;
    esac
    for tool in upstream wgse; do
        binary=$upstream
        [ "$tool" = wgse ] && binary=$wgse
        # shellcheck disable=SC2086 # $options holds zero or one option
        "$binary" mem -t 4 $options "$tool/reference.fa" reads_1.fq reads_2.fq \
            > "$tool.$mode.pe.sam" 2> "$tool.$mode.pe.log"
        # shellcheck disable=SC2086
        "$binary" mem -t 4 $options "$tool/reference.fa" reads_1.fq \
            > "$tool.$mode.se.sam" 2> "$tool.$mode.se.log"
        records "$tool.$mode.pe.sam"
        records "$tool.$mode.se.sam"
    done
    cmp "upstream.$mode.pe.sam.records" "wgse.$mode.pe.sam.records"
    cmp "upstream.$mode.se.sam.records" "wgse.$mode.se.sam.records"
    # The repeats must give ambiguous alignments (primary with MAPQ 0).
    awk -F '\t' '!/^@/ && int($2 / 256) % 2 == 0 && $5 == 0 { n++ } END { exit !(n > 0) }' \
        "upstream.$mode.pe.sam"
done
grep -q '^@PG	ID:bwa-mem3	PN:bwa-mem3	VN:[^	]*-wgse-' wgse.plain.pe.sam
# The @PG command line keeps the backend option; the records do not change.
"$wgse" --backend auto mem -t 4 wgse/reference.fa reads_1.fq > wgse.auto.se.sam 2> wgse.auto.se.log
grep -q '^@PG	ID:bwa-mem3	.*	CL:[^	]*wgse-bwa-mem3 --backend auto mem -t 4 ' wgse.auto.se.sam
records wgse.auto.se.sam
cmp wgse.plain.se.sam.records wgse.auto.se.sam.records
echo "wgse-bwa-mem3 matches upstream bwa-mem3 for --compat=bwa-mem, --compat=bwa-mem2, plain and --fast"
