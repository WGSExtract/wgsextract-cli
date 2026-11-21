# shellcheck shell=bash
# Link-time redirection preserves ownership of pointers returned by UCRT DLLs.
MI_SOURCE="$(cygpath -u "$SRC_DIR")/mimalloc"
MI_BUILD="$(cygpath -u "$SRC_DIR")/.wgse/mimalloc"
cmake -S "$MI_SOURCE" -B "$MI_BUILD" -G Ninja \
    -DCMAKE_C_COMPILER=gcc -DCMAKE_C_FLAGS="$OPT" -DMI_OPT_ARCH=OFF \
    -DCMAKE_BUILD_TYPE=Release -DMI_BUILD_SHARED=OFF -DMI_BUILD_TESTS=OFF \
    -DMI_BUILD_OBJECT=OFF -DMI_BUILD_STATIC=ON -DMI_OVERRIDE=OFF
cmake --build "$MI_BUILD" -j"$JOBS"
MI_WRAPPER="$(cygpath -u "$SRC_DIR")/.wgse/mimalloc_wrap.o"
# shellcheck disable=SC2086
gcc $OPT -I"$MI_SOURCE/include" -c "$COMPAT/mimalloc_wrap.c" -o "$MI_WRAPPER"
ALLOC_LDFLAGS=""
for symbol in malloc calloc realloc free _msize \
    __imp_malloc __imp_calloc __imp_realloc __imp_free __imp__msize; do
    ALLOC_LDFLAGS="$ALLOC_LDFLAGS -Wl,--wrap=$symbol"
done
ALLOC_LDFLAGS="$ALLOC_LDFLAGS $MI_WRAPPER $MI_BUILD/libmimalloc.a -lpsapi -lbcrypt -ladvapi32"
# shellcheck disable=SC2086
gcc $OPT -UNDEBUG -I"$MI_SOURCE/include" "$COMPAT/../tests/mimalloc_wrap.c" \
    $STATIC_LDFLAGS $ALLOC_LDFLAGS -o .wgse/mimalloc-test.exe
./.wgse/mimalloc-test.exe
