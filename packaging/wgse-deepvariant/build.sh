#!/usr/bin/env bash
set -euxo pipefail
# Rattler isolates the caller's environment. Opt in only when CI's shared
# action has prepared a cache; normal package builds remain cache-free.
cache_dir="$(cd "$RECIPE_DIR/../.." && pwd)/out/compiler-cache"
if [ -f "$cache_dir/ccache.conf" ]; then
    export CCACHE_DIR="$cache_dir"
    export CMAKE_C_COMPILER_LAUNCHER="$(command -v ccache)"
    export CMAKE_CXX_COMPILER_LAUNCHER="$(command -v ccache)"
fi
# CMAKE_ARGS (conda-build's toolchain settings) is a list of arguments.
# shellcheck disable=SC2086
cmake -S native/deepvariant -B build -G Ninja ${CMAKE_ARGS} \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="$PREFIX" \
    -DWGSE_BUILD_JOBS="${CPU_COUNT:-2}"
cmake --build build --parallel "${CPU_COUNT:-2}"
cmake --install build --prefix "$PREFIX"
