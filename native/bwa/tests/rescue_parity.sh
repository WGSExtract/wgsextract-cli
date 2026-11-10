#!/bin/sh
# GPU mate rescue (mem_matesw's ksw_align2) must leave the paired SAM unchanged.
set -eu
aligner=$1
work=$2
mkdir -p "$work"
cd "$work"

# The @PG command line differs by the backend option; every other line must match.
same() {
    grep -v '^@PG' "$1" > "$1.records"
    grep -v '^@PG' "$2" > "$2.records"
    cmp "$1.records" "$2.records"
}
metric() {
    sed -n "s/.* $2=\([0-9]*\).*/\1/p" "$1"
}

# A random reference with near-copies of one region, so rescue finds suboptimal
# hits, and pairs in three orientations. Every third mate has a mismatch every
# 12 bp and often an indel, so it has no exact 19-mer seed and is only placed by
# mate rescue.
awk 'BEGIN {
    srand(364)
    split("A C G T", base, " ")
    for (i = 1; i <= 160000; ++i) random = random base[int(rand() * 4) + 1]
    repeat = substr(random, 40001, 3000)
    copy = repeat
    for (i = 50; i < 3000; i += 97) copy = substr(copy, 1, i - 1) base[int(rand() * 4) + 1] substr(copy, i + 1)
    ref = substr(random, 1, 41500) copy substr(random, 44501)
    print ">chr\n" ref > "reference.fa"
    complement["A"] = "T"; complement["C"] = "G"; complement["G"] = "C"; complement["T"] = "A"
    complement["N"] = "N"
    split("151 151 151 101 76 250 300", lengths, " ")
    for (p = 1; p <= 1500; ++p) {
        n = lengths[1 + p % 7]
        insert = 350 + int(rand() * 150)
        start = p % 5 == 0 ? 39000 + int(rand() * 6000) : 1 + int(rand() * (length(ref) - insert))
        left = substr(ref, start, n)
        right = substr(ref, start + insert - n, n)
        if (p % 3 == 0) {
            for (i = 1 + int(rand() * 12); i <= n; i += 12) right = substr(right, 1, i - 1) (substr(right, i, 1) == "A" ? "C" : "A") substr(right, i + 1)
            # Insertions and deletions exercise the striped lazy-F and E updates.
            at = 10 + int(rand() * (n - 20))
            gap = 1 + int(rand() * 4)
            if (p % 6 == 0) {
                inserted = ""
                for (i = 1; i <= gap; ++i) inserted = inserted base[int(rand() * 4) + 1]
                right = substr(right, 1, at) inserted substr(right, at + 1, n - at - gap)
            } else if (p % 9 == 0) {
                right = substr(right, 1, at) substr(right, at + gap + 1) substr(ref, start + insert, gap)
            }
        }
        if (p % 8 == 0) right = substr(right, 1, 30) "N" substr(right, 32)
        if (p % 13 == 0) right = substr(right, 1, 40) substr(right, 44)
        revleft = revright = ""
        for (i = length(left); i >= 1; --i) revleft = revleft complement[substr(left, i, 1)]
        for (i = length(right); i >= 1; --i) revright = revright complement[substr(right, i, 1)]
        orientation = p % 20
        if (orientation < 14) { one = left; two = revright }        # FR
        else if (orientation < 17) { one = revleft; two = right }   # RF (outward)
        else { one = left; two = right }                            # FF
        q1 = q2 = ""
        for (i = 1; i <= length(one); ++i) q1 = q1 "I"
        for (i = 1; i <= length(two); ++i) q2 = q2 "I"
        printf "@pair%d/1\n%s\n+\n%s\n", p, one, q1 > "pairs-R1.fq"
        printf "@pair%d/2\n%s\n+\n%s\n", p, two, q2 > "pairs-R2.fq"
    }
}'
"$aligner" index reference.fa > index.log 2>&1

