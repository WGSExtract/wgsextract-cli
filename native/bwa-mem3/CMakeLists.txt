find_package(ZLIB REQUIRED)
find_package(Threads REQUIRED)
include(ExternalProject)

# bwa-mem3 0.16.0 and the commits of its ext/ submodules at that tag.
FetchContent_Declare(mem3
    URL https://github.com/fg-labs/bwa-mem3/archive/refs/tags/v0.16.0.tar.gz
    URL_HASH SHA256=28f7e4be7cf359e97b59f2925bb67b27905058c13e6c8af08507b13f2c10662c
    SOURCE_SUBDIR unused)
# A newer sse2neon than the one bwa-mem2 and minimap2 declare as "neon".
FetchContent_Declare(mem3_neon
    URL https://github.com/DLTcollab/sse2neon/archive/92f6de174717aef09033ad21568d5bb9e5470404.tar.gz
    URL_HASH SHA256=c6b1abea2debdd534a3845e9079a99cac1c585ded4f8822676e6734ff3d55acb
    SOURCE_SUBDIR unused)
FetchContent_Declare(mem3_libsais
    URL https://github.com/IlyaGrebnov/libsais/archive/1a696e600ec62ef7900d77c00af6d76983f7a658.tar.gz
    URL_HASH SHA256=b04859f4f312bfe91a18c7973be1c2c7487f6871ae49fcce5d7572cf541c9816
    SOURCE_SUBDIR unused)
FetchContent_Declare(mem3_mimalloc
    URL https://github.com/microsoft/mimalloc/archive/0ddf397796fbefa35b3278bd4431c2913a9892eb.tar.gz
    URL_HASH SHA256=217afdcf73286e0c57b4dc03926cc8d3f54fe777cca56da73396bd7e18781954
    SOURCE_SUBDIR unused)
FetchContent_Declare(mem3_zlibng
    URL https://github.com/zlib-ng/zlib-ng/archive/860e4cff7917d93f54f5d7f0bc1d0e8b1a3cb988.tar.gz
    URL_HASH SHA256=97f98a25fc3e27cda4f6972dbdac9db15175969e766e17c3db67d0dab897dcec)
# Upstream takes libdeflate from the system; this is the release Bioconda's build uses.
FetchContent_Declare(mem3_libdeflate
    URL https://github.com/ebiggers/libdeflate/archive/refs/tags/v1.25.tar.gz
    URL_HASH SHA256=d11473c1ad4c57d874695e8026865e38b47116bbcb872bfc622ec8f37a86017d
    SOURCE_SUBDIR unused)
# zlib-ng's native (zng_*) API as a static library, without its tests or install rules.
set(ZLIB_COMPAT OFF)
set(ZLIB_ENABLE_TESTS OFF)
set(ZLIBNG_ENABLE_TESTS OFF)
set(WITH_GTEST OFF)
set(BUILD_SHARED_LIBS OFF)
set(SKIP_INSTALL_ALL ON)
FetchContent_MakeAvailable(mem3 mem3_neon mem3_libsais mem3_mimalloc mem3_zlibng mem3_libdeflate)

# Bump when the wgse-bwa-mem3 package changes; it is versioned separately from bwa-mem3.
set(WGSE_BWA_MEM3_VERSION 0.4.0)
set(MEM3_VERSION 0.16.0)
set(generated "${CMAKE_CURRENT_BINARY_DIR}/generated")
# src/ includes the vendored "../ext/pdqsort/pdqsort.h".
file(COPY "${mem3_SOURCE_DIR}/ext/pdqsort" DESTINATION "${generated}/ext")

file(GLOB libdeflate_sources "${mem3_libdeflate_SOURCE_DIR}/lib/*.c")
if(APPLE)
    set(libdeflate_cpu arm)
else()
    set(libdeflate_cpu x86)
endif()
add_library(mem3_libdeflate STATIC ${libdeflate_sources}
    "${mem3_libdeflate_SOURCE_DIR}/lib/${libdeflate_cpu}/cpu_features.c")
