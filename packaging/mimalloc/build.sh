#!/usr/bin/env bash
# MI_OVERRIDE makes the library replace malloc/free in the process it is loaded
# into: by interposing on macOS, and as the default malloc zone, so calls from
# libSystem reach it too. That is what DYLD_INSERT_LIBRARIES needs.
set -euxo pipefail
# CMAKE_ARGS holds several arguments.
# shellcheck disable=SC2086
cmake -S . -B build -G Ninja ${CMAKE_ARGS} \
    -DCMAKE_BUILD_TYPE=Release \
    -DMI_OVERRIDE=ON \
    -DMI_BUILD_SHARED=ON \
    -DMI_BUILD_STATIC=OFF \
    -DMI_BUILD_OBJECT=OFF \
    -DMI_BUILD_TESTS=OFF
cmake --build build
dest="$PREFIX/lib/wgse-mimalloc"
mkdir -p "$dest" "$PREFIX/share/licenses/wgse-mimalloc"
# install copies the file behind libmimalloc.dylib's version symlinks.
install -m 755 build/libmimalloc.dylib "$dest/libmimalloc.dylib"
install_name_tool -id "@rpath/wgse-mimalloc/libmimalloc.dylib" "$dest/libmimalloc.dylib"
install -m 644 LICENSE "$PREFIX/share/licenses/wgse-mimalloc/LICENSE"