# Regression (#548): a GPU command that never completes and that macOS reports
# no error for (as on a hung paravirtual GPU) is given up by the watchdog; the
# run finishes on the CPU with the same records. --gpu-inject-hang withholds
# the first command of a stage and gives up after 2 s.
if [ "${3:-}" = watchdog ]; then
    "$aligner" capabilities --json > capabilities.json
    if ! grep -q '"metal_available":true' capabilities.json; then
        grep -q '"metal_hardware_supported":true' capabilities.json && exit 1
        exit 77
    fi
    "$aligner" --cpu-upstream mem -t 4 -K 20000 reference.fa pairs-R1.fq pairs-R2.fq > cpu-hang.sam \
        2> cpu-hang.log
    for stage in collect_seeds locate_rows rescue_align extend; do
        status=0
        "$aligner" --metal --gpu-inject-hang "$stage" --gpu-policy all --gpu-rescue-batch 7 \
            mem -t 4 -K 20000 reference.fa pairs-R1.fq pairs-R2.fq > "hang-$stage.sam" \
            2> "hang-$stage.log" || status=$?
        test "$status" -eq 1
        grep -q "GPU watchdog: a Metal command ($stage" "hang-$stage.log"
        grep -q 'Metal execution failed (alignments were completed on the CPU)' "hang-$stage.log"
        same cpu-hang.sam "hang-$stage.sam"
        "$aligner" --backend auto --gpu-inject-hang "$stage" --gpu-policy all \
            --gpu-rescue-batch 7 mem -t 4 -K 20000 reference.fa pairs-R1.fq pairs-R2.fq \
            > "hang-auto-$stage.sam" 2> "hang-auto-$stage.log"
        grep -q "GPU watchdog: a Metal command ($stage" "hang-auto-$stage.log"
        same cpu-hang.sam "hang-auto-$stage.sam"
    done
    exit 0
fi

# The CPU backend replaces some of upstream's steps with exact batched versions
# (suffix-array locate, each pair's rescue windows, region sorts, reference
# unpacking); --cpu-upstream runs upstream's code. Their records must match.
cpu_parity() {
    name=$1
    shift
    "$aligner" mem "$@" reference.fa pairs-R1.fq pairs-R2.fq > "fast-$name.sam" 2> "fast-$name.log"
    "$aligner" --cpu-upstream mem "$@" reference.fa pairs-R1.fq pairs-R2.fq \
        > "upstream-$name.sam" 2> "upstream-$name.log"
    same "upstream-$name.sam" "fast-$name.sam"
    grep '^\[wgse-bwa\] cpu_located_rows' "fast-$name.log"
}
for threads in 1 4; do
    cpu_parity "t$threads" -t "$threads"
    test "$(metric "fast-t$threads.log" cpu_located_rows)" -gt 0
    test "$(metric "fast-t$threads.log" locate_misses)" -eq 0
    test "$(metric "fast-t$threads.log" rescue_cpu_hits)" -gt 1000
    test "$(metric "upstream-t$threads.log" cpu_located_rows)" -eq 0
    test "$(metric "upstream-t$threads.log" rescue_cpu_jobs)" -eq 0
done
cpu_parity chunks -t 4 -K 20000
cpu_parity gaps -t 4 -O 4,8 -E 2,1 -L 3,7 -U 9
test "$(metric fast-gaps.log rescue_cpu_hits)" -gt 0
cpu_parity insert -t 4 -I 420,40
cpu_parity nopair -t 4 -P
test "$(metric fast-nopair.log rescue_cpu_hits)" -gt 0
# 16-bit windows are left to upstream's ksw_align2.
cpu_parity wide -t 4 -A 4 -B 8
test "$(metric fast-wide.log rescue_cpu_jobs)" -eq 0
test "$(metric fast-wide.log rescue_cpu_skipped)" -gt 0
cpu_parity norescue -t 4 -S
test "$(metric fast-norescue.log rescue_cpu_jobs)" -eq 0
# A pair's windows beyond the byte bound are aligned by upstream on demand.
"$aligner" --cpu-rescue-bytes 1200 mem -t 4 reference.fa pairs-R1.fq pairs-R2.fq \
    > fast-capped.sam 2> fast-capped.log