target_include_directories(mem3_libdeflate SYSTEM PUBLIC "${mem3_libdeflate_SOURCE_DIR}")
set_target_properties(mem3_libdeflate PROPERTIES OUTPUT_NAME deflate
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/libdeflate")

# htslib 1.21 (upstream's submodule commit is that release), for --bam output,
# configured as upstream's Makefile does, with the libdeflate above.
set(htslib_dir "${CMAKE_CURRENT_BINARY_DIR}/htslib")
set(htslib_cflags "-g -O2")
if(APPLE)
    string(APPEND htslib_cflags " -mmacosx-version-min=${CMAKE_OSX_DEPLOYMENT_TARGET}")
endif()
file(MAKE_DIRECTORY "${htslib_dir}")
ExternalProject_Add(mem3_htslib_build
    URL https://github.com/samtools/htslib/releases/download/1.21/htslib-1.21.tar.bz2
    URL_HASH SHA256=84b510e735f4963641f26fd88c8abdee81ff4cb62168310ae716636aac0f1823
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    SOURCE_DIR "${htslib_dir}"
    BUILD_IN_SOURCE TRUE
    CONFIGURE_COMMAND ./configure "CC=${CMAKE_C_COMPILER}"
        "CFLAGS=${htslib_cflags}"
        "CPPFLAGS=-I${mem3_libdeflate_SOURCE_DIR}"
        "LDFLAGS=-L${CMAKE_CURRENT_BINARY_DIR}/libdeflate"
        --with-libdeflate --disable-bz2 --disable-lzma --disable-libcurl --disable-gcs
        --disable-s3 --disable-plugins
    BUILD_COMMAND make -j4 libhts.a
    INSTALL_COMMAND ""
    BUILD_BYPRODUCTS "${htslib_dir}/libhts.a"
    DEPENDS mem3_libdeflate)
add_library(mem3_htslib STATIC IMPORTED)
set_target_properties(mem3_htslib PROPERTIES IMPORTED_LOCATION "${htslib_dir}/libhts.a")
target_include_directories(mem3_htslib SYSTEM INTERFACE "${htslib_dir}")
target_link_libraries(mem3_htslib INTERFACE mem3_libdeflate ZLIB::ZLIB)
add_dependencies(mem3_htslib mem3_htslib_build)

option(WGSE_MEM3_MIMALLOC_OVERRIDE "Make mimalloc the process's malloc" OFF)
option(WGSE_MEM3_LTO "Link-time optimization of bwa-mem3" OFF)
add_library(mem3_mimalloc STATIC "${mem3_mimalloc_SOURCE_DIR}/src/static.c")
target_include_directories(mem3_mimalloc SYSTEM PUBLIC "${mem3_mimalloc_SOURCE_DIR}/include")
target_compile_definitions(mem3_mimalloc PRIVATE MI_BUILD_RELEASE)
if(WGSE_MEM3_MIMALLOC_OVERRIDE)
    target_compile_definitions(mem3_mimalloc PRIVATE MI_MALLOC_OVERRIDE MI_OSX_ZONE=1)
endif()
target_compile_options(mem3_mimalloc PRIVATE -fvisibility=hidden -ftls-model=initial-exec
    -Wno-deprecated)

# libsais for `index`. Apple's clang has no OpenMP runtime, so its OpenMP API (which
# upstream's index code calls) runs on one thread: indexing is single-threaded and
# gives the same index; alignment never uses OpenMP.
file(WRITE "${generated}/omp/omp.h" "#pragma once
static inline int omp_get_thread_num(void) { return 0; }
static inline int omp_get_num_threads(void) { return 1; }
static inline int omp_get_max_threads(void) { return 1; }
static inline int omp_get_dynamic(void) { return 0; }
")
add_library(mem3_libsais STATIC
    "${mem3_libsais_SOURCE_DIR}/src/libsais.c" "${mem3_libsais_SOURCE_DIR}/src/libsais64.c")
target_include_directories(mem3_libsais SYSTEM PUBLIC "${mem3_libsais_SOURCE_DIR}/include")
target_include_directories(mem3_libsais SYSTEM PRIVATE "${generated}/omp")
target_compile_definitions(mem3_libsais PUBLIC LIBSAIS_OPENMP)
target_compile_options(mem3_libsais PRIVATE -O3 -std=c99 -Wno-unknown-pragmas)

function(mem3_seam variable before after)
    string(FIND "${${variable}}" "${before}" position)
    if(position EQUAL -1)
        message(FATAL_ERROR "Unexpected upstream bwa-mem3 source: ${before}")
    endif()
    string(REPLACE "${before}" "${after}" patched "${${variable}}")
    set(${variable} "${patched}" PARENT_SCOPE)
endfunction()

# Upstream's Makefile OBJS. An arm64 build has one (NEON) kernel tier; on x86-64 the kernels
# are also built for each tier (mem3_kernel_tiers), and simd_dispatch picks one at run time.
set(mem3_names
    fastmap bwtindex utils kthread kstring bntseq bwamem seed_order profiling compat_target
    FMI_search fmi_seed_api read_index_ele bwamem_pair bwa bwamem_extra kopen bam_writer
    meth_bam meth_xm meth_opts meth_model packed_text fm_index_writer index_prelude system
    libsais_build bwa_shm bwa_hugepages bwa_thp simd_dispatch stage_prof smem_dedup
    lockstep_width read_memo rescue_band bandedSWA kswv ksw sam_encode)
set(mem3_c_names fast_reader fast_reader_bseq fr_fastq)

# Upstream's compile flags: -O3, gnu++14 and asserts on (it strips -DNDEBUG); for arm64,
# sse2neon's SSE feature macros and 128-byte cache lines; for x86-64 (its default `single`
# build), AVX2 as the baseline and the kernel sources again for each tier.
set(mem3_kernel_names bandedSWA kswv ksw sam_encode)
set(mem3_kernel_tiers sse41 sse42 avx avx2 avx512bw)
set(mem3_tier_flags_sse41 -msse4.1)
set(mem3_tier_flags_sse42 -msse4.2 -mpopcnt)
set(mem3_tier_flags_avx -mavx -mpopcnt)
set(mem3_tier_flags_avx2 -mavx -mavx2 -mpopcnt)
set(mem3_tier_flags_avx512bw -mavx -mavx2 -mavx512f -mavx512bw -mpopcnt)
set(mem3_definitions ENABLE_PREFETCH V17=1 MATE_SORT=0 USE_MIMALLOC=1)
if(APPLE)
    list(APPEND mem3_definitions APPLE_SILICON=1 __SSE__=1 __SSE2__=1 __SSE3__=1 __SSSE3__=1
        __SSE4_1__=1 __SSE4_2__=1 CACHE_LINE_BYTES=128)
endif()
function(mem3_core target source_dir)
    list(TRANSFORM mem3_names PREPEND "${source_dir}/" OUTPUT_VARIABLE sources)
    list(TRANSFORM sources APPEND ".cpp")
    list(TRANSFORM mem3_c_names PREPEND "${source_dir}/" OUTPUT_VARIABLE c_sources)
    list(TRANSFORM c_sources APPEND ".c")
    add_library(${target} STATIC ${sources} ${c_sources} ${ARGN})
    set_target_properties(${target} PROPERTIES CXX_STANDARD 14 CXX_EXTENSIONS ON)
    target_compile_definitions(${target} PUBLIC ${mem3_definitions})
    target_include_directories(${target} SYSTEM PUBLIC "${source_dir}")
    target_compile_options(${target} PRIVATE -O3 -UNDEBUG
        $<$<COMPILE_LANGUAGE:CXX>:-fpermissive> -w)
    if(APPLE)
        target_include_directories(${target} SYSTEM PUBLIC "${mem3_neon_SOURCE_DIR}")
    else()
        target_compile_options(${target} PRIVATE -mavx2)
        list(TRANSFORM mem3_kernel_names PREPEND "${source_dir}/" OUTPUT_VARIABLE kernels)
        list(TRANSFORM kernels APPEND ".cpp")
        foreach(tier IN LISTS mem3_kernel_tiers)
            add_library(${target}_${tier} OBJECT ${kernels})
            set_target_properties(${target}_${tier} PROPERTIES CXX_STANDARD 14 CXX_EXTENSIONS ON
                INTERPROCEDURAL_OPTIMIZATION ${WGSE_MEM3_LTO})
            target_compile_definitions(${target}_${tier} PRIVATE ${mem3_definitions}
                KERNEL_VARIANT=_${tier})
            target_include_directories(${target}_${tier} SYSTEM PRIVATE
                $<TARGET_PROPERTY:${target},INCLUDE_DIRECTORIES>)
            target_compile_options(${target}_${tier} PRIVATE -O3 -UNDEBUG -fpermissive -w
                ${mem3_tier_flags_${tier}})
            target_link_libraries(${target}_${tier} PRIVATE
                mem3_htslib mem3_libsais mem3_mimalloc zlib mem3_libdeflate)
            target_sources(${target} PRIVATE $<TARGET_OBJECTS:${target}_${tier}>)
        endforeach()
    endif()
    target_link_libraries(${target} PUBLIC
        mem3_htslib mem3_libsais mem3_mimalloc zlib mem3_libdeflate ZLIB::ZLIB Threads::Threads)
    if(NOT APPLE)
        # shm_open (bwa-mem3's shm command) is in librt before glibc 2.34.
        target_link_libraries(${target} PUBLIC rt)
    endif()
    set_target_properties(${target} PROPERTIES INTERPROCEDURAL_OPTIMIZATION ${WGSE_MEM3_LTO})
endfunction()

# A patched copy of src/: main() becomes a function of the wrapper, and @PG VN gains
# a -wgse-<version> suffix.
set(mem3_src "${generated}/mem3-src")
file(COPY "${mem3_SOURCE_DIR}/src/" DESTINATION "${mem3_src}")
file(WRITE "${mem3_src}/version.h"
    "#define PACKAGE_VERSION \"${MEM3_VERSION}-wgse-${WGSE_BWA_MEM3_VERSION}\"\n")
file(READ "${mem3_SOURCE_DIR}/src/main.cpp" main_source)
mem3_seam(main_source "int main(int argc, char* argv[])"
    "extern \"C\" int wgse_bwamem3_main(int argc, char* argv[])")
file(WRITE "${mem3_src}/main.cpp" "${main_source}")
# GPU seed extension (--metal, --cuda): getScores8, the 8-bit banded SW tier, runs on
# extension.metal (extension.cuh), which computes the scalar banded SW that upstream's NEON,
# SSE, AVX2 and AVX-512 kernels match; the hook goes in each of them.
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/extension.metal")
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/extension.metal" extension_source)
configure_file(extension_source.h.in "${generated}/extension_source.h" @ONLY)
set(bsw8_guard "    {\n        BswOvershootGuard _g(pairArray, numPairs, SIMD_WIDTH8, guard_overshoot_);")
file(READ "${mem3_SOURCE_DIR}/src/bandedSWA.cpp" bsw_source)
mem3_seam(bsw_source "${bsw8_guard}" "    if (!cpg_ && m == 5 && !guard_overshoot_) {
        const WgseBswScoring wgse_scoring{o_del, e_del, o_ins, e_ins, zdrop, end_bonus, mat};
        if (wgse_mem3_bsw8(pairArray, seqBufRef, seqBufQer, numPairs, w, wgse_scoring, SIMD_WIDTH8))
            return;
    }
${bsw8_guard}")
file(WRITE "${mem3_src}/bandedSWA.cpp" "#include \"metal_extension.h\"\n${bsw_source}")
# GPU seeding (--metal, --cuda): wgse-bwa-mem2's seeding.metal (seeding.cuh) collects
# mem_collect_smem's SMEMs on the GPU, from the occurrence table in place on Metal (so it is
# allocated page-aligned), from a copy on CUDA.
set(mem2_dir "${CMAKE_CURRENT_SOURCE_DIR}/../bwa-mem2")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${mem2_dir}/seeding_types.h" "${mem2_dir}/seeding.metal")
file(READ "${mem2_dir}/seeding_types.h" seeding_types)
file(READ "${mem2_dir}/seeding.metal" seeding_source)
configure_file(seeding_source.h.in "${generated}/seeding_source.h" @ONLY)
configure_file("${mem2_dir}/seeding_types.h" "${generated}/seeding_types.h" COPYONLY)
file(READ "${mem3_SOURCE_DIR}/src/FMI_search.h" fmi_header)
mem3_seam(fmi_header "    public:\n    FMI_search(const char *fname);"
    "    public:\n    const CP_OCC *wgse_cp_occ() const { return cp_occ; }\n    const int64_t *wgse_count() const { return count; }\n    FMI_search(const char *fname);")
file(WRITE "${mem3_src}/FMI_search.h" "${fmi_header}")
file(READ "${mem3_SOURCE_DIR}/src/FMI_search.cpp" fmi_source)
mem3_seam(fmi_source "    cp_occ = (CP_OCC *)_mm_malloc(cp_occ_bytes, 64);"
    "    cp_occ = (CP_OCC *)_mm_malloc((cp_occ_bytes + 16383) / 16384 * 16384, 16384);")
# The GPU seeder reads cp_occ in place, so it stops before the index is freed; after the GPU
# watchdog gives up, a command may still read it, and the index is left to the process's exit.
mem3_seam(fmi_source "FMI_search::~FMI_search()\n{\n"
    "FMI_search::~FMI_search()\n{\n    if (wgse_mem3_seed_release(this)) return;\n")
file(WRITE "${mem3_src}/FMI_search.cpp"
    "class FMI_search;\nbool wgse_mem3_seed_release(const FMI_search *fmi);\n${fmi_source}")
file(READ "${mem3_SOURCE_DIR}/src/bwamem.cpp" bwamem_source)
mem3_seam(bwamem_source "                       int64_t &tot_smem,\n                       int tid)\n{\n"
    "                       int64_t &tot_smem,\n                       int tid)\n{\n    {\n        SMEM *wgse_smems = nullptr;\n        if (wgse_mem3_seed_collect(fmi, opt, seq_, nseq, mmc, tot_smem, tid, wgse_smems))\n            return wgse_smems;\n    }\n")
mem3_seam(bwamem_source "    kt_for(worker_bwt_aln, &w, n_); // SMEMs (+SAL) then BSW, fused\n"
    "    wgse_mem3_seed_chunk(w.fmi, opt, seqs, n_);\n    kt_for(worker_bwt_aln, &w, n_); // SMEMs (+SAL) then BSW, fused\n    wgse_mem3_seed_chunk_done(seqs);\n")
mem3_seam(bwamem_source "    uint64_t tim = __rdtsc();\n    kt_for(worker_bwt_aln, &w, n);\n"
    "    uint64_t tim = __rdtsc();\n    wgse_mem3_seed_chunk(w.fmi, opt, seqs, n);\n    kt_for(worker_bwt_aln, &w, n);\n    wgse_mem3_seed_chunk_done(seqs);\n")
mem3_seam(bwamem_source "#include \"smem_dedup.h\"\n" "#include \"smem_dedup.h\"\n#include \"metal_seeding.h\"\n")
file(WRITE "${mem3_src}/bwamem.cpp" "${bwamem_source}")
# While workers wait for the GPU, others use their cores: --metal runs more compute threads
# (the reads per chunk, and so the output, do not depend on them).
file(READ "${mem3_SOURCE_DIR}/src/fastmap.cpp" fastmap_source)
mem3_seam(fastmap_source "    int32_t nthreads = opt->n_threads; // global variable for profiling!\n    w.nthreads = opt->n_threads;"
    "    int32_t nthreads = wgse_mem3_compute_threads(opt->n_threads);\n    w.nthreads = nthreads;")
# The same low-memory warning as wgse-bwa-mem2, before the index is loaded.
mem3_seam(fastmap_source "    aux.fmi = new FMI_search(ref_prefix);"
    "    wgse_mem3_memory_preflight(ref_prefix, fixed_chunk_size > 0 ? int64_t(fixed_chunk_size)\n        : int64_t(opt->chunk_size) * opt->n_threads);\n    aux.fmi = new FMI_search(ref_prefix);")
# The GPU seeds each chunk that maps whole while the previous one maps.
mem3_seam(fastmap_source "#include \"rescue_band.h\"\n" "#include \"rescue_band.h\"\n#include \"metal_seeding.h\"\n")
mem3_seam(fastmap_source "        return ret;\n    } // Step 0\n"
    "        if (ret->cohort_complete && !mid_cohort && !(aux->opt->flag & MEM_F_SMARTPE))\n            wgse_mem3_seed_preseed(aux->fmi, aux->opt, ret->seqs, ret->n_seqs);\n        return ret;\n    } // Step 0\n")
mem3_seam(fastmap_source "        read_arena_destroy(ret->read_arena);\n        free(ret->seqs);\n"
    "        wgse_mem3_seed_forget(ret->seqs);\n        read_arena_destroy(ret->read_arena);\n        free(ret->seqs);\n")
file(WRITE "${mem3_src}/fastmap.cpp" "#include \"metal_extension.h\"\n${fastmap_source}")
# The GPU backend: Metal on macOS, CUDA on Linux with -DWGSE_CUDA=ON, else the CPU's code
# only. seeding_host.cpp schedules GPU seeding for either.
if(APPLE)
    set(gpu_sources metal_extension.mm seeding_host.cpp metal_seeding_device.mm)
    set_source_files_properties(metal_extension.mm metal_seeding_device.mm main.cpp
        PROPERTIES COMPILE_OPTIONS "-fobjc-arc")
    set_source_files_properties(main.cpp PROPERTIES LANGUAGE OBJCXX)
elseif(WGSE_CUDA)
    set(gpu_sources seeding_host.cpp)
    add_library(wgse_mem3_cuda STATIC cuda_extension.cu cuda_seeding.cu)
    target_include_directories(wgse_mem3_cuda PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}"
        "${CMAKE_CURRENT_SOURCE_DIR}/../gpu-common" "${CMAKE_CURRENT_SOURCE_DIR}/../bwa-mem2")
    target_compile_options(wgse_mem3_cuda PRIVATE
        $<$<COMPILE_LANGUAGE:CUDA>:--Werror=all-warnings -Xcompiler=-Wall,-Wextra>)
    # The static runtime: the package needs only the NVIDIA driver.
    target_link_libraries(wgse_mem3_cuda PUBLIC CUDA::cudart_static)
else()
    set(gpu_sources gpu_none.cpp seeding_none.cpp)
endif()
mem3_core(wgse_mem3_core "${mem3_src}" "${mem3_src}/main.cpp" host_memory.cpp ${gpu_sources})
target_include_directories(wgse_mem3_core PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}"
    PRIVATE "${generated}")
