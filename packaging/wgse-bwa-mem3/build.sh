#!/usr/bin/env bash
set -euxo pipefail
# wgse-bwa-mem3 alone (wgse-bwa has its own package), with the CUDA backend, from the
# sources the recipe fetched.
# CMAKE_ARGS (conda-build's toolchain settings) is a list of arguments.
# shellcheck disable=SC2086
cmake -S native -B build -G Ninja ${CMAKE_ARGS} \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_TESTING=OFF \
    -DWGSE_BWA=OFF \
    -DWGSE_BWA_MEM3=ON \
    -DWGSE_MINIMAP2=OFF \
    -DWGSE_CUDA=ON \
    -DFETCHCONTENT_SOURCE_DIR_MEM3="$SRC_DIR/mem3" \
    -DFETCHCONTENT_SOURCE_DIR_MEM3_LIBSAIS="$SRC_DIR/libsais" \
    -DFETCHCONTENT_SOURCE_DIR_MEM3_MIMALLOC="$SRC_DIR/mimalloc" \
    -DFETCHCONTENT_SOURCE_DIR_MEM3_ZLIBNG="$SRC_DIR/zlib-ng" \
    -DFETCHCONTENT_SOURCE_DIR_MEM3_LIBDEFLATE="$SRC_DIR/libdeflate" \
    -DWGSE_MEM3_HTSLIB_URL="$SRC_DIR/htslib-1.21.tar.bz2" \
    -DFETCHCONTENT_FULLY_DISCONNECTED=ON
cmake --build build --parallel "${CPU_COUNT:-2}"
cmake --install build --prefix "$PREFIX"
