# shellcheck shell=bash
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../env.sh
# shellcheck disable=SC1091
source "$(cygpath -u "$RECIPE_DIR")/../env.sh"

# Test the compat reader with the same CRT/flags used by samblaster.
# Compiler/linker flags below are argument lists from env.sh.
# shellcheck disable=SC2086
gcc $OPT $COMPAT_CPPFLAGS "$COMPAT/../tests/getline.c" "$COMPAT_OBJ" \
    -lpsapi $STATIC_LDFLAGS -o test-getline.exe
./test-getline.exe

# Keep an upstream-reader oracle with the same compiler, CRT and options.
mkdir -p .wgse
/usr/bin/patch --batch --reverse -p1 --output=.wgse/original-samblaster.cpp \
    < "$(cygpath -u "$RECIPE_DIR")/patches/0001-buffered-sam-reader.patch"
# shellcheck disable=SC2086
g++ $OPT -Wall -D BUILDNUM=26 $COMPAT_CPPFLAGS -c sbhash.cpp -o sbhash.o
for version in original candidate; do
    source_file=samblaster.cpp
    binary=samblaster.exe
    if [[ $version == original ]]; then
        source_file=.wgse/original-samblaster.cpp
        binary=.wgse/samblaster-original.exe
    fi
    # shellcheck disable=SC2086
    g++ $OPT -Wall -D BUILDNUM=26 $COMPAT_CPPFLAGS -I. -I"$COMPAT" \
        -c "$source_file" -o ".wgse/$version.o"
    # shellcheck disable=SC2086
    g++ $STATIC_LDFLAGS -o "$binary" ".wgse/$version.o" sbhash.o $COMPAT_LIBS
done
bash "$COMPAT/../tests/samblaster_reader.sh" .wgse/samblaster-original.exe \
    ./samblaster.exe out/reader-test
install_exe samblaster.exe
