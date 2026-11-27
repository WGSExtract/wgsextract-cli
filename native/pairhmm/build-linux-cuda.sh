#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Usage: build-linux-cuda.sh WORK_DIR GATK_JAR OUT_DIR [--download]
set -euo pipefail
if [ "$#" -lt 3 ] || [ "$#" -gt 4 ]; then
    echo "Usage: $0 WORK_DIR GATK_JAR OUT_DIR [--download]" >&2
    exit 2
fi
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$(realpath -m "$1")
GATK_JAR=$(realpath "$2")
OUT=$(realpath -m "$3")
DOWNLOAD=${4:-}
case "$DOWNLOAD" in ""|--download) ;; *) echo "Unknown option: $DOWNLOAD" >&2; exit 2 ;; esac
GKL_SHA256=23f1aa7de874e79aedc35901397545e9e406266dfb1584f21300e440806a8950
test "$(uname -s)/$(uname -m)" = Linux/x86_64
for tool in nvcc g++ javac jar python3 curl sha256sum; do
    command -v "$tool" >/dev/null || { echo "Missing build dependency: $tool" >&2; exit 1; }
done
python3 - "$GATK_JAR" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    manifest = archive.read("META-INF/MANIFEST.MF").decode()
if "Implementation-Version: 4.6.2.0" not in manifest.splitlines():
    sys.exit("This sidecar is pinned to GATK 4.6.2.0 / GKL 0.8.11")
PY
mkdir -p "$WORK" "$OUT"
archive="$WORK/gkl-0.8.11.tar.gz"
# shellcheck disable=SC1091
source "$HERE/../../.github/actions/setup-pixi/fetch-pinned.sh"
if [ "$DOWNLOAD" = --download ]; then
    fetch_pinned https://codeload.github.com/IntelLabs/GKL/tar.gz/refs/tags/0.8.11 "$GKL_SHA256" "$archive"
elif ! pinned_digest_matches "$archive" "$GKL_SHA256"; then
    echo "Missing or invalid GKL 0.8.11 source ($archive). Rerun with --download to fetch public source from github.com/IntelLabs/GKL." >&2
    exit 1
fi
tar -xzf "$archive" -C "$WORK"
SRC="$WORK/GKL-0.8.11/src/main/native"
BUILD="$OUT/build"
RES="$BUILD/jar/com/intel/gkl/native"
mkdir -p "$RES" "$BUILD/objects"
JAVA_HOME=$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")
CUDA_HOME=$(dirname "$(dirname "$(readlink -f "$(command -v nvcc)")")")
test -f "$CUDA_HOME/EULA.txt" || { echo "Missing CUDA Toolkit EULA.txt" >&2; exit 1; }
# The JNI remains GKL's; only its float probability source changes. The original
# double-rescue, log10f/log10 and OpenMP CPU path stay in the same translation unit.
python3 - "$SRC/pairhmm/IntelPairHmm.cc" "$BUILD/IntelPairHmm.cc" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).read_text()
anchor = "        #ifdef _OPENMP\n              #pragma omp parallel for"
assert source.count(anchor) == 1
source = source.replace(
    anchor,
    "        std::vector<float> cudaResults;\n"
    "        const bool cuda = !g_use_double && wgse_cuda_pairhmm(testcases, cudaResults);\n"
    + anchor,
)
anchor = "g_use_double ? 0.0f : g_compute_full_prob_float(&testcases[i])"
assert source.count(anchor) == 1
source = source.replace(
    anchor,
    "g_use_double ? 0.0f : cuda ? cudaResults[i] : g_compute_full_prob_float(&testcases[i])",
)
source = '#include "cuda_pairhmm.h"\n' + source
pathlib.Path(sys.argv[2]).write_text(source)
PY
COMMON=(-std=c++17 -O3 -fPIC -ffp-contract=off -fno-fast-math -Dlinux
    "-I$HERE" "-I$SRC/common" "-I$SRC/pairhmm"
    "-I$JAVA_HOME/include" "-I$JAVA_HOME/include/linux")
for unit in "$BUILD/IntelPairHmm.cc" "$SRC/pairhmm/avx_impl.cc" \
    "$SRC/pairhmm/avx512_impl.cc" "$SRC/pairhmm/pairhmm_common.cc"; do
    isa=(-mavx)
    if [[ "$unit" == *avx512_impl.cc ]]; then isa+=(-mavx2 -mavx512f -mavx512dq -mavx512vl); fi
    g++ "${COMMON[@]}" "${isa[@]}" -fopenmp -c "$unit" \
        -o "$BUILD/objects/$(basename "$unit").o"
done
nvcc -std=c++17 -O3 "-I$HERE" "-I$SRC/common" "-I$SRC/pairhmm" \
    -Xcompiler=-fPIC,-mavx,-ffp-contract=off,-fno-fast-math --fmad=false --ftz=true \
    --prec-div=true --prec-sqrt=true -gencode 'arch=compute_75,code=[sm_75,compute_75]' \
    -gencode arch=compute_80,code=sm_80 -gencode arch=compute_86,code=sm_86 \
    -gencode arch=compute_89,code=sm_89 -gencode arch=compute_90,code=sm_90 \
    -c "$HERE/cuda_pairhmm.cu" -o "$BUILD/objects/cuda_pairhmm.o"
nvcc -shared -Xcompiler=-fopenmp -Xlinker=-z,noexecstack "$BUILD"/objects/*.o \
    -o "$RES/libgkl_pairhmm_omp.so"
cp "$RES/libgkl_pairhmm_omp.so" "$RES/libgkl_pairhmm.so"
mkdir -p "$BUILD/jar/META-INF/licenses/"{gkl,wgse,cuda}
printf '%s\n' '{"schema":1,"gatk":"4.6.2.0","gkl":"0.8.11","backend":"cuda"}' \
    > "$BUILD/jar/META-INF/wgse-pairhmm.json"
cp "$WORK/GKL-0.8.11/LICENSE" "$BUILD/jar/META-INF/licenses/gkl/"
cp "$HERE/LICENSE" "$BUILD/jar/META-INF/licenses/wgse/"
cp "$CUDA_HOME/EULA.txt" "$BUILD/jar/META-INF/licenses/cuda/"
jar --create --date=2024-01-01T00:00:00Z \
    --file "$OUT/gkl-native-wgse-linux-cuda.jar" -C "$BUILD/jar" .
sha256sum "$OUT/gkl-native-wgse-linux-cuda.jar"
