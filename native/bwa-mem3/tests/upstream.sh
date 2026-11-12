#!/bin/sh
# Compare wgse-bwa-mem3 with a build of the unmodified upstream bwa-mem3 sources on a
# repeat-rich synthetic genome: segmental duplications (diverged and inverted copies, within
# and across chromosomes), satellite-like and long tandem repeats, an N gap and soft masking.
# The indexes each builds must be byte-identical, and the SAM records (all but @PG) of paired
# and single-end reads must be identical in each output mode: --compat=bwa-mem,
# --compat=bwa-mem2, bwa-mem3's own records, and --fast; and with other seeding options
# (-k, -r, -y, -c) on reads that add long, N-rich and SMEM-rich ones.
# Options after the work directory go to wgse-bwa-mem3 before its command, for example
# --metal or --cuda; with either the GPU must have scored seed extensions (committing them again
# when --gpu-inject-interruptions reports them interrupted), and with
# --gpu-inject-extension-failure (or --gpu-inject-hang, a command the GPU watchdog gives up
# on) the CPU must report scoring them instead (and --metal or --cuda must then exit 1, with the same
# records; --gpu-inject-seed-failure likewise for seeding). With --gpu-seed-wait the GPU's
# SMEMs must have been used (and, with --gpu-verify-seeds, match upstream's). With
# --physical-memory below the run's needs, every alignment must warn about memory.
# Usage: upstream.sh <wgse-bwa-mem3> <bwa-mem3-upstream> <work-dir> [wgse-bwa-mem3 options]
set -eu
wgse=$1
upstream=$2
work=$3
shift 3
wgse_options="$*"

# --metal and --cuda exit 1 after a GPU failure; --backend auto does not.
expected_status() {
    case "$1: $wgse_options " in
    wgse:*" --gpu-inject-extension-failure "* | wgse:*" --gpu-inject-hang "* \
        | wgse:*" --gpu-inject-seed-failure "*) case " $wgse_options " in
        *" --metal "* | *" --cuda "*) echo 1 ;;
        *) echo 0 ;;
        esac ;;
    *) echo 0 ;;
    esac
}

rm -rf "$work"
mkdir -p "$work/upstream" "$work/wgse"
cd "$work"

# Skip (77) GPU runs only on machines without supported GPU hardware.
case " $wgse_options " in
*" --metal "* | *" --cuda "* | *" --backend "*)
    "$wgse" capabilities --json > capabilities.json
    if ! grep -Eq '"(metal|cuda)_available":true' capabilities.json; then
        grep -Eq '"(metal|cuda)_hardware_supported":true' capabilities.json && exit 1
        exit 77
    fi ;;
esac

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
    # Pairs the GPU leaves to the CPU or that stress seeding: 600-base reads (longer than the
    # GPU seeds), N-rich reads, and 500-base chimeras of 13-base pieces (many short SMEMs).
    for (p = 1; p <= 90; p++) {
        c = int(rand() * contigs) + 1
        start = int(rand() * (length(sequence[c]) - 1000)) + 1
        if (p % 3 == 0) {
            left = mutate(substr(sequence[c], start, 600), 0.01)
            right = reverse_complement(mutate(substr(sequence[c], start + 300, 600), 0.01))
        } else if (p % 3 == 1) {
            left = substr(sequence[c], start, 150)
            for (i = 7; i <= 150; i += 7) left = substr(left, 1, i - 1) "N" substr(left, i + 1)
            left = substr(left, 1, 60) repeat("N", 20) substr(left, 81)
            right = reverse_complement(substr(sequence[c], start + 300, 150))
        } else {
            left = ""
            while (length(left) < 500) {
                c = int(rand() * contigs) + 1
                left = left substr(sequence[c], int(rand() * (length(sequence[c]) - 13)) + 1, 13)
            }
            left = substr(left, 1, 500)
            right = reverse_complement(substr(sequence[c], start, 150))
        }
        printf "@o%d\n%s\n+\n%s\n", p, left, quality(length(left)) > "odd_1.fq"
        printf "@o%d\n%s\n+\n%s\n", p, right, quality(length(right)) > "odd_2.fq"
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

# align NAME OPTIONS READS_1 READS_2: each tool's paired and single-end records must match.
align() {
    for tool in upstream wgse; do
        binary=$upstream backend=
        [ "$tool" = wgse ] && binary=$wgse backend=$wgse_options
        for ends in pe se; do
            reads="$3 $4"
            [ $ends = se ] && reads=$3
            status=0
            # shellcheck disable=SC2086 # $2 holds mem options, $backend a few
            "$binary" $backend mem -t 4 $2 "$tool/reference.fa" $reads \
                > "$tool.$1.$ends.sam" 2> "$tool.$1.$ends.log" || status=$?
            [ "$status" -eq "$(expected_status "$tool")" ] || {
                echo "$tool $1 $ends exited $status" >&2
                exit 1
            }
            records "$tool.$1.$ends.sam"
        done
    done
    cmp "upstream.$1.pe.sam.records" "wgse.$1.pe.sam.records"
    cmp "upstream.$1.se.sam.records" "wgse.$1.se.sam.records"
}

for mode in bwa-mem bwa-mem2 plain fast; do
    case $mode in
        plain) options="" ;;
        fast) options=--fast ;;
        *) options=--compat=$mode ;;
    esac
    align $mode "$options" reads_1.fq reads_2.fq
    # The repeats must give ambiguous alignments (primary with MAPQ 0).
    awk -F '\t' '!/^@/ && int($2 / 256) % 2 == 0 && $5 == 0 { n++ } END { exit !(n > 0) }' \
        "upstream.$mode.pe.sam"
