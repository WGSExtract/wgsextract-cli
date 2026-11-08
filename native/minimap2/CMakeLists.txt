find_package(ZLIB REQUIRED)
find_package(Threads REQUIRED)

FetchContent_Declare(minimap2
    URL https://github.com/lh3/minimap2/archive/refs/tags/v2.31.tar.gz
    URL_HASH SHA256=bff334a0e4512644e2f3e29944aeec408f49450f4f74dc39fe89e5273869255b
    SOURCE_SUBDIR unused)
# Also declared by bwa-mem2; the first declaration wins, so keep them identical.
FetchContent_Declare(neon
    URL https://github.com/DLTcollab/sse2neon/archive/refs/tags/v1.8.0.tar.gz
    URL_HASH SHA256=e251746e3b761f3f0de1ad462b1efe53532341b6b0498d394765fceb85ce8a46
    SOURCE_SUBDIR unused)
FetchContent_MakeAvailable(minimap2 neon)

# Bump when the wgse-minimap2 package changes; it is versioned separately from minimap2.
set(WGSE_MINIMAP2_VERSION 0.2.0)
set(generated "${CMAKE_CURRENT_BINARY_DIR}/generated")
file(MAKE_DIRECTORY "${generated}/intrinsics")
foreach(header emmintrin.h smmintrin.h)
    file(WRITE "${generated}/intrinsics/${header}" "#include \"sse2neon.h\"\n")
endforeach()

function(minimap2_seam variable before after)
    string(FIND "${${variable}}" "${before}" position)
    if(position EQUAL -1)
        message(FATAL_ERROR "Unexpected upstream minimap2 source: ${before}")
    endif()
    string(REPLACE "${before}" "${after}" patched "${${variable}}")
    set(${variable} "${patched}" PARENT_SCOPE)
endfunction()
# main.c becomes a function of the wrapper, and @PG VN gains a -wgse-<version> suffix.
file(READ "${minimap2_SOURCE_DIR}/main.c" main_source)
minimap2_seam(main_source "int main(int argc, char *argv[])"
    "int wgse_minimap2_main(int argc, char *argv[])")
minimap2_seam(main_source "mm_write_sam_hdr(mi, rg, MM_VERSION, argc, argv)"
    "mm_write_sam_hdr(mi, rg, WGSE_PG_VERSION, argc, argv)")
minimap2_seam(main_source "mm_write_sam_hdr(0, rg, MM_VERSION, argc, argv)"
    "mm_write_sam_hdr(0, rg, WGSE_PG_VERSION, argc, argv)")
file(WRITE "${generated}/main.c" "${main_source}")

