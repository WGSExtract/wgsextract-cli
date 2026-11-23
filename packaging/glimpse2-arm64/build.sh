# shellcheck shell=bash
set -euo pipefail

# Replace upstream's x86 switches and git autodetection with the pinned tag
# metadata. SIMDe translates the explicit AVX2/FMA intrinsics to ARM NEON.
flags="$CXXFLAGS -O3 -Wno-ignored-attributes -DSIMDE_ENABLE_NATIVE_ALIASES"
flags="$flags -D__COMMIT_ID__=\\\"88915f7\\\" -D__COMMIT_DATE__=\\\"2023-04-24\\\""
mkdir -p "$PREFIX/bin"
for tool in chunk concordance ligate phase split_reference; do
    make -C "$tool" -j"$CPU_COUNT" "bin/GLIMPSE2_$tool" \
        CXX="$CXX -std=c++17" CXXFLAG="$flags" LDFLAG="$LDFLAGS" \
        HTSLIB_INC="$PREFIX/include" HTSLIB_LIB="-lhts" \
        BOOST_INC="$PREFIX/include" BOOST_LIB_IO="-lboost_iostreams" \
        BOOST_LIB_PO="-lboost_program_options" BOOST_LIB_SE="-lboost_serialization" \
        DYN_LIBS="-pthread"
    install -m 755 "$tool/bin/GLIMPSE2_$tool" "$PREFIX/bin/"
done