same upstream-t4.sam fast-capped.sam
test "$(metric fast-capped.log rescue_cpu_skipped)" -gt 0
test "$(metric fast-capped.log rescue_cpu_hits)" -gt 0
# Single-end reads use the batched locate only.
"$aligner" mem -t 4 reference.fa pairs-R1.fq > fast-single.sam 2> fast-single.log
"$aligner" --cpu-upstream mem -t 4 reference.fa pairs-R1.fq > upstream-single.sam \
    2> upstream-single.log
same upstream-single.sam fast-single.sam
test "$(metric fast-single.log cpu_located_rows)" -gt 0
cpu_parity flags -t 4 -M -Y
# Asymmetric gaps, clipping penalties, band and z-drop (PR #596 review).
cpu_parity scoring -t 4 -A 3 -B 5 -O 8,9 -E 2,1 -L 0,30 -w 20 -d 50
# Regression (PR #581 review): --cpu-rescue-batch off leaves rescue to upstream.
"$aligner" --cpu-rescue-batch off mem -t 4 reference.fa pairs-R1.fq pairs-R2.fq \
    > fast-nobatch.sam 2> fast-nobatch.log
same upstream-t4.sam fast-nobatch.sam
test "$(metric fast-nobatch.log rescue_cpu_jobs)" -eq 0
# Interleaved pairs (-p).
paste -d '\n' pairs-R1.fq pairs-R2.fq | paste - - - - - - - - \
    | awk -F '\t' '{ for (i = 1; i <= 8; i += 2) print $i; for (i = 2; i <= 8; i += 2) print $i }' \
    > pairs-interleaved.fq
"$aligner" mem -t 4 -p reference.fa pairs-interleaved.fq > fast-interleaved.sam 2> fast-interleaved.log
"$aligner" --cpu-upstream mem -t 4 -p reference.fa pairs-interleaved.fq \
    > upstream-interleaved.sam 2> upstream-interleaved.log
same upstream-interleaved.sam fast-interleaved.sam
test "$(metric fast-interleaved.log rescue_cpu_hits)" -gt 1000
# -p with some unpaired reads, which smart pairing maps as single-end (PR #596 review):
# drop the second mate of every tenth pair.
awk '/^@pair[0-9]*0\/2$/ { skip = 4 } skip { --skip; next } { print }' pairs-interleaved.fq \
    > mixed-interleaved.fq
"$aligner" mem -t 4 -p reference.fa mixed-interleaved.fq > fast-mixed.sam 2> /dev/null
"$aligner" --cpu-upstream mem -t 4 -p reference.fa mixed-interleaved.fq > upstream-mixed.sam \
    2> /dev/null
same upstream-mixed.sam fast-mixed.sam
test "$(grep -v '^@' fast-mixed.sam | awk '$2 % 2 == 0' | wc -l)" -gt 100
# Regression (PR #581 review): aln, samse, sampe and bwasw also unpack the
# reference with the CPU backend's NEON code.
for mode in fast upstream; do
    option=
    test "$mode" = upstream && option=--cpu-upstream
    "$aligner" $option aln -t 4 reference.fa pairs-R1.fq > "$mode-1.sai" 2> /dev/null
    "$aligner" $option aln -t 4 reference.fa pairs-R2.fq > "$mode-2.sai" 2> /dev/null
    "$aligner" $option samse reference.fa "$mode-1.sai" pairs-R1.fq > "$mode-samse.sam" 2> /dev/null
    "$aligner" $option sampe reference.fa "$mode-1.sai" "$mode-2.sai" pairs-R1.fq pairs-R2.fq \
        > "$mode-sampe.sam" 2> /dev/null
    # Upstream bwasw breaks ties between repeat copies differently with threads.
    "$aligner" $option bwasw -t 1 reference.fa pairs-R1.fq > "$mode-bwasw.sam" 2> /dev/null
done
for mode in samse sampe bwasw; do
    same "upstream-$mode.sam" "fast-$mode.sam"
    test "$(grep -vc '^@' "fast-$mode.sam")" -gt 1000
done

