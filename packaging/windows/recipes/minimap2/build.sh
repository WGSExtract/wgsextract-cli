source "$(cygpath -u "$RECIPE_DIR")/../env.sh"
source "$(cygpath -u "$RECIPE_DIR")/../allocator_measurement.sh"

# minimap2 has its own WIN32 code (gettimeofday, timing), so it doesn't take
# the compat header; it links the compat object for binary I/O. The SSE4.1
# kernels are picked at run time (ksw2_dispatch).
# Its Makefile links with LIBS only.
make -j"$JOBS" CC=gcc CFLAGS="$OPT -g0 -Wall" \
    LIBS="-lm -lz -lpthread $COMPAT_LIBS $STATIC_LDFLAGS" minimap2
install_exe minimap2.exe
cp minimap2.exe minimap2-system.exe
rm minimap2.exe
make -j"$JOBS" CC=gcc CFLAGS="$OPT -g0 -Wall" \
    LIBS="-lm -lz -lpthread $COMPAT_LIBS $STATIC_LDFLAGS $ALLOC_LDFLAGS" minimap2
install_exe minimap2.exe minimap2-system.exe
