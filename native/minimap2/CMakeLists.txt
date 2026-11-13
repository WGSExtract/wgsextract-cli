find_package(ZLIB REQUIRED)
find_package(Threads REQUIRED)

FetchContent_Declare(minimap2
    URL https://github.com/lh3/minimap2/archive/refs/tags/v2.31.tar.gz
    URL_HASH SHA256=bff334a0e4512644e2f3e29944aeec408f49450f4f74dc39fe89e5273869255b
    SOURCE_SUBDIR unused)
FetchContent_MakeAvailable(minimap2)
if(APPLE)
    # Also declared by bwa-mem2; the first declaration wins, so keep them identical.
    FetchContent_Declare(neon
        URL https://github.com/DLTcollab/sse2neon/archive/refs/tags/v1.8.0.tar.gz
        URL_HASH SHA256=e251746e3b761f3f0de1ad462b1efe53532341b6b0498d394765fceb85ce8a46
        SOURCE_SUBDIR unused)
    FetchContent_MakeAvailable(neon)
endif()

# Bump when the wgse-minimap2 package changes; it is versioned separately from minimap2.
set(WGSE_MINIMAP2_VERSION 0.4.0)
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
# main.c becomes a function of the wrapper, @PG VN gains a -wgse-<version> suffix,
# and @PG CL records the wrapper's command line, including --metal.
file(READ "${minimap2_SOURCE_DIR}/main.c" main_source)
minimap2_seam(main_source "int main(int argc, char *argv[])"
    "extern int wgse_pg_argc;\nextern char **wgse_pg_argv;\nint wgse_minimap2_main(int argc, char *argv[])")
minimap2_seam(main_source "mm_write_sam_hdr(mi, rg, MM_VERSION, argc, argv)"
    "mm_write_sam_hdr(mi, rg, WGSE_PG_VERSION, wgse_pg_argc, wgse_pg_argv)")
minimap2_seam(main_source "mm_write_sam_hdr(0, rg, MM_VERSION, argc, argv)"
    "mm_write_sam_hdr(0, rg, WGSE_PG_VERSION, wgse_pg_argc, wgse_pg_argv)")
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
# misc.c: anchor radix sorts skip passes that cannot change the order, and
# radix_sort_128x replays the in-place sort's passes out of place (wgse_sort128.inc).
file(READ "${minimap2_SOURCE_DIR}/misc.c" misc_source)
minimap2_seam(misc_source "#include \"ksort.h\"\n" "#include \"wgse_ksort.h\"\n")
minimap2_seam(misc_source "KRADIX_SORT_INIT(128x, mm128_t, sort_key_128x, 8)"
    "WGSE_KRADIX_SORT_BASE(128x, mm128_t, sort_key_128x)\n#include \"wgse_sort128.inc\"")
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
# --metal (ksw_gpu.h): align.c queues each read's gap fills for the GPU before
# aligning it and takes the results of identical fills; map.c runs several reads
# per CPU thread as fibers, so reads waiting for the GPU leave their thread to others.
file(READ "${minimap2_SOURCE_DIR}/align.c" align_source)
minimap2_seam(align_source "#include \"ksw2.h\"\n"
    "#include \"ksw2.h\"\n#include \"ksw_gpu.h\"\n
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
    "#include \"ksw_gpu.h\"\nmm_tbuf_t *mm_tbuf_init(void)\n")
minimap2_seam(map_source "\tif (b->km) {\n\t\tkm_stat(b->km, &kmst);"
    "\tif (b->km && wgse_ksw_km_check(b->km, opt->cap_kalloc)) {\n\t\tkm_stat(b->km, &kmst);")
minimap2_seam(map_source "\t\t\tkm_destroy(b->km);\n\t\t\tb->km = km_init();\n"
    "\t\t\tkm_destroy(b->km);\n\t\t\tb->km = km_init();\n\t\t\twgse_ksw_km_reset(b->km);\n")
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