# The CPU backend seeds blocks of reads with their FM-index walks interleaved
# (smem_batch.cpp). Reads of 1 to 900 bp (and some of 5000) from the repeat copies, tandem and
# homopolymer runs, with mismatches and Ns, through all three of
# mem_collect_intv()'s passes (-y enables the third).
awk 'BEGIN {
    srand(543)
    split("A C G T", base, " ")
    getline < "reference.fa"
    getline ref < "reference.fa"
    for (i = 1; i <= 300; ++i) tandem = tandem "ACGT"
    for (i = 1; i <= 1200; ++i) homo = homo "A"
    split("1 18 19 20 31 32 33 76 151 250 511 900", lengths, " ")
    for (r = 1; r <= 3000; ++r) {
        n = lengths[1 + r % 12]
        # Longer than the NEON ksw_extend2 limit (4096 bp).
        if (r % 250 == 0) n = 5000
        start = r % 4 == 0 ? 39000 + int(rand() * 6000) : 1 + int(rand() * (length(ref) - n))
        seq = substr(ref, start, n)
        if (r % 7 == 0) seq = substr(seq, 1, n / 2) substr(tandem, 1 + r % 4, n - int(n / 2))
        if (r % 11 == 0) seq = substr(homo, 1, n / 3) substr(seq, 1, n - int(n / 3))
        if (r % 3 == 0)
            for (i = 15; i < n; i += 23) seq = substr(seq, 1, i - 1) base[int(rand() * 4) + 1] substr(seq, i + 1)
        if (r % 5 == 0)
            for (i = 1 + r % 17; i <= n; i += 37) seq = substr(seq, 1, i - 1) "N" substr(seq, i + 1)
        qual = ""
        for (i = 1; i <= length(seq); ++i) qual = qual "I"
        printf "@read%d\n%s\n+\n%s\n", r, seq, qual > "seeds.fq"
    }
}'
seed_parity() {
    name=$1
    shift
    "$aligner" mem -t 4 "$@" reference.fa seeds.fq > "fast-seeds-$name.sam" 2> /dev/null
    "$aligner" --cpu-upstream mem -t 4 "$@" reference.fa seeds.fq > "upstream-seeds-$name.sam" \
        2> /dev/null
    same "upstream-seeds-$name.sam" "fast-seeds-$name.sam"
    test "$(grep -v '^@' "fast-seeds-$name.sam" | awk '$2 < 256 && $3 != "*"' | wc -l)" -gt 1000
}
seed_parity default
seed_parity split -k 15 -r 0.6
seed_parity third -k 17 -y 30
seed_parity wide -k 25 -r 3 -c 50

# Skip (77) only on machines without supported Metal hardware.
"$aligner" capabilities --json > capabilities.json
if ! grep -q '"metal_available":true' capabilities.json; then
    grep -q '"metal_hardware_supported":true' capabilities.json && exit 1
    exit 77
fi

run() {
    name=$1
    shift
    "$aligner" --cpu-upstream mem "$@" reference.fa pairs-R1.fq pairs-R2.fq > "cpu-$name.sam" 2> "cpu-$name.log"
    "$aligner" --gpu-policy all --gpu-rescue-batch 7 --metal mem "$@" reference.fa \
        pairs-R1.fq pairs-R2.fq > "metal-$name.sam" 2> "metal-$name.log"
    same "cpu-$name.sam" "metal-$name.sam"
    grep '^\[wgse-bwa\] rescue_batches' "metal-$name.log"
}

# Each GPU result is compared with upstream ksw_align2 for the same inputs.
"$aligner" --gpu-policy all --gpu-rescue-batch 7 --gpu-rescue-verify --metal mem -t 4 \
    reference.fa pairs-R1.fq pairs-R2.fq > verify.sam 2> verify.log
"$aligner" --cpu-upstream mem -t 4 reference.fa pairs-R1.fq pairs-R2.fq > cpu.sam 2> cpu.log
same cpu.sam verify.sam
# Rescue windows cover every orientation BWA infers from the pairs.
for orientation in FF FR RF; do
    grep -q "analyzing insert size distribution for orientation $orientation" cpu.log
