#!/usr/bin/env bash
set -euxo pipefail
# CMAKE_ARGS (conda-build's toolchain settings) is a list of arguments.
# shellcheck disable=SC2086
cmake -S native -B build -G Ninja ${CMAKE_ARGS} \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_TESTING=OFF \
    -DWGSE_CUDA=ON \
    -DFETCHCONTENT_SOURCE_DIR_BWA="$SRC_DIR/bwa" \
    -DFETCHCONTENT_FULLY_DISCONNECTED=ON
cmake --build build --parallel "${CPU_COUNT:-2}"
cmake --install build --prefix "$PREFIX"