if(TARGET wgse_mem3_cuda)
    target_link_libraries(wgse_mem3_core PUBLIC wgse_mem3_cuda)
endif()
add_executable(wgse-bwa-mem3 main.cpp)
target_compile_definitions(wgse-bwa-mem3 PRIVATE WGSE_VERSION="${WGSE_BWA_MEM3_VERSION}"
    MEM3_VERSION="${MEM3_VERSION}")
target_compile_options(wgse-bwa-mem3 PRIVATE -Wall -Wextra -Werror)
target_link_libraries(wgse-bwa-mem3 PRIVATE wgse_mem3_core)
if(APPLE)
    target_link_libraries(wgse_mem3_core PUBLIC wgse_metal_common)
endif()

if(BUILD_TESTING)
    # Hidden failure-injection flag (--gpu-inject-hang) for tests only.
    target_compile_definitions(wgse-bwa-mem3 PRIVATE WGSE_METAL_BENCHMARK_TOOLS=1)
    add_test(NAME bwa_mem3_capabilities COMMAND wgse-bwa-mem3 capabilities --json)
    set_tests_properties(bwa_mem3_capabilities PROPERTIES
        PASS_REGULAR_EXPRESSION "\"engine\":\"bwa-mem3\".*\"metal_hardware_supported\":.*\"physical_memory_bytes\":[1-9]")
    # "version" prints one line, like --version and the other wrappers.
    add_test(NAME bwa_mem3_version COMMAND wgse-bwa-mem3 version)
    set_tests_properties(bwa_mem3_version PROPERTIES
        PASS_REGULAR_EXPRESSION "^wgse-bwa-mem3 [0-9.]+; bwa-mem3 [0-9.]+\n$")
    add_test(NAME bwa_mem3_smoke COMMAND "${PROJECT_SOURCE_DIR}/tests/smoke.sh"
        $<TARGET_FILE:wgse-bwa-mem3> "${CMAKE_CURRENT_BINARY_DIR}/smoke" metal)
    # Unmodified upstream sources, to check that wgse-bwa-mem3 changes no alignment.
    set(upstream_src "${generated}/upstream-src")
    file(COPY "${mem3_SOURCE_DIR}/src/" DESTINATION "${upstream_src}")
    file(WRITE "${upstream_src}/version.h" "#define PACKAGE_VERSION \"${MEM3_VERSION}\"\n")
    mem3_core(mem3_upstream_core "${upstream_src}")
    add_executable(bwa-mem3-upstream "${upstream_src}/main.cpp")
    set_target_properties(bwa-mem3-upstream PROPERTIES CXX_STANDARD 14 CXX_EXTENSIONS ON)
    target_compile_options(bwa-mem3-upstream PRIVATE -O3 -UNDEBUG -fpermissive -w)
    target_link_libraries(bwa-mem3-upstream PRIVATE mem3_upstream_core)
    add_test(NAME bwa_mem3_upstream_parity COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tests/upstream.sh"
        $<TARGET_FILE:wgse-bwa-mem3> $<TARGET_FILE:bwa-mem3-upstream>
        "${CMAKE_CURRENT_BINARY_DIR}/upstream")
    # Too little memory for the run is reported, and changes no alignment.
    add_test(NAME bwa_mem3_low_memory_warning
        COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tests/upstream.sh"
        $<TARGET_FILE:wgse-bwa-mem3> $<TARGET_FILE:bwa-mem3-upstream>
        "${CMAKE_CURRENT_BINARY_DIR}/low-memory" --physical-memory 1048576)
    # The GPU's SMEMs and seed extensions, including those committed again after an
    # interruption (Metal), and the CPU's after a GPU failure, must leave every record
    # unchanged. --gpu-seed-wait has each chunk use its GPU SMEMs, which --gpu-verify-seeds
    # compares with upstream's.
    if(APPLE)
        set(gpu metal)
        set(parity_options --gpu-seed-wait --gpu-inject-interruptions 2)
    elseif(WGSE_CUDA)
        set(gpu cuda)
        set(parity_options --gpu-seed-wait)
    endif()
    if(gpu)
        set(gpu_tests)
        foreach(test IN ITEMS "parity;${parity_options}"
                "seed_verify;--gpu-seed-wait;--gpu-verify-seeds"
                # Seed extension on the GPU without GPU seeding, as in 0.2.0.
                "cpu_seeding_parity;--gpu-seed;off"
                "failure_parity;--gpu-inject-extension-failure"
                # A failed GPU seeding batch: the CPU seeds, and the run exits 1.
                "seed_failure_parity;--gpu-seed-wait;--gpu-inject-seed-failure"
                # Regression (#548): a GPU command that never completes is given up by the
                # watchdog.
                "watchdog;--gpu-inject-hang;extend"
                # A hung GPU seeding command: the chunk waits for it, then the run goes on on
                # the CPU.
                "seed_watchdog;--gpu-seed-wait;--gpu-inject-hang;collect_smems")
            list(POP_FRONT test name)
            string(REPLACE "_" "-" directory "${gpu}-${name}")
            add_test(NAME bwa_mem3_${gpu}_${name}
                COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tests/upstream.sh"
                $<TARGET_FILE:wgse-bwa-mem3> $<TARGET_FILE:bwa-mem3-upstream>
                "${CMAKE_CURRENT_BINARY_DIR}/${directory}" --${gpu} ${test})
            list(APPEND gpu_tests bwa_mem3_${gpu}_${name})
        endforeach()
        add_test(NAME bwa_mem3_auto_watchdog
            COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tests/upstream.sh"
            $<TARGET_FILE:wgse-bwa-mem3> $<TARGET_FILE:bwa-mem3-upstream>
            "${CMAKE_CURRENT_BINARY_DIR}/auto-watchdog" --backend auto --gpu-inject-hang extend)
        set_tests_properties(${gpu_tests} bwa_mem3_auto_watchdog PROPERTIES SKIP_RETURN_CODE 77)
    endif()