# Output-identical speedups (see wgse_index.inc and wgse_ksort.h).
# index.c: load .mmi hash tables in parallel and compute mid_occ and the index
# statistics in parallel, with the same results.
file(READ "${minimap2_SOURCE_DIR}/index.c" index_source)
minimap2_seam(index_source "int32_t mm_idx_cal_max_occ(const mm_idx_t *mi, float f)\n{"
    "static int32_t wgse_mm_idx_cal_max_occ(const mm_idx_t *mi, float f);
int32_t mm_idx_cal_max_occ(const mm_idx_t *mi, float f)
{
	return wgse_mm_idx_cal_max_occ(mi, f);
}

__attribute__((unused)) static int32_t upstream_mm_idx_cal_max_occ(const mm_idx_t *mi, float f)
{")
minimap2_seam(index_source "void mm_idx_stat(const mm_idx_t *mi)\n{"
    "static void wgse_mm_idx_stat(const mm_idx_t *mi);
void mm_idx_stat(const mm_idx_t *mi)
{
	wgse_mm_idx_stat(mi);
}

__attribute__((unused)) static void upstream_mm_idx_stat(const mm_idx_t *mi)
{")
minimap2_seam(index_source "mm_idx_t *mm_idx_reader_read(mm_idx_reader_t *r, int n_threads)\n{\n\tmm_idx_t *mi;\n"
    "static mm_idx_t *wgse_mm_idx_load(FILE *fp, int n_threads);
mm_idx_t *mm_idx_reader_read(mm_idx_reader_t *r, int n_threads)
{
	mm_idx_t *mi;
	wgse_n_threads = n_threads;
")
minimap2_seam(index_source "mi = mm_idx_load(r->fp.idx);" "mi = wgse_mm_idx_load(r->fp.idx, n_threads);")
minimap2_seam(index_source "static void *worker_pipeline(void *shared, int step, void *in)\n"
    "static int wgse_n_threads;
static void *wgse_worker_pipeline(void *shared, int step, void *in);
static void *worker_pipeline(void *shared, int step, void *in)\n")
minimap2_seam(index_source "\tkt_pipeline(n_threads < 3? n_threads : 3, worker_pipeline, &pl, 3);"
    "\twgse_n_threads = n_threads;
\tkt_pipeline(n_threads < 3? n_threads : 3, wgse_worker_pipeline, &pl, 3);")
file(WRITE "${generated}/index.c" "${index_source}\n#include \"wgse_index.inc\"\n")
# misc.c: anchor radix sorts skip passes that cannot change the order.
file(READ "${minimap2_SOURCE_DIR}/misc.c" misc_source)
minimap2_seam(misc_source "#include \"ksort.h\"\n" "#include \"wgse_ksort.h\"\n")
minimap2_seam(misc_source "KRADIX_SORT_INIT(128x, mm128_t, sort_key_128x, 8)"
    "WGSE_KRADIX_SORT_INIT(128x, mm128_t, sort_key_128x, 8)")
minimap2_seam(misc_source "KRADIX_SORT_INIT(64, uint64_t, sort_key_64, 8)"
    "WGSE_KRADIX_SORT_INIT(64, uint64_t, sort_key_64, 8)")
file(WRITE "${generated}/misc.c" "${misc_source}")
# seed.c: prefetch the index hash table slots a few minimizers ahead of their lookups.
file(READ "${minimap2_SOURCE_DIR}/seed.c" seed_source)
minimap2_seam(seed_source "#include \"ksort.h\"\n"
    "#include \"ksort.h\"\n
void wgse_mm_idx_prefetch_table(const mm_idx_t *mi, uint64_t minier);
void wgse_mm_idx_prefetch_slot(const mm_idx_t *mi, uint64_t minier);
")
minimap2_seam(seed_source "\t\tcr = mm_idx_get(mi, p->x>>8, &t);\n"
    "\t\tif (i + 16 < mv->n) wgse_mm_idx_prefetch_table(mi, mv->a[i + 16].x>>8);
\t\tif (i + 8 < mv->n) wgse_mm_idx_prefetch_slot(mi, mv->a[i + 8].x>>8);
\t\tcr = mm_idx_get(mi, p->x>>8, &t);
\t\tif (t > 1) __builtin_prefetch(cr);
")
file(WRITE "${generated}/seed.c" "${seed_source}")
# sketch.c: sketch part of a sequence, for index construction on several threads.
file(READ "${minimap2_SOURCE_DIR}/sketch.c" sketch_source)
file(WRITE "${generated}/sketch.c" "${sketch_source}\n#include \"wgse_sketch.inc\"\n")
# --metal (metal_ksw.h): align.c queues each read's gap fills for the GPU before
# aligning it and takes the results of identical fills; map.c runs several reads
# per CPU thread as fibers, so reads waiting for the GPU leave their thread to others.
file(READ "${minimap2_SOURCE_DIR}/align.c" align_source)
minimap2_seam(align_source "#include \"ksw2.h\"\n"
    "#include \"ksw2.h\"\n#include \"metal_ksw.h\"\n
static void wgse_ksw_prefetch(void *km, const mm_mapopt_t *opt, const mm_idx_t *mi, int qlen,
\tuint8_t *qseq0[2], int n_regs, const mm_reg1_t *regs, const mm128_t *a);
")
minimap2_seam(align_source "\t} else { // dual affine gap\n\t\tksw_extd2_sse("
    "\t} else if (!wgse_ksw_take(km, qlen, qseq, tlen, tseq, w, ksw_flag, ez)) { // dual affine gap\n\t\tksw_extd2_sse(")
minimap2_seam(align_source "\tmemset(&ez, 0, sizeof(ksw_extz_t));\n\tfor (i = 0; i < n_regs; ++i) {\n\t\tmm_reg1_t r2;"
    "\tmemset(&ez, 0, sizeof(ksw_extz_t));\n\twgse_ksw_prefetch(km, opt, mi, qlen, qseq0, n_regs, regs, a);
\tfor (i = 0; i < n_regs; ++i) {\n\t\tmm_reg1_t r2;")
file(WRITE "${generated}/align.c" "${align_source}\n#include \"wgse_align.inc\"\n")
file(READ "${minimap2_SOURCE_DIR}/map.c" map_source)
minimap2_seam(map_source "mm_tbuf_t *mm_tbuf_init(void)\n"
    "#include \"metal_ksw.h\"\nmm_tbuf_t *mm_tbuf_init(void)\n")
minimap2_seam(map_source "\tif (b->km) {\n\t\tkm_stat(b->km, &kmst);"
    "\tif (b->km && wgse_ksw_km_check()) {\n\t\tkm_stat(b->km, &kmst);")
minimap2_seam(map_source "\tmm_tbuf_t *b = s->buf[tid];\n\tassert(s->n_seg[i] <= MM_MAX_SEG);\n"
    "\tmm_tbuf_t *b = s->buf[tid];\n\tvoid *wgse_own_km = wgse_ksw_share_km(&b->km);\n\tassert(s->n_seg[i] <= MM_MAX_SEG);\n")
minimap2_seam(map_source "\tfor (j = 0; j < s->n_seg[i]; ++j) // flip the query strand"
    "\twgse_ksw_unshare_km(&b->km, wgse_own_km);\n\tfor (j = 0; j < s->n_seg[i]; ++j) // flip the query strand")
minimap2_seam(map_source "\t\t\ts->buf = (mm_tbuf_t**)calloc(p->n_threads, sizeof(mm_tbuf_t*));\n\t\t\tfor (i = 0; i < p->n_threads; ++i)"
    "\t\t\ts->buf = (mm_tbuf_t**)calloc(wgse_ksw_workers(p->n_threads), sizeof(mm_tbuf_t*));\n\t\t\tfor (i = 0; i < wgse_ksw_workers(p->n_threads); ++i)")
minimap2_seam(map_source "\t\telse kt_for(p->n_threads, worker_for, in, ((step_t*)in)->n_frag);"
    "\t\telse wgse_ksw_for(p->n_threads, worker_for, in, ((step_t*)in)->n_frag);")
minimap2_seam(map_source "\t\tfor (i = 0; i < p->n_threads; ++i) mm_tbuf_destroy(s->buf[i]);"
    "\t\tfor (i = 0; i < wgse_ksw_workers(p->n_threads); ++i) mm_tbuf_destroy(s->buf[i]);")
file(WRITE "${generated}/map.c" "${map_source}")

# Mirrors upstream's Makefile OBJS for an aarch64 (arm_neon) build, less the ksw2
# extension kernels, which are built below.
set(minimap2_names
    kthread kalloc bseq sdust options lchain hit jump
    format pe esterr splitidx ksw2_ll_sse)
list(TRANSFORM minimap2_names PREPEND "${minimap2_SOURCE_DIR}/" OUTPUT_VARIABLE minimap2_common_sources)
list(TRANSFORM minimap2_common_sources APPEND ".c")
set(minimap2_seamed main index misc seed sketch align map)
list(TRANSFORM minimap2_seamed PREPEND "${generated}/" OUTPUT_VARIABLE minimap2_sources)
list(TRANSFORM minimap2_sources APPEND ".c")
list(PREPEND minimap2_sources ${minimap2_common_sources})

set(WGSE_MINIMAP2_KSW2 sse41 CACHE STRING
    "ksw2 extension kernels: sse41 (sse2neon 1.8.0) or sse2 (upstream's NEON build)")
set_property(CACHE WGSE_MINIMAP2_KSW2 PROPERTY STRINGS sse41 sse2)
add_library(wgse_minimap2_ksw2 OBJECT
    "${minimap2_SOURCE_DIR}/ksw2_extz2_sse.c" "${minimap2_SOURCE_DIR}/ksw2_extd2_sse.c"
    "${minimap2_SOURCE_DIR}/ksw2_exts2_sse.c")
if(WGSE_MINIMAP2_KSW2 STREQUAL "sse41")
    # Upstream's x86 build picks either kernel at run time; they give identical results.
    # The SSE4.1 one maps its max and blend operations to single NEON instructions.
    target_compile_definitions(wgse_minimap2_ksw2 PRIVATE __SSE2__ __SSE4_1__ SSE2NEON_SUPPRESS_WARNINGS)
    target_include_directories(wgse_minimap2_ksw2 SYSTEM PRIVATE
        "${generated}/intrinsics" "${neon_SOURCE_DIR}")
elseif(WGSE_MINIMAP2_KSW2 STREQUAL "sse2")
    target_compile_definitions(wgse_minimap2_ksw2 PRIVATE KSW_SSE2_ONLY __SSE2__)
    target_include_directories(wgse_minimap2_ksw2 SYSTEM PRIVATE "${minimap2_SOURCE_DIR}/sse2neon")
else()
    message(FATAL_ERROR "WGSE_MINIMAP2_KSW2 must be sse41 or sse2")
endif()
target_include_directories(wgse_minimap2_ksw2 SYSTEM PRIVATE "${minimap2_SOURCE_DIR}")
target_compile_definitions(wgse_minimap2_ksw2 PRIVATE HAVE_KALLOC)
target_compile_options(wgse_minimap2_ksw2 PRIVATE -UNDEBUG -fsigned-char -Wno-unused-function)

function(minimap2_target_settings target)
    target_include_directories(${target} SYSTEM PUBLIC "${minimap2_SOURCE_DIR}")
    target_include_directories(${target} SYSTEM PRIVATE "${minimap2_SOURCE_DIR}/sse2neon")
    target_compile_definitions(${target} PRIVATE HAVE_KALLOC _FILE_OFFSET_BITS=64)
    # Upstream asserts stay on, as in its own and Bioconda's builds.
    target_compile_options(${target} PRIVATE -UNDEBUG -fsigned-char
        -Wno-unused-function -Wno-unused-but-set-variable -Wno-unused-variable)
    target_link_libraries(${target} PUBLIC ZLIB::ZLIB Threads::Threads m)
endfunction()

function(wgse_minimap2_executable name core)
    add_library(${core} STATIC ${minimap2_sources} metal_ksw.mm
        $<TARGET_OBJECTS:wgse_minimap2_ksw2>)
    minimap2_target_settings(${core})
    target_include_directories(${core} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    target_link_libraries(${core} PRIVATE wgse_metal_common)
    set_source_files_properties(metal_ksw.mm PROPERTIES COMPILE_OPTIONS
        "-fobjc-arc;-Wall;-Wextra;-Werror")
    target_compile_definitions(${core} PRIVATE
        WGSE_PG_VERSION="2.31-r1302-wgse-${WGSE_MINIMAP2_VERSION}")

    add_executable(${name} main.mm)
    target_compile_definitions(${name} PRIVATE WGSE_VERSION="${WGSE_MINIMAP2_VERSION}")
    if(BUILD_TESTING)
        # Hidden tuning and failure-injection flags for tests and benchmarks only.
        target_compile_definitions(${name} PRIVATE WGSE_METAL_BENCHMARK_TOOLS=1)
    endif()
    target_compile_options(${name} PRIVATE -fobjc-arc -Wall -Wextra -Werror)
    target_link_libraries(${name} PRIVATE ${core} wgse_metal_common)
endfunction()

wgse_minimap2_executable(wgse-minimap2 wgse_minimap2_core)

if(BUILD_TESTING)
    add_test(NAME minimap2_capabilities COMMAND wgse-minimap2 capabilities --json)
    set_tests_properties(minimap2_capabilities PROPERTIES
        PASS_REGULAR_EXPRESSION "\"engine\":\"minimap2\".*\"metal_hardware_supported\":")
    add_test(NAME minimap2_smoke COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tests/smoke.sh"
        $<TARGET_FILE:wgse-minimap2> "${CMAKE_CURRENT_BINARY_DIR}/smoke")
    # Index construction splits sequences into blocks and segments of megabases, longer
    # than the test genomes: build a copy with tiny ones and require identical indexes.
    wgse_minimap2_executable(wgse-minimap2-segments wgse_minimap2_core_segments)
    target_compile_definitions(wgse_minimap2_core_segments PRIVATE
        WGSE_PACK_BLOCK=64 WGSE_SKETCH_SEGMENT=20000)
    add_test(NAME minimap2_segments COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tests/segments.sh"
        $<TARGET_FILE:wgse-minimap2> $<TARGET_FILE:wgse-minimap2-segments>
        "${CMAKE_CURRENT_BINARY_DIR}/segments")
    # Unmodified upstream sources, to check that the seams change no index or alignment.
    add_executable(minimap2-upstream
        ${minimap2_common_sources} "${minimap2_SOURCE_DIR}/main.c" "${minimap2_SOURCE_DIR}/index.c"
        "${minimap2_SOURCE_DIR}/misc.c" "${minimap2_SOURCE_DIR}/seed.c"
        "${minimap2_SOURCE_DIR}/sketch.c" "${minimap2_SOURCE_DIR}/align.c"
        "${minimap2_SOURCE_DIR}/map.c" $<TARGET_OBJECTS:wgse_minimap2_ksw2>)
    minimap2_target_settings(minimap2-upstream)
    add_test(NAME minimap2_upstream_parity COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tests/upstream.sh"
        $<TARGET_FILE:wgse-minimap2> $<TARGET_FILE:minimap2-upstream>
        "${CMAKE_CURRENT_BINARY_DIR}/upstream")
endif()

install(TARGETS wgse-minimap2 RUNTIME DESTINATION bin)
install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/../../LICENSE" DESTINATION share/licenses/wgse-minimap2
    RENAME WGSE-LICENSE)
install(FILES "${minimap2_SOURCE_DIR}/LICENSE.txt" DESTINATION share/licenses/wgse-minimap2
    RENAME MINIMAP2-LICENSE)
install(FILES "${neon_SOURCE_DIR}/LICENSE" DESTINATION share/licenses/wgse-minimap2
    RENAME sse2neon-LICENSE)
# wgse_ksort.h adapts ksort.h, whose MIT notice differs from minimap2's.
file(READ "${minimap2_SOURCE_DIR}/ksort.h" ksort_source)
string(REGEX MATCH "/\\* The MIT License[^*]*\\*/" ksort_license "${ksort_source}")
if(NOT ksort_license)
    message(FATAL_ERROR "No MIT notice in upstream ksort.h")
endif()
string(REGEX REPLACE "^/\\* | *\\*/$" "" ksort_license "${ksort_license}")
string(REGEX REPLACE "\n   " "\n" ksort_license "${ksort_license}")
file(WRITE "${generated}/KSORT-LICENSE" "${ksort_license}\n")
install(FILES "${generated}/KSORT-LICENSE" DESTINATION share/licenses/wgse-minimap2)