done
cat verify.log | grep '^\[wgse-bwa\] rescue'
test "$(metric verify.log rescue_batches)" -ge 3
test "$(metric verify.log rescue_hits)" -gt 1000
test "$(metric verify.log rescue_misses)" -eq 0
test "$(metric verify.log rescue_mismatches)" -eq 0
# Mates over 256 bp stay on upstream ksw_align2.
test "$(metric verify.log rescue_skipped)" -gt 0

for threads in 1 4; do
    run "t$threads" -t "$threads"
    test "$(metric "metal-t$threads.log" rescue_hits)" -gt 1000
done
# Small chunks: Metal maps each chunk while the previous one pairs.
run chunks -t 4 -K 20000
test "$(metric metal-chunks.log rescue_hits)" -gt 0
test "$(metric metal-chunks.log overlapped_chunks)" -gt 0
# macOS aborts command buffers that delay the display (ImpactingInteractivity),
# for example while another process uses the GPU; that work is committed again.
# Report every second piece of seeding, locate, rescue and extension work as
# interrupted, so its kernels run twice: the results must not change.
"$aligner" --gpu-inject-interruptions 2 --gpu-policy all --gpu-rescue-batch 7 \
    --gpu-rescue-verify --gpu-extend-verify --metal mem -t 4 -K 20000 reference.fa \
    pairs-R1.fq pairs-R2.fq > interrupted.sam 2> interrupted.log
same cpu-chunks.sam interrupted.sam
test "$(grep -c 'CPU fallback' interrupted.log)" -eq 0
test "$(metric interrupted.log interruptions)" -gt 0
test "$(metric interrupted.log rescue_hits)" -gt 0
test "$(metric interrupted.log rescue_mismatches)" -eq 0
test "$(metric interrupted.log extend_hits)" -gt 0
test "$(metric interrupted.log extend_mismatches)" -eq 0
# Other gap penalties and clipping; a manual insert size distribution.
run gaps -t 4 -O 4,8 -E 2,1 -L 3,7 -U 9
test "$(metric metal-gaps.log rescue_hits)" -gt 0
run insert -t 4 -I 420,40
test "$(metric metal-insert.log rescue_hits)" -gt 0
# -P skips pairing but still rescues mates.
run nopair -t 4 -P
test "$(metric metal-nopair.log rescue_hits)" -gt 0
# A larger match score makes mem_matesw use 16-bit ksw_i16, which stays on the CPU.
run wide -t 4 -A 4 -B 8
test "$(metric metal-wide.log rescue_hits)" -eq 0
test "$(metric metal-wide.log rescue_skipped)" -gt 0
# -S disables rescue entirely.
run norescue -t 4 -S
test "$(metric metal-norescue.log rescue_batches)" -eq 0

# A large mismatch penalty saturates ksw_u8 on exact 151 bp mates (which -k 160
# keeps from seeding); upstream then runs an empty reverse pass, so the GPU
# declines those windows and mem_matesw() computes them as upstream does.
awk 'BEGIN {
    srand(7)
    while ((getline line < "reference.fa") > 0) if (line !~ /^>/) ref = ref line
    complement["A"] = "T"; complement["C"] = "G"; complement["G"] = "C"; complement["T"] = "A"
    for (p = 1; p <= 200; ++p) {
        start = 1 + int(rand() * (length(ref) - 500))
        right = substr(ref, start + 380 + int(rand() * 40) - 151, 151)
        two = ""
        for (i = 151; i >= 1; --i) two = two complement[substr(right, i, 1)]
        q1 = q2 = ""
        for (i = 1; i <= 200; ++i) q1 = q1 "I"
        for (i = 1; i <= 151; ++i) q2 = q2 "I"
        printf "@sat%d/1\n%s\n+\n%s\n", p, substr(ref, start, 200), q1 > "sat-R1.fq"
        printf "@sat%d/2\n%s\n+\n%s\n", p, two, q2 > "sat-R2.fq"
    }
}'
"$aligner" --cpu-upstream mem -t 4 -k 160 -B 120 -I 400,40 reference.fa sat-R1.fq sat-R2.fq \
    > cpu-saturate.sam 2> cpu-saturate.log
