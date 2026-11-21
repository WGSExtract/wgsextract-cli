# shellcheck shell=bash
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../env.sh
# The shared recipe is sourced through MSYS2's cygpath at build time.
# shellcheck disable=SC1091
source "$(cygpath -u "$RECIPE_DIR")/../env.sh"
# shellcheck source=../mimalloc.sh
source "$(cygpath -u "$RECIPE_DIR")/../mimalloc.sh"

# The release tarball bundles the matching htslib.
build_htslib htslib-* "$ALLOC_LDFLAGS"
./configure --with-htslib="$(echo htslib-*)" --without-curses \
    CFLAGS="$OPT" LDFLAGS="$STATIC_LDFLAGS" LIBS="$HTS_LIBS"
make -j"$JOBS" AR=gcc-ar samtools
mv samtools.exe .wgse/samtools-system.exe
make -j"$JOBS" AR=gcc-ar LDFLAGS="$STATIC_LDFLAGS $ALLOC_LDFLAGS" samtools
mv samtools.exe samtools-mimalloc.exe
mv .wgse/samtools-system.exe samtools.exe
bash "$COMPAT/../tests/samtools_allocator.sh" ./samtools.exe ./samtools-mimalloc.exe \
    out/samtools-allocator
install_exe samtools.exe samtools-mimalloc.exe
