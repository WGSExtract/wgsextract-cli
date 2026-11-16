# shellcheck shell=bash
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../env.sh
# The shared recipe is sourced through MSYS2's cygpath at build time.
# shellcheck disable=SC1091
source "$(cygpath -u "$RECIPE_DIR")/../env.sh"

# Link-time redirection covers this executable and its static libraries, not
# the UCRT DLL. The bridge preserves ownership of pointers returned by that DLL.
mkdir -p .wgse
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

# The release tarball bundles the matching htslib. Plugins need dlopen of
# DLLs built against a shared libhts, so this static build leaves them out.
build_htslib htslib-* "$ALLOC_LDFLAGS"
./configure --with-htslib="$(echo htslib-*)" --disable-bcftools-plugins \
    CFLAGS="$OPT" LDFLAGS="$STATIC_LDFLAGS" LIBS="$HTS_LIBS"
make -j"$JOBS" AR=gcc-ar bcftools
cp bcftools.exe .wgse/bcftools-original.exe

# Check the actual patched grouping function against the release's original,
# including multisample fallback and sample selection, before packaging it.
mkdir -p .wgse
/usr/bin/patch --batch --reverse -p1 --output=.wgse/original-mpileup.c \
    < "$(cygpath -u "$RECIPE_DIR")/patches/0004-bulk-copy-single-sample.patch"
for version in original candidate; do
    source_file=mpileup.c
    if [[ $version == original ]]; then source_file=.wgse/original-mpileup.c; fi
    awk '/^static void group_smpl\(/ { copy=1 } copy { print } copy && /^}/ { exit }' \
        "$source_file" > ".wgse/group_$version.inc"
    test -s ".wgse/group_$version.inc"
done
cat > .wgse/group.mk <<'MAKEFILE'
include $(wildcard htslib-*/htslib_static.mk)
.PHONY: group-test
group-test:
	$(CC) $(CFLAGS) -I. -I.wgse -I"$(HTSDIR)" "$(GROUP_TEST)" bam_sample.c "$(HTSDIR)/libhts.a" $(HTSLIB_static_LDFLAGS) $(HTSLIB_static_LIBS) $(GROUP_LIBS) -o .wgse/group-copy.exe
	./.wgse/group-copy.exe
MAKEFILE
make -f .wgse/group.mk group-test CC=gcc CFLAGS="$OPT $STATIC_LDFLAGS" \
    HTSDIR="$(echo htslib-*)" GROUP_LIBS="$HTS_LIBS $ALLOC_LDFLAGS" \
    GROUP_TEST="$(cygpath -u "$RECIPE_DIR")/../../tests/group_copy.c"

rm bcftools.exe
make -j"$JOBS" AR=gcc-ar LDFLAGS="$STATIC_LDFLAGS $ALLOC_LDFLAGS" bcftools
bash "$COMPAT/../tests/calling_alloc.sh" .wgse/bcftools-original.exe ./bcftools.exe \
    out/calling-allocator
install_exe bcftools.exe
