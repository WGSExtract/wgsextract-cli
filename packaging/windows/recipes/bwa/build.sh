# shellcheck shell=bash
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../env.sh
# The shared recipe is sourced through MSYS2's cygpath at build time.
# shellcheck disable=SC1091
source "$(cygpath -u "$RECIPE_DIR")/../env.sh"

# Share the native fork's exact extension kernel, retaining upstream for scores
# outside its 16-bit range. PE/COFF uses SSE2, without ELF's IFUNC AVX2 resolver.
KERNELS="$(cygpath -u "$SRC_DIR")/wgse-cpu"
mkdir -p .wgse
cp "$KERNELS/extend_neon.c.in" "$KERNELS/wgse_neon.h" .wgse/
grep -q '^int ksw_extend2(' ksw.c
sed -i 's/^int ksw_extend2(/int wgse_ksw_extend2_upstream(/' ksw.c
printf '\n#define wgse_fast_cpu() 1\n#include ".wgse/extend_neon.c.in"\n' >> ksw.c

make -j"$JOBS" CC=gcc \
    CFLAGS="$OPT -g0 -Wall -Wno-unused-function" \
    INCLUDES="$COMPAT_CPPFLAGS" \
    LDFLAGS="$STATIC_LDFLAGS" \
    LIBS="-lm -lz -lpthread $COMPAT_LIBS"
install_exe bwa.exe

# Exhaustively compare the batched lookup with the scalar implementation on
# a synthetic index, including different suffix-array sampling intervals.
# Compiler and linker flags are argument lists provided by the shared recipe.
# shellcheck disable=SC2086
gcc $OPT $COMPAT_CPPFLAGS -I. \
    "$(cygpath -u "$RECIPE_DIR")/../../tests/bwa_sa.c" libbwa.a \
    -o .wgse/bwa-sa-test.exe $STATIC_LDFLAGS -lm -lz -lpthread $COMPAT_LIBS
mkdir -p out
./.wgse/bwa-sa-test.exe --write-ref out/sa-test.fa
./bwa.exe index out/sa-test.fa
./.wgse/bwa-sa-test.exe out/sa-test.fa.bwt out/sa-test.fa.sa

# Every result field must match upstream, including scoring/gap overflow fallbacks.
# shellcheck disable=SC2086
gcc $OPT $COMPAT_CPPFLAGS -I. "$KERNELS/tests/extend_neon_check.c" libbwa.a \
    -o .wgse/extension-parity.exe $STATIC_LDFLAGS -lm -lz -lpthread $COMPAT_LIBS
./.wgse/extension-parity.exe 200000