"$aligner" --gpu-policy all --metal mem -t 4 -k 160 -B 120 -I 400,40 reference.fa \
    sat-R1.fq sat-R2.fq > metal-saturate.sam 2> metal-saturate.log
same cpu-saturate.sam metal-saturate.sam
test "$(metric metal-saturate.log rescue_declined)" -eq 200
test "$(metric metal-saturate.log rescue_misses)" -eq 0

# A rescue batch that fails after the first one (while the next is packed) is
# finished on the CPU, as is the rest. Explicit --metal still reports it.
status=0
"$aligner" --gpu-inject-rescue-failure --gpu-policy all --gpu-rescue-batch 7 --metal mem -t 4 reference.fa \
    pairs-R1.fq pairs-R2.fq > failed-metal.sam 2> failed-metal.log || status=$?
test "$status" -eq 1
grep -q 'CPU fallback: injected GPU rescue failure' failed-metal.log
test "$(metric failed-metal.log rescue_batches)" -ge 1
grep -q 'Metal execution failed (alignments were completed on the CPU)' failed-metal.log
same cpu.sam failed-metal.sam
"$aligner" --gpu-inject-rescue-failure --gpu-policy all --gpu-rescue-batch 7 --backend auto mem -t 4 \
    reference.fa pairs-R1.fq pairs-R2.fq > failed-auto.sam 2> failed-auto.log
grep -q 'CPU fallback: injected GPU rescue failure' failed-auto.log
same cpu.sam failed-auto.sam
# The same while the next chunk is being mapped on the GPU.
status=0
"$aligner" --gpu-inject-rescue-failure --gpu-policy all --gpu-rescue-batch 7 --metal mem -t 4 \
    -K 20000 reference.fa pairs-R1.fq pairs-R2.fq > failed-chunks.sam 2> failed-chunks.log \
    || status=$?
test "$status" -eq 1
grep -q 'CPU fallback: injected GPU rescue failure' failed-chunks.log
same cpu-chunks.sam failed-chunks.sam

# CPU threads align the rescue windows of a pair they take in one interleaved
# batch (ksw_oracle.c.in). After a GPU rescue failure they take every remaining
# pair; verification compares each batched result with upstream ksw_align2.
cpu_rescue_bytes=4194304
cpu_batch() {
    name=$1
    shift
    "$aligner" --cpu-rescue-bytes "$cpu_rescue_bytes" --gpu-inject-rescue-failure \
        --gpu-rescue-verify --gpu-policy all --gpu-rescue-batch 7 --backend auto \
        mem "$@" reference.fa pairs-R1.fq pairs-R2.fq > "batch-$name.sam" 2> "batch-$name.log"
    same "cpu-$name.sam" "batch-$name.sam"
    grep '^\[wgse-bwa\] rescue_batches' "batch-$name.log"
    test "$(metric "batch-$name.log" rescue_mismatches)" -eq 0
}
cpu_batch t4 -t 4
test "$(metric batch-t4.log rescue_cpu_hits)" -gt 100
cpu_batch gaps -t 4 -O 4,8 -E 2,1 -L 3,7 -U 9
test "$(metric batch-gaps.log rescue_cpu_hits)" -gt 100
# Regression (PR #502 review): 16-bit windows are left to upstream ksw_align2
# on demand rather than aligned speculatively.
cpu_batch wide -t 4 -A 4 -B 8
test "$(metric batch-wide.log rescue_cpu_jobs)" -eq 0
test "$(metric batch-wide.log rescue_cpu_skipped)" -gt 100
# Regression (PR #502 review): windows past a thread's byte cap are aligned by
# upstream ksw_align2 while the rest of the pair's windows stay batched.
cpu_rescue_bytes=3000
cp cpu-t4.sam cpu-capped.sam
cpu_batch capped -t 4
test "$(metric batch-capped.log rescue_cpu_hits)" -gt 100
test "$(metric batch-capped.log rescue_cpu_skipped)" -gt 100
