#!/usr/bin/env bash
set -euxo pipefail
# wgse-minimap2 alone (wgse-bwa and wgse-bwa-mem3 have their own packages), with the CUDA
# backend, from the sources the recipe fetched.
# CMAKE_ARGS (conda-build's toolchain settings) is a list of arguments.
# shellcheck disable=SC2086
cmake -S native -B build -G Ninja ${CMAKE_ARGS} \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_TESTING=OFF \
    -DWGSE_BWA=OFF \
    -DWGSE_BWA_MEM3=OFF \
    -DWGSE_MINIMAP2=ON \
    -DWGSE_CUDA=ON \
    -DFETCHCONTENT_SOURCE_DIR_MINIMAP2="$SRC_DIR/minimap2" \
    -DFETCHCONTENT_FULLY_DISCONNECTED=ON
cmake --build build --parallel "${CPU_COUNT:-2}"
cmake --install build --prefix "$PREFIX"
