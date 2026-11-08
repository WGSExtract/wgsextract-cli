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
set(WGSE_BWA_MEM3_VERSION 0.1.0)
set(MEM3_VERSION 0.16.0)
set(generated "${CMAKE_CURRENT_BINARY_DIR}/generated")
# src/ includes the vendored "../ext/pdqsort/pdqsort.h".
file(COPY "${mem3_SOURCE_DIR}/ext/pdqsort" DESTINATION "${generated}/ext")

file(GLOB libdeflate_sources "${mem3_libdeflate_SOURCE_DIR}/lib/*.c")
add_library(mem3_libdeflate STATIC ${libdeflate_sources}
    "${mem3_libdeflate_SOURCE_DIR}/lib/arm/cpu_features.c")
target_include_directories(mem3_libdeflate SYSTEM PUBLIC "${mem3_libdeflate_SOURCE_DIR}")
set_target_properties(mem3_libdeflate PROPERTIES OUTPUT_NAME deflate
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/libdeflate")

# htslib 1.21 (upstream's submodule commit is that release), for --bam output,
# configured as upstream's Makefile does, with the libdeflate above.
set(htslib_dir "${CMAKE_CURRENT_BINARY_DIR}/htslib")
file(MAKE_DIRECTORY "${htslib_dir}")
ExternalProject_Add(mem3_htslib_build
    URL https://github.com/samtools/htslib/releases/download/1.21/htslib-1.21.tar.bz2
    URL_HASH SHA256=84b510e735f4963641f26fd88c8abdee81ff4cb62168310ae716636aac0f1823
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    SOURCE_DIR "${htslib_dir}"
    BUILD_IN_SOURCE TRUE
    CONFIGURE_COMMAND ./configure "CC=${CMAKE_C_COMPILER}"
        "CFLAGS=-g -O2 -mmacosx-version-min=${CMAKE_OSX_DEPLOYMENT_TARGET}"
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

# Upstream's Makefile OBJS for an arm64 build, which has one (NEON) kernel tier.
set(mem3_names
    fastmap bwtindex utils kthread kstring bntseq bwamem seed_order profiling compat_target
    FMI_search fmi_seed_api read_index_ele bwamem_pair bwa bwamem_extra kopen bam_writer
    meth_bam meth_xm meth_opts meth_model packed_text fm_index_writer index_prelude system
    libsais_build bwa_shm bwa_hugepages bwa_thp simd_dispatch stage_prof smem_dedup
    lockstep_width read_memo rescue_band bandedSWA kswv ksw sam_encode)
set(mem3_c_names fast_reader fast_reader_bseq fr_fastq)

# Upstream's compile flags for arm64: -O3, gnu++14, asserts on (it strips -DNDEBUG),
# sse2neon's SSE feature macros and 128-byte cache lines.
function(mem3_core target source_dir)
    list(TRANSFORM mem3_names PREPEND "${source_dir}/" OUTPUT_VARIABLE sources)
    list(TRANSFORM sources APPEND ".cpp")
    list(TRANSFORM mem3_c_names PREPEND "${source_dir}/" OUTPUT_VARIABLE c_sources)
    list(TRANSFORM c_sources APPEND ".c")
    add_library(${target} STATIC ${sources} ${c_sources} ${ARGN})
    set_target_properties(${target} PROPERTIES CXX_STANDARD 14 CXX_EXTENSIONS ON)
    target_compile_definitions(${target} PUBLIC
        APPLE_SILICON=1 __SSE__=1 __SSE2__=1 __SSE3__=1 __SSSE3__=1 __SSE4_1__=1 __SSE4_2__=1
        CACHE_LINE_BYTES=128 ENABLE_PREFETCH V17=1 MATE_SORT=0 USE_MIMALLOC=1)
    target_include_directories(${target} SYSTEM PUBLIC "${source_dir}" "${mem3_neon_SOURCE_DIR}")
    target_compile_options(${target} PRIVATE -O3 -UNDEBUG
        $<$<COMPILE_LANGUAGE:CXX>:-fpermissive> -w)
    target_link_libraries(${target} PUBLIC
        mem3_htslib mem3_libsais mem3_mimalloc zlib mem3_libdeflate ZLIB::ZLIB Threads::Threads)
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

mem3_core(wgse_mem3_core "${mem3_src}" "${mem3_src}/main.cpp")
add_executable(wgse-bwa-mem3 main.mm)
target_compile_definitions(wgse-bwa-mem3 PRIVATE WGSE_VERSION="${WGSE_BWA_MEM3_VERSION}"
    MEM3_VERSION="${MEM3_VERSION}")
target_compile_options(wgse-bwa-mem3 PRIVATE -fobjc-arc -Wall -Wextra -Werror)
target_link_libraries(wgse-bwa-mem3 PRIVATE wgse_mem3_core wgse_metal_common)

if(BUILD_TESTING)
    add_test(NAME bwa_mem3_capabilities COMMAND wgse-bwa-mem3 capabilities --json)
    set_tests_properties(bwa_mem3_capabilities PROPERTIES
        PASS_REGULAR_EXPRESSION "\"engine\":\"bwa-mem3\".*\"metal_hardware_supported\":")
    # "version" prints one line, like --version and the other wrappers.
    add_test(NAME bwa_mem3_version COMMAND wgse-bwa-mem3 version)
    set_tests_properties(bwa_mem3_version PROPERTIES
        PASS_REGULAR_EXPRESSION "^wgse-bwa-mem3 [0-9.]+; bwa-mem3 [0-9.]+\n$")
    # Without a Metal backend, --metal must fail rather than silently use the CPU.
    add_test(NAME bwa_mem3_metal_refused COMMAND wgse-bwa-mem3 --metal mem)
    set_tests_properties(bwa_mem3_metal_refused PROPERTIES
        PASS_REGULAR_EXPRESSION "Metal unavailable: wgse-bwa-mem3 [^ ]+ has no Metal backend")
    add_test(NAME bwa_mem3_smoke COMMAND "${PROJECT_SOURCE_DIR}/tests/smoke.sh"
        $<TARGET_FILE:wgse-bwa-mem3> "${CMAKE_CURRENT_BINARY_DIR}/smoke" cpu)
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
