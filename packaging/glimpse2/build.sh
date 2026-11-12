# shellcheck shell=bash
# shellcheck disable=SC1091
source "$(cygpath -u "$RECIPE_DIR")/../windows/recipes/env.sh"

build_htslib htslib-*
HTS=$(cd htslib-* && pwd)

# Windows' GCC doesn't align the stack for AVX (phase is built with -mavx2,
# as upstream builds it): the assembler uses unaligned vector moves instead.
# The compat header gives 64-bit file offsets (Win64's long is 32-bit).
CXXFLAGS="$OPT -std=c++17 -Wno-ignored-attributes -Wa,-muse-unaligned-vector-move $COMPAT_CPPFLAGS"
CXXFLAGS="$CXXFLAGS -D__COMMIT_ID__=\\\"88915f7\\\" -D__COMMIT_DATE__=\\\"2023-04-24\\\""
LIBS="-ldeflate -lbz2 -llzma -lzstd -lz -lpthread $HTS_LIBS"
for tool in chunk concordance ligate phase split_reference; do
    extra=""
    [ "$tool" = phase ] && extra="-mavx2 -mfma"
    make -C "$tool" -j"$JOBS" "bin/GLIMPSE2_$tool" \
        CXX=g++ CXXFLAG="$CXXFLAGS $extra" LDFLAG="$OPT $STATIC_LDFLAGS" \
        HTSLIB_INC="$HTS" HTSLIB_LIB="$HTS/libhts.a" BOOST_INC=/ucrt64/include \
        BOOST_LIB_IO=/ucrt64/lib/libboost_iostreams-mt.a \
        BOOST_LIB_PO=/ucrt64/lib/libboost_program_options-mt.a \
        BOOST_LIB_SE=/ucrt64/lib/libboost_serialization-mt.a \
        DYN_LIBS="$LIBS"
    install_exe "$tool/bin/GLIMPSE2_$tool.exe"
done