done
# Seeding options (-k, -r, -y with the third pass off and on, -c) on reads that include the
# long, N-rich and SMEM-rich ones.
cat reads_1.fq odd_1.fq > mixed_1.fq
cat reads_2.fq odd_2.fq > mixed_2.fq
align seeds-short "-k 10 -r 0.5 -y 100 -c 1000" mixed_1.fq mixed_2.fq
align seeds-no-third-pass "-k 15 -r 1.0 -y 0 -c 100" mixed_1.fq mixed_2.fq
grep -q '^@PG	ID:bwa-mem3	PN:bwa-mem3	VN:[^	]*-wgse-' wgse.plain.pe.sam
case " $wgse_options " in
*" --physical-memory "*)
    for log in wgse.*.log; do grep -q 'warning: this run needs about' "$log"; done ;;
esac
case " $wgse_options " in
*" --gpu-inject-hang "*)
    # The hung command's label (extend, or collect_smems for GPU seeding).
    label=$(echo " $wgse_options " | sed 's/.* --gpu-inject-hang \([^ ]*\) .*/\1/')
    for log in wgse.*.log; do
        grep -Eq "CPU fallback: GPU seed extension failed \(GPU watchdog: a (Metal|CUDA) command \($label" \
            "$log"
        if [ "$label" = collect_smems ]; then
            grep -q 'CPU fallback: GPU seeding failed: GPU watchdog' "$log"
        fi
    done ;;
*" --gpu-inject-seed-failure "*)
    # The CPU seeds instead; the GPU still extends seeds.
    for log in wgse.*.log; do
        grep -q 'CPU fallback: GPU seeding failed: injected' "$log"
        grep -Eq 'extension_batches=[1-9][0-9]* extension_pairs=[1-9]' "$log"
    done ;;
*" --gpu-inject-extension-failure "*)
    for log in wgse.*.log; do grep -q 'CPU fallback: GPU seed extension failed' "$log"; done ;;
*" --metal "* | *" --cuda "*)
    for log in wgse.*.log; do
        grep -Eq 'extension_batches=[1-9][0-9]* extension_pairs=[1-9]' "$log"
        if grep -q 'CPU fallback' "$log"; then exit 1; fi
        # The GPU collects SMEMs unless --gpu-seed off. On small inputs a chunk may map
        # before its batches finish; --gpu-seed-wait makes it wait, so the GPU's SMEMs are
        # used, and --gpu-verify-seeds checks them against upstream's.
        case " $wgse_options " in
        *" --gpu-seed off "*) if grep -q 'seed_chunks=' "$log"; then exit 1; fi ;;
        *" --gpu-seed-wait "*) grep -Eq 'seed_gpu_reads=[1-9].* seed_hits=[1-9][0-9]* seed_misses=0 ' "$log" ;;
        *) grep -Eq 'seed_chunks=[1-9]' "$log" ;;
        esac
        case " $wgse_options " in
        *" --gpu-verify-seeds "*) grep -q ' seed_mismatches=0 ' "$log" ;;
        esac
    done
    # Reads over 512 bases, and with more SMEMs than the GPU keeps, are seeded on the CPU.
    case " $wgse_options " in
    *" --gpu-seed-wait "*)
        for log in wgse.seeds-*.log; do grep -Eq 'seed_long_reads=[1-9]' "$log"; done
        for log in wgse.seeds-short.*.log; do grep -Eq 'seed_overflow_reads=[1-9]' "$log"; done ;;
    esac
    case " $wgse_options " in
    *" --gpu-inject-interruptions "*) grep -Eq 'extension_interruptions=[1-9]' wgse.plain.pe.log ;;
    esac ;;
esac
# The @PG command line keeps the backend option; the records do not change.
"$wgse" --backend auto mem -t 4 wgse/reference.fa reads_1.fq > wgse.auto.se.sam 2> wgse.auto.se.log
grep -q '^@PG	ID:bwa-mem3	.*	CL:[^	]*wgse-bwa-mem3 --backend auto mem -t 4 ' wgse.auto.se.sam
records wgse.auto.se.sam
cmp wgse.plain.se.sam.records wgse.auto.se.sam.records
echo "wgse-bwa-mem3 matches upstream bwa-mem3 for --compat=bwa-mem, --compat=bwa-mem2, plain and --fast"
