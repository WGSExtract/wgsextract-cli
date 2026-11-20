#!/usr/bin/env bash
# Build the existing NEON PairHMM port, without enabling Smith-Waterman.
# Usage: build-darwin-aarch64.sh WORK_DIR GATK_JAR OUT_DIR [--download]
set -euo pipefail
if [ "$#" -lt 3 ] || [ "$#" -gt 4 ]; then
    echo "Usage: $0 WORK_DIR GATK_JAR OUT_DIR [--download]" >&2
    exit 2
fi
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=fma_guard.sh disable=SC1091
. "$HERE/fma_guard.sh"
GATK_JAR=$(realpath "$2")
DOWNLOAD=${4:-}
case "$DOWNLOAD" in ""|--download) ;; *) echo "Unknown option: $DOWNLOAD" >&2; exit 2 ;; esac
test "$(uname -s)/$(uname -m)" = Darwin/arm64
for tool in clang++ javac jar python3 curl shasum patch tar brew; do
    command -v "$tool" >/dev/null || { echo "Missing build dependency: $tool" >&2; exit 1; }
done
JAVA_HOME=$(javac -J-XshowSettings:properties -version 2>&1 |
    sed -n 's/^[[:space:]]*java.home = //p')
test -f "$JAVA_HOME/include/jni.h"
"$JAVA_HOME/bin/java" -XshowSettings:properties -version 2>&1 |
    grep -Eq 'os.arch = (aarch64|arm64)'
OMP=$(brew --prefix libomp)
OBJDUMP="$(brew --prefix llvm)/bin/llvm-objdump"
test -f "$OMP/lib/libomp.dylib"
test -x "$OBJDUMP"
python3 - "$GATK_JAR" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    manifest = archive.read("META-INF/MANIFEST.MF").decode()
if "Implementation-Version: 4.6.2.0" not in manifest.splitlines():
    sys.exit("This sidecar is pinned to GATK 4.6.2.0 / GKL 0.8.11")
PY
mkdir -p "$1" "$3"
WORK=$(cd "$1" && pwd)
OUT=$(cd "$3" && pwd)
fetch() {
    local name=$1 sha=$2 url=$3
    if [ ! -f "$WORK/$name" ]; then
        if [ "$DOWNLOAD" != --download ]; then
            echo "Missing $name in $WORK. Rerun with --download to fetch public source from $url." >&2
            exit 1
        fi
        curl -fsSL "$url" -o "$WORK/$name.part"
        mv "$WORK/$name.part" "$WORK/$name"
    fi
    echo "$sha  $WORK/$name" | shasum -a 256 -c --status
}
fetch gkl-0.8.11.tar.gz 23f1aa7de874e79aedc35901397545e9e406266dfb1584f21300e440806a8950 \
    https://codeload.github.com/IntelLabs/GKL/tar.gz/refs/tags/0.8.11
fetch simde-0.8.2.tar.gz ed2a3268658f2f2a9b5367628a85ccd4cf9516460ed8604eed369653d49b25fb \
    https://codeload.github.com/simd-everywhere/simde/tar.gz/refs/tags/v0.8.2
SRC="$WORK/GKL-0.8.11"
BUILD="$OUT/build"
rm -rf "$SRC" "$WORK/simde-0.8.2" "$BUILD"
tar -xzf "$WORK/gkl-0.8.11.tar.gz" -C "$WORK"
tar -xzf "$WORK/simde-0.8.2.tar.gz" -C "$WORK"
patch -s -d "$SRC" -p1 < "$HERE/../../windows/recipes/gkl/patches/0001-windows-avx-native.patch"
patch -s -d "$SRC" -p1 < "$HERE/patches/0002-aarch64-neon-simde.patch"
patch -s -d "$SRC" -p1 < "$HERE/patches/0003-darwin-aarch64-loader.patch"
NATIVE="$SRC/src/main/native"
RES="$BUILD/jar/com/intel/gkl/native/darwin-aarch64"
mkdir -p "$RES" "$BUILD/classes"
COMMON=(-std=c++11 -O3 -march=armv8-a -ffp-contract=off -fno-fast-math -fPIC
    -DWGSE_GKL_AVX_ONLY -Wno-ignored-attributes
    "-I$WORK/simde-0.8.2" "-I$JAVA_HOME/include" "-I$JAVA_HOME/include/darwin"
    "-I$NATIVE/common")
fma_guard_selftest clang++ "$OBJDUMP" "$BUILD/fma-selftest" "${COMMON[@]}"
OPENMP=(-Xpreprocessor -fopenmp "-I$OMP/include" "-L$OMP/lib" -lomp)
clang++ "${COMMON[@]}" "${OPENMP[@]}" -dynamiclib "$NATIVE/utils/utils.cc" \
    -o "$RES/libgkl_utils.dylib"
for suffix in "" _omp; do
    flags=()
    if [ "$suffix" = _omp ]; then flags=("${OPENMP[@]}"); fi
    clang++ "${COMMON[@]}" ${flags[@]+"${flags[@]}"} -dynamiclib \
        "$NATIVE/pairhmm/IntelPairHmm.cc" "$NATIVE/pairhmm/avx_impl.cc" \
        "$NATIVE/pairhmm/pairhmm_common.cc" -o "$RES/libgkl_pairhmm$suffix.dylib"
done
for lib in "$RES"/*.dylib; do
    file "$lib" | grep -q 'arm64'
    reject_fma "$OBJDUMP" "$lib"
    otool -L "$lib"
    while IFS= read -r dependency; do
        case "$dependency" in
            /usr/lib/libc++.1.dylib|/usr/lib/libSystem.B.dylib|"$OMP/lib/libomp.dylib") ;;
            *) echo "Unexpected native dependency: $dependency" >&2; exit 1 ;;
        esac
    done < <(otool -L "$lib" | awk 'NR > 2 {print $1}')
done
JAVA_SRC="$SRC/src/main/java/com/intel/gkl"
"$JAVA_HOME/bin/javac" --release 17 -nowarn -cp "$GATK_JAR" -d "$BUILD/classes" \
    "$JAVA_SRC/IntelGKLUtils.java" "$JAVA_SRC/NativeLibraryLoader.java" \
    "$JAVA_SRC/pairhmm/IntelPairHmm.java" "$JAVA_SRC/pairhmm/IntelPairHmmOMP.java"
cp -r "$BUILD/classes/." "$BUILD/jar/"
mkdir -p "$BUILD/jar/META-INF/licenses/"{gkl,simde,wgse}
cp "$SRC/LICENSE" "$BUILD/jar/META-INF/licenses/gkl/"
cp "$WORK/simde-0.8.2/COPYING" "$BUILD/jar/META-INF/licenses/simde/"
cp "$HERE/../../../LICENSE" "$BUILD/jar/META-INF/licenses/wgse/"
python3 - "$BUILD/jar/META-INF/wgse-pairhmm.json" "$JAVA_HOME/bin/java" "$GATK_JAR" <<'PY'
import json
import pathlib
import sys

pathlib.Path(sys.argv[1]).write_text(json.dumps({
    "schema": 1, "gatk": "4.6.2.0", "gkl": "0.8.11", "backend": "neon",
    "java": sys.argv[2], "gatk_jar": sys.argv[3],
}) + "\n")
PY
"$JAVA_HOME/bin/jar" --create --date=2024-01-01T00:00:00Z \
    --file "$OUT/gkl-native-wgse-darwin-aarch64.jar" -C "$BUILD/jar" .
shasum -a 256 "$OUT/gkl-native-wgse-darwin-aarch64.jar"