# lchain.c (chain_gpu.h): mg_lchain_dp's score fill moves verbatim into
# wgse_lchain_dp_fill, which mg_lchain_dp calls unless the GPU computed the same scores.
file(READ "${minimap2_SOURCE_DIR}/lchain.c" lchain_source)
set(fill_begin "\t// fill the score and backtrack arrays\n\tfor (i = 0, max_ii = -1; i < n; ++i) {")
set(fill_end "\n\tu = mg_chain_backtrack(km, n, f, p, v, t, min_cnt, min_sc, max_drop, &n_u, &n_v);")
string(FIND "${lchain_source}" "${fill_begin}" fill_at)
string(FIND "${lchain_source}" "${fill_end}" fill_end_at)
string(FIND "${lchain_source}" "mm128_t *mg_lchain_rmq(" rmq_at)
if(fill_at EQUAL -1 OR fill_end_at LESS fill_at OR rmq_at LESS fill_end_at)
    message(FATAL_ERROR "Unexpected upstream minimap2 source: mg_lchain_dp's score fill")
endif()
math(EXPR fill_length "${fill_end_at} - ${fill_at}")
string(SUBSTRING "${lchain_source}" ${fill_at} ${fill_length} fill_loop)
string(SUBSTRING "${lchain_source}" 0 ${fill_at} lchain_head)
string(SUBSTRING "${lchain_source}" ${fill_end_at} -1 lchain_tail)
minimap2_seam(lchain_head "#include \"krmq.h\"\n" "#include \"krmq.h\"\n#include \"chain_gpu.h\"\n")
file(WRITE "${generated}/lchain.c" "${lchain_head}\t// fill the score and backtrack arrays
\tif (!wgse_chain_dp_gpu(max_dist_x, max_dist_y, bw, max_skip, max_iter, chn_pen_gap, chn_pen_skip,
\t\t\tis_cdna, n_seg, n, a, f, p, v))
\t\twgse_lchain_dp_fill(max_dist_x, max_dist_y, bw, max_skip, max_iter, chn_pen_gap, chn_pen_skip,
\t\t\tis_cdna, n_seg, n, a, f, p, v, t);${lchain_tail}
void wgse_lchain_dp_fill(int max_dist_x, int max_dist_y, int bw, int max_skip, int max_iter,
\tfloat chn_pen_gap, float chn_pen_skip, int is_cdna, int n_seg, int64_t n, const mm128_t *a,
\tint32_t *f, int64_t *p, int32_t *v, int32_t *t)
{
\tint32_t mmax_f = 0;
\tint64_t i, j, max_ii, st = 0;

${fill_loop}
}
")

# Mirrors upstream's Makefile OBJS (the same on aarch64, arm_neon, and x86-64), less the
# ksw2 extension kernels, which are built below.
set(minimap2_names
    kthread kalloc bseq sdust options hit jump
    format pe esterr splitidx ksw2_ll_sse)
list(TRANSFORM minimap2_names PREPEND "${minimap2_SOURCE_DIR}/" OUTPUT_VARIABLE minimap2_common_sources)
list(TRANSFORM minimap2_common_sources APPEND ".c")
set(minimap2_seamed main index misc seed sketch align map lchain)
list(TRANSFORM minimap2_seamed PREPEND "${generated}/" OUTPUT_VARIABLE minimap2_sources)
list(TRANSFORM minimap2_sources APPEND ".c")
list(PREPEND minimap2_sources ${minimap2_common_sources})

if(APPLE)
    set(WGSE_MINIMAP2_KSW2 neon CACHE STRING
        "ksw2 extension kernels: neon (ksw_extd2 written for NEON, the others sse41), sse41 (sse2neon 1.8.0) or sse2 (upstream's NEON build)")
    set_property(CACHE WGSE_MINIMAP2_KSW2 PROPERTY STRINGS neon sse41 sse2)
else()
    # Upstream's x86-64 build: SSE4.1 and SSE2 kernels, picked at run time (ksw2_dispatch.c).
    set(WGSE_MINIMAP2_KSW2 x86)
endif()
# The ksw2 extension kernels for mode as object libraries named after name; their objects
# are in ${name}_objects.
function(minimap2_ksw2_library name mode)
    set(kernels ksw2_extz2_sse ksw2_extd2_sse ksw2_exts2_sse)
    list(TRANSFORM kernels PREPEND "${minimap2_SOURCE_DIR}/")
    list(TRANSFORM kernels APPEND ".c")
    if(mode STREQUAL "x86")
        # One object per kernel and instruction set, as upstream's Makefile compiles them.
        set(objects)
        foreach(isa sse41 sse2)
            add_library(${name}_${isa} OBJECT ${kernels})
            target_compile_definitions(${name}_${isa} PRIVATE KSW_CPU_DISPATCH HAVE_KALLOC)
            if(isa STREQUAL "sse41")
                target_compile_options(${name}_${isa} PRIVATE -msse4.1)
            else()
                target_compile_definitions(${name}_${isa} PRIVATE KSW_SSE2_ONLY)
                target_compile_options(${name}_${isa} PRIVATE -msse2 -mno-sse4.1)
            endif()
            target_include_directories(${name}_${isa} SYSTEM PRIVATE "${minimap2_SOURCE_DIR}")
            target_compile_options(${name}_${isa} PRIVATE -UNDEBUG -Wno-unused-function)
            list(APPEND objects $<TARGET_OBJECTS:${name}_${isa}>)
        endforeach()
        add_library(${name} OBJECT "${minimap2_SOURCE_DIR}/ksw2_dispatch.c")
        target_compile_definitions(${name} PRIVATE KSW_CPU_DISPATCH)
        target_include_directories(${name} SYSTEM PRIVATE "${minimap2_SOURCE_DIR}")
        set(${name}_objects $<TARGET_OBJECTS:${name}> ${objects} PARENT_SCOPE)
        return()
    endif()
    set(extd2 "${minimap2_SOURCE_DIR}/ksw2_extd2_sse.c")
    if(mode STREQUAL "neon")
        set(extd2 "${CMAKE_CURRENT_SOURCE_DIR}/wgse_ksw2_extd2.c")
        set(mode sse41)
    endif()
    add_library(${name} OBJECT
        "${minimap2_SOURCE_DIR}/ksw2_extz2_sse.c" "${extd2}" "${minimap2_SOURCE_DIR}/ksw2_exts2_sse.c")
    if(mode STREQUAL "sse41")
        # Upstream's x86 build picks either kernel at run time; they give identical results.
        # The SSE4.1 one maps its max and blend operations to single NEON instructions.
        target_compile_definitions(${name} PRIVATE __SSE2__ __SSE4_1__ SSE2NEON_SUPPRESS_WARNINGS)
        target_include_directories(${name} SYSTEM PRIVATE
            "${generated}/intrinsics" "${neon_SOURCE_DIR}")
    elseif(mode STREQUAL "sse2")
        target_compile_definitions(${name} PRIVATE KSW_SSE2_ONLY __SSE2__)
        target_include_directories(${name} SYSTEM PRIVATE "${minimap2_SOURCE_DIR}/sse2neon")
    else()
        message(FATAL_ERROR "WGSE_MINIMAP2_KSW2 must be neon, sse41 or sse2")
    endif()
    target_include_directories(${name} SYSTEM PRIVATE "${minimap2_SOURCE_DIR}")
    target_compile_definitions(${name} PRIVATE HAVE_KALLOC)
    target_compile_options(${name} PRIVATE -UNDEBUG -fsigned-char -Wno-unused-function)
    set(${name}_objects $<TARGET_OBJECTS:${name}> PARENT_SCOPE)
endfunction()
minimap2_ksw2_library(wgse_minimap2_ksw2 ${WGSE_MINIMAP2_KSW2})

if(WGSE_CUDA)
    # The CUDA devices: ksw_cuda.cu (ksw_fill.cuh's kernels, ported from metal_ksw.mm's)
    # and chain_cuda.cu (chain_fill.cuh's).
    add_library(wgse_minimap2_cuda STATIC ksw_cuda.cu chain_cuda.cu)
    target_include_directories(wgse_minimap2_cuda PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}"
        "${CMAKE_CURRENT_SOURCE_DIR}/../gpu-common")
    target_include_directories(wgse_minimap2_cuda SYSTEM PRIVATE "${minimap2_SOURCE_DIR}")
    target_compile_options(wgse_minimap2_cuda PRIVATE
        $<$<COMPILE_LANGUAGE:CUDA>:--Werror=all-warnings -Xcompiler=-Wall,-Wextra>)
    target_link_libraries(wgse_minimap2_cuda PUBLIC CUDA::cudart_static)
