#!/bin/sh
# Compare wgse-minimap2 with a build of the unmodified upstream minimap2 sources on a
# repeat-rich synthetic genome: segmental duplications (diverged and inverted copies, within
# and across chromosomes), short, satellite-like and long tandem repeats, N gaps and soft
# masking. For map-hifi, map-ont, map-pb and sr, the indexes each builds must be
# byte-identical, and the SAM records (all but @PG) of reads drawn from the whole genome
# must be identical, whether wgse-minimap2 maps the FASTA or loads upstream's index.
# Where Metal is available, so must those of --metal (gap fills on the GPU) with small
# batches, one or several reads per thread, most fills kept on the CPU, injected Metal
# interruptions, and an injected GPU failure, which must exit 1 after the CPU redoes the
# failed fills.
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
metal=false
if "$wgse" capabilities --json | grep -q '"metal_available":true'; then metal=true; fi

# metal_run <preset> <name> <expected exit> <wrapper options>... -- <minimap2 options>...
metal_run() {
    preset=$1 name=$2 expected=$3
    shift 3
    status=0
    # shellcheck disable=SC2086
    "$wgse" --metal "$@" -ax "$preset" reference.fa $reads > "metal.$preset.$name.sam" \
        2> "metal.$preset.$name.log" || status=$?
    test "$status" -eq "$expected"
    records "metal.$preset.$name.sam"
    cmp "upstream.$preset.sam.records" "metal.$preset.$name.sam.records"
}

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
    # sr keeps its gap fills on the CPU but still maps its reads as fibers.
    if $metal; then metal_run "$preset" default 0 -t 4; fi
    if $metal && [ "$preset" != sr ]; then
        grep -Eq 'on the GPU \([1-9][0-9]* used\)' "metal.$preset.default.log"
        metal_run "$preset" small 0 --gpu-fibers 3 --gpu-batch 5 -t 3
        metal_run "$preset" single 0 --gpu-fibers 1 -t 1
        metal_run "$preset" capped 0 --gpu-max-cost 3000 -t 2
        metal_run "$preset" interrupted 0 --gpu-inject-interruptions 1 -t 2
        grep -Eq '[1-9][0-9]* Metal interruptions' "metal.$preset.interrupted.log"
        metal_run "$preset" failed 1 --gpu-inject-failure -t 2
        grep -q 'a GPU batch failed (injected GPU failure)' "metal.$preset.failed.log"
        # Regression (#548): a GPU command that never completes, with no error
        # from macOS, is given up by the watchdog and its fills run on the CPU.
        metal_run "$preset" hang 1 --gpu-inject-hang ksw_fill -t 2
        grep -q 'a GPU batch failed (GPU watchdog: a Metal command (ksw_fill' \
            "metal.$preset.hang.log"
    fi
    # The repeats must give ambiguous alignments (secondary, or primary with MAPQ 0).
    awk -F '\t' '!/^@/ && int($2 / 4) % 2 == 0 && $5 == 0 { n++ } END { exit !(n > 0) }' \
        "upstream.$preset.sam"
done
# PR #559 review: a read of independent segments whose first segment resets the
# thread's arena (--cap-kalloc) must hand the new arena to the reads that start
# while a later segment waits for the GPU.
if $metal; then
    "$upstream" -t 2 --pairing=no --cap-kalloc 1 -ax map-hifi reference.fa hifi.fq hifi.fq \
        > upstream.km-reset.sam 2> upstream.km-reset.log
    "$wgse" --metal --gpu-fibers 4 -t 2 --pairing=no --cap-kalloc 1 -ax map-hifi reference.fa \
        hifi.fq hifi.fq > metal.km-reset.sam 2> metal.km-reset.log
    records upstream.km-reset.sam
    records metal.km-reset.sam
    cmp upstream.km-reset.sam.records metal.km-reset.sam.records
    # PR #559 review: an arena over the limit while other reads hold memory in it
    # must still be released, after those reads finish.
    grep -Eq ' [1-9][0-9]* arena drains' metal.km-reset.log
    # Fills whose int8 differences wrap (a match score this large) must be redone on the CPU.
    "$upstream" -t 2 -ax map-hifi -A85 -B19 -O39,81 -E3,1 reference.fa hifi.fq \
        > upstream.wrap.sam 2> upstream.wrap.log
    "$wgse" --metal -t 2 -ax map-hifi -A85 -B19 -O39,81 -E3,1 reference.fa hifi.fq \
        > metal.wrap.sam 2> metal.wrap.log
    records upstream.wrap.sam
    records metal.wrap.sam
    cmp upstream.wrap.sam.records metal.wrap.sam.records
    grep -Eq '[1-9][0-9]* redone on the CPU' metal.wrap.log
    # PR #657 review: scorings at the edges of the 16-bit kernels' bounds (each pair is just
    # inside and just outside one bound) must give upstream's records with the kernels named.
    for scoring in "16-bit -A104 -B40 -O20,40 -E2,1" "8-bit -A105 -B40 -O20,40 -E2,1" \
        "16-bit -A10 -B60 -O30,68 -E4,1" "8-bit -A10 -B60 -O30,69 -E4,1" "16-bit -A20 -B6 -O2,10 -E2,1"; do
        set -- $scoring
        kernels=$1; shift
        name=scoring$(echo "$*" | tr -d ' ,-')
        "$upstream" -t 2 -ax map-hifi "$@" reference.fa hifi.fq > "upstream.$name.sam" 2> "upstream.$name.log"
        "$wgse" --metal -t 2 -ax map-hifi "$@" reference.fa hifi.fq > "metal.$name.sam" 2> "metal.$name.log"
        records "upstream.$name.sam"
        records "metal.$name.sam"
        cmp "upstream.$name.sam.records" "metal.$name.sam.records"
        grep -q "; $kernels kernels;" "metal.$name.log"
        grep -Eq 'on the GPU \([1-9][0-9]* used\)' "metal.$name.log"
    done
    # PR #657 review: scorings with q > q2 cannot reach the kernels: minimap2 requires E1 > E2 and
    # O1 + E1 < O2 + E2, which imply q2 > q, and rejects any other dual-gap scoring before mapping.
    for gaps in "-O50,10 -E4,44" "-O12,8 -E4,2" "-O30,10 -E2,1"; do
        status=0
        "$wgse" --metal -t 2 -ax map-hifi -B80 $gaps reference.fa hifi.fq > /dev/null 2> metal.qgtq2.log || status=$?
        [ "$status" -ne 0 ]
        grep -q 'dual gap penalties violating' metal.qgtq2.log
    done
fi
echo "wgse-minimap2 matches upstream minimap2 for map-hifi, map-ont, map-pb and sr"
if $metal; then echo "wgse-minimap2 --metal matches upstream minimap2 for map-hifi, map-ont, map-pb and sr"; fi