endif()

install(TARGETS wgse-bwa-mem3 RUNTIME DESTINATION bin)
set(licenses share/licenses/wgse-bwa-mem3)
install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/../../LICENSE" DESTINATION ${licenses}
    RENAME WGSE-LICENSE)
install(FILES "${mem3_SOURCE_DIR}/LICENSE" DESTINATION ${licenses} RENAME BWA-MEM3-LICENSE)
install(FILES "${mem3_neon_SOURCE_DIR}/LICENSE" DESTINATION ${licenses} RENAME sse2neon-LICENSE)
install(FILES "${mem3_libsais_SOURCE_DIR}/LICENSE" DESTINATION ${licenses} RENAME libsais-LICENSE)
install(FILES "${mem3_mimalloc_SOURCE_DIR}/LICENSE" DESTINATION ${licenses}
    RENAME mimalloc-LICENSE)
install(FILES "${mem3_zlibng_SOURCE_DIR}/LICENSE.md" DESTINATION ${licenses}
    RENAME zlib-ng-LICENSE)
install(FILES "${mem3_libdeflate_SOURCE_DIR}/COPYING" DESTINATION ${licenses}
    RENAME libdeflate-LICENSE)
install(FILES "${htslib_dir}/LICENSE" DESTINATION ${licenses} RENAME htslib-LICENSE)
# htslib's CRAM codecs (rANS, arithmetic coder, ...) come from its bundled htscodecs.
install(FILES "${htslib_dir}/htscodecs/LICENSE.md" DESTINATION ${licenses}
    RENAME htscodecs-LICENSE)