endif()

function(minimap2_target_settings target)
    target_include_directories(${target} SYSTEM PUBLIC "${minimap2_SOURCE_DIR}")
    if(APPLE)
        # Its emmintrin.h maps the SSE2 intrinsics of ksw2_ll_sse.c to NEON.
        target_include_directories(${target} SYSTEM PRIVATE "${minimap2_SOURCE_DIR}/sse2neon")
    endif()
    target_compile_definitions(${target} PRIVATE HAVE_KALLOC _FILE_OFFSET_BITS=64)
    # Upstream asserts stay on, as in its own and Bioconda's builds.
    target_compile_options(${target} PRIVATE -UNDEBUG -fsigned-char
        -Wno-unused-function -Wno-unused-but-set-variable -Wno-unused-variable)
    target_link_libraries(${target} PUBLIC ZLIB::ZLIB Threads::Threads m)
endfunction()

function(wgse_minimap2_executable name core)
    # The gap-fill offload (ksw_gpu.h): ksw_host.cpp's engine on Metal (macOS) or CUDA
    # (Linux with -DWGSE_CUDA=ON), else every fill on the CPU.
    if(APPLE)
        set(gpu_sources ksw_host.cpp chain_host.cpp metal_ksw.mm)
    elseif(WGSE_CUDA)
        set(gpu_sources ksw_host.cpp chain_host.cpp)
    else()
        set(gpu_sources ksw_gpu_none.cpp)
    endif()
    add_library(${core} STATIC ${minimap2_sources} ${gpu_sources}
        ${wgse_minimap2_ksw2_objects})
    minimap2_target_settings(${core})
    target_include_directories(${core} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    set_source_files_properties(${gpu_sources} PROPERTIES COMPILE_OPTIONS "-Wall;-Wextra;-Werror")
    if(WGSE_CUDA)
        target_link_libraries(${core} PRIVATE wgse_minimap2_cuda)
    endif()
    if(APPLE)
        target_link_libraries(${core} PRIVATE wgse_metal_common)
        set_source_files_properties(metal_ksw.mm PROPERTIES COMPILE_OPTIONS
            "-fobjc-arc;-Wall;-Wextra;-Werror")
    endif()
    target_compile_definitions(${core} PRIVATE
        WGSE_PG_VERSION="2.31-r1302-wgse-${WGSE_MINIMAP2_VERSION}")

    add_executable(${name} main.cpp)
    target_compile_definitions(${name} PRIVATE WGSE_VERSION="${WGSE_MINIMAP2_VERSION}")
    if(BUILD_TESTING)
        # Hidden tuning and failure-injection flags for tests and benchmarks only.
        target_compile_definitions(${name} PRIVATE WGSE_METAL_BENCHMARK_TOOLS=1)
    endif()
    target_compile_options(${name} PRIVATE -Wall -Wextra -Werror)
    target_link_libraries(${name} PRIVATE ${core})
    if(APPLE)
        # The Objective-C++ autorelease pool around minimap2's main.
        set_source_files_properties(main.cpp PROPERTIES LANGUAGE OBJCXX)
        target_compile_options(${name} PRIVATE -fobjc-arc)
        target_link_libraries(${name} PRIVATE wgse_metal_common)
    endif()
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
    if(WGSE_MINIMAP2_KSW2 STREQUAL "neon")
        # The NEON ksw_extd2 against upstream's, on random alignments over every flag and scoring.
        add_library(minimap2_ksw2_fuzz_upstream OBJECT "${minimap2_SOURCE_DIR}/ksw2_extd2_sse.c")
        target_compile_definitions(minimap2_ksw2_fuzz_upstream PRIVATE
            __SSE2__ __SSE4_1__ SSE2NEON_SUPPRESS_WARNINGS HAVE_KALLOC ksw_extd2_sse=upstream_ksw_extd2)
        target_include_directories(minimap2_ksw2_fuzz_upstream SYSTEM PRIVATE
            "${generated}/intrinsics" "${neon_SOURCE_DIR}" "${minimap2_SOURCE_DIR}")
        add_executable(minimap2-ksw2-fuzz tests/ksw2_fuzz.c wgse_ksw2_extd2.c "${minimap2_SOURCE_DIR}/kalloc.c"
            $<TARGET_OBJECTS:minimap2_ksw2_fuzz_upstream>)
        target_include_directories(minimap2-ksw2-fuzz SYSTEM PRIVATE "${minimap2_SOURCE_DIR}")
        target_compile_definitions(minimap2-ksw2-fuzz PRIVATE HAVE_KALLOC)
        foreach(target minimap2_ksw2_fuzz_upstream minimap2-ksw2-fuzz)
            target_compile_options(${target} PRIVATE -UNDEBUG -fsigned-char -Wno-unused-function)
        endforeach()
        add_test(NAME minimap2_ksw2_fuzz COMMAND minimap2-ksw2-fuzz 20000 1)
    endif()
    if(APPLE OR WGSE_CUDA)
        # The GPU gap fills against upstream's ksw_extd2_sse (skipped without a usable GPU).
        add_executable(minimap2-ksw-gpu-fuzz tests/ksw_gpu_fuzz.cpp)
        target_include_directories(minimap2-ksw-gpu-fuzz PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
        target_compile_definitions(minimap2-ksw-gpu-fuzz PRIVATE HAVE_KALLOC)
        target_compile_options(minimap2-ksw-gpu-fuzz PRIVATE -Wall -Wextra -Werror)
        target_link_libraries(minimap2-ksw-gpu-fuzz PRIVATE wgse_minimap2_core)
        add_test(NAME minimap2_ksw_gpu_fuzz COMMAND minimap2-ksw-gpu-fuzz 60 1)
        set_tests_properties(minimap2_ksw_gpu_fuzz PROPERTIES SKIP_RETURN_CODE 77)
    endif()
    if(WGSE_CUDA)
        # The GPU chaining (split, kernels, unpack) against upstream's score fill.
        add_executable(minimap2-chain-gpu-fuzz tests/chain_gpu_fuzz.cpp)
        target_include_directories(minimap2-chain-gpu-fuzz PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
        target_compile_options(minimap2-chain-gpu-fuzz PRIVATE -Wall -Wextra -Werror)
        target_link_libraries(minimap2-chain-gpu-fuzz PRIVATE wgse_minimap2_core)
        add_test(NAME minimap2_chain_gpu_fuzz COMMAND minimap2-chain-gpu-fuzz 60 1)
        set_tests_properties(minimap2_chain_gpu_fuzz PROPERTIES SKIP_RETURN_CODE 77)
    endif()
    # The anchor sort against upstream's in-place radix sort, including the order of equal keys.
    add_executable(minimap2-sort128-fuzz tests/sort128_fuzz.c)
    target_include_directories(minimap2-sort128-fuzz PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
    target_include_directories(minimap2-sort128-fuzz SYSTEM PRIVATE "${minimap2_SOURCE_DIR}")
    target_compile_options(minimap2-sort128-fuzz PRIVATE -UNDEBUG -Wno-unused-function)
    target_link_libraries(minimap2-sort128-fuzz PRIVATE Threads::Threads)
    add_test(NAME minimap2_sort128_fuzz COMMAND minimap2-sort128-fuzz 3000 1)
    # Unmodified upstream sources, to check that the seams and the NEON ksw_extd2 change
    # no index or alignment.
    if(WGSE_MINIMAP2_KSW2 STREQUAL "neon")
        minimap2_ksw2_library(minimap2_upstream_ksw2 sse41)
    else()
        minimap2_ksw2_library(minimap2_upstream_ksw2 ${WGSE_MINIMAP2_KSW2})
    endif()
    add_executable(minimap2-upstream
        ${minimap2_common_sources} "${minimap2_SOURCE_DIR}/main.c" "${minimap2_SOURCE_DIR}/index.c"
        "${minimap2_SOURCE_DIR}/lchain.c"
        "${minimap2_SOURCE_DIR}/misc.c" "${minimap2_SOURCE_DIR}/seed.c"
        "${minimap2_SOURCE_DIR}/sketch.c" "${minimap2_SOURCE_DIR}/align.c"
        "${minimap2_SOURCE_DIR}/map.c" ${minimap2_upstream_ksw2_objects})
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
if(APPLE)
    install(FILES "${neon_SOURCE_DIR}/LICENSE" DESTINATION share/licenses/wgse-minimap2
        RENAME sse2neon-LICENSE)
endif()
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
