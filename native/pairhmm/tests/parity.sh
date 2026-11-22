#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Usage: parity.sh GATK_JAR SIDECAR_JAR OUT_DIR [BATCHES] [THREADS]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
GATK=$(realpath "$1")
SIDECAR=$(realpath "$2")
OUT=$(realpath -m "$3")
BATCHES=${4:-1000}
THREADS=${5:-1}
mkdir -p "$OUT/classes" "$OUT/tmp"
javac --release 17 -cp "$GATK" -d "$OUT/classes" "$HERE/PairHmmProbe.java"
COMMON=(-Xmx1g "-XX:ActiveProcessorCount=$THREADS" "-Djava.io.tmpdir=$OUT/tmp"
    -cp "$OUT/classes:$GATK")
java "${COMMON[@]}" PairHmmProbe "$OUT/stock.tsv" "$BATCHES" "$THREADS" \
    > "$OUT/stock.log" 2>&1
java "-Xbootclasspath/a:$SIDECAR" "${COMMON[@]}" \
    PairHmmProbe "$OUT/cuda.tsv" "$BATCHES" "$THREADS" > "$OUT/cuda.log" 2>&1
cmp "$OUT/stock.tsv" "$OUT/cuda.tsv"
grep -Eq 'completed [1-9][0-9]* GPU read/haplotype pairs' "$OUT/cuda.log"
# CUDA's existing process-local device mask models an unavailable GPU at the
# driver boundary; it is not WGSE configuration or a mock of the computation.
CUDA_VISIBLE_DEVICES='' java "-Xbootclasspath/a:$SIDECAR" "${COMMON[@]}" \
    PairHmmProbe "$OUT/fallback.tsv" "$BATCHES" "$THREADS" > "$OUT/fallback.log" 2>&1
cmp "$OUT/stock.tsv" "$OUT/fallback.tsv"
grep -q 'no usable NVIDIA CUDA device/driver.*GKL CPU fallback' "$OUT/fallback.log"
grep -q 'completed 0 GPU read/haplotype pairs' "$OUT/fallback.log"
echo "Exact GKL/CUDA/unavailable-GPU fallback likelihood parity: $BATCHES batches, $THREADS threads"
