find_package(ZLIB REQUIRED)
find_package(Threads REQUIRED)

FetchContent_Declare(minibwa
    URL https://github.com/lh3/minibwa/archive/refs/tags/v0.7.tar.gz
    URL_HASH SHA256=8a1129bcba045e4af4b6fbf73b3fc6b42208afbab870c774c5f0cd2716d748ae
    SOURCE_SUBDIR unused)
# minibwa bundles libsais 2.10.4 (Apache-2.0) without the licence text; ship it.
FetchContent_Declare(libsais_license
    URL https://raw.githubusercontent.com/IlyaGrebnov/libsais/v2.10.4/LICENSE
    URL_HASH SHA256=cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30
    DOWNLOAD_NO_EXTRACT TRUE
    SOURCE_SUBDIR unused)
FetchContent_MakeAvailable(minibwa libsais_license)

# Bump when the wgse-minibwa package changes; it is versioned separately from minibwa.
set(WGSE_MINIBWA_VERSION 0.1.0)
set(generated "${CMAKE_CURRENT_BINARY_DIR}/generated")
file(MAKE_DIRECTORY "${generated}")

function(minibwa_seam variable before after)
    string(FIND "${${variable}}" "${before}" position)
    if(position EQUAL -1)
        message(FATAL_ERROR "Unexpected upstream minibwa source: ${before}")
    endif()
    string(REPLACE "${before}" "${after}" patched "${${variable}}")
    set(${variable} "${patched}" PARENT_SCOPE)
endfunction()
# main.c becomes a function of the wrapper, and @PG VN gains a -wgse-<version> suffix.
file(READ "${minibwa_SOURCE_DIR}/main.c" main_source)
minibwa_seam(main_source "int main(int argc, char *argv[])"
    "int wgse_minibwa_main(int argc, char *argv[])")
file(WRITE "${generated}/main.c" "${main_source}")
file(READ "${minibwa_SOURCE_DIR}/map-main.c" map_main_source)
minibwa_seam(map_main_source "mb_fmt_sam_hdr(&hdr, idx->l2b, rg_line, MB_VERSION, argc, argv)"
    "mb_fmt_sam_hdr(&hdr, idx->l2b, rg_line, WGSE_PG_VERSION, argc, argv)")
file(WRITE "${generated}/map-main.c" "${map_main_source}")

# Mirrors upstream's Makefile on arm64 with its defaults: the bundled mimalloc, the
# GPL'd low-memory BWT construction (index -l) and upstream's own NEON ksw2 path.
# Apple's compiler has no OpenMP, so libsais indexes on one thread.
set(minibwa_names
    kommon kalloc bwt l2bit options seed map-algo lchain align pe cs format
    ksw2_extz2_sse ksw2_extd2_sse ksw2_ll_sse
    kthread libsais libsais64 index bseq fastmap QSufSort bwtgen)
list(TRANSFORM minibwa_names PREPEND "${minibwa_SOURCE_DIR}/" OUTPUT_VARIABLE minibwa_common_sources)
list(TRANSFORM minibwa_common_sources APPEND ".c")

# Upstream overrides the process allocator with mimalloc, built with its own flags.
add_library(wgse_minibwa_mimalloc OBJECT "${minibwa_SOURCE_DIR}/mimalloc/static.c")
set_target_properties(wgse_minibwa_mimalloc PROPERTIES C_STANDARD 11 C_EXTENSIONS ON)
target_include_directories(wgse_minibwa_mimalloc SYSTEM PRIVATE "${minibwa_SOURCE_DIR}/mimalloc")
target_compile_definitions(wgse_minibwa_mimalloc PRIVATE
    NDEBUG MI_MALLOC_OVERRIDE MI_OSX_INTERPOSE=1 MI_OSX_ZONE=1)
target_compile_options(wgse_minibwa_mimalloc PRIVATE -O3 -w)

function(minibwa_target_settings target)
    target_include_directories(${target} SYSTEM PUBLIC "${minibwa_SOURCE_DIR}")
    target_compile_definitions(${target} PRIVATE USE_GPL)
    set_target_properties(${target} PROPERTIES C_STANDARD 99 C_EXTENSIONS OFF)
    # Upstream asserts stay on, as in its own and Bioconda's builds.
    target_compile_options(${target} PRIVATE -O3 -UNDEBUG -w)
    target_link_libraries(${target} PUBLIC ZLIB::ZLIB Threads::Threads m)
endfunction()

add_library(wgse_minibwa_core STATIC
    ${minibwa_common_sources} "${generated}/main.c" "${generated}/map-main.c")
minibwa_target_settings(wgse_minibwa_core)
target_compile_definitions(wgse_minibwa_core PRIVATE
    WGSE_PG_VERSION="0.7-r421-wgse-${WGSE_MINIBWA_VERSION}")

add_executable(wgse-minibwa main.mm $<TARGET_OBJECTS:wgse_minibwa_mimalloc>)
target_compile_definitions(wgse-minibwa PRIVATE WGSE_VERSION="${WGSE_MINIBWA_VERSION}")
target_compile_options(wgse-minibwa PRIVATE -fobjc-arc -Wall -Wextra -Werror)
target_link_libraries(wgse-minibwa PRIVATE wgse_minibwa_core wgse_metal_common)

if(BUILD_TESTING)
    add_test(NAME minibwa_capabilities COMMAND wgse-minibwa capabilities --json)
    set_tests_properties(minibwa_capabilities PROPERTIES
        PASS_REGULAR_EXPRESSION "\"engine\":\"minibwa\".*\"metal_hardware_supported\":")
    # Unmodified upstream sources, to check that the build changes no index or alignment.
    add_executable(minibwa-upstream ${minibwa_common_sources}
        "${minibwa_SOURCE_DIR}/main.c" "${minibwa_SOURCE_DIR}/map-main.c"
        $<TARGET_OBJECTS:wgse_minibwa_mimalloc>)
    minibwa_target_settings(minibwa-upstream)
    add_test(NAME minibwa_smoke COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tests/smoke.sh"
        $<TARGET_FILE:wgse-minibwa> $<TARGET_FILE:minibwa-upstream> "${CMAKE_CURRENT_BINARY_DIR}/smoke")
endif()

install(TARGETS wgse-minibwa RUNTIME DESTINATION bin)
install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/../../LICENSE" DESTINATION share/licenses/wgse-minibwa
    RENAME WGSE-LICENSE)
# minibwa's MIT licence, with its notices for libsais, mimalloc, QSufSort and bwtgen
# (GPL-2.0-or-later, which makes the binary GPL'd).
install(FILES "${minibwa_SOURCE_DIR}/LICENSE.txt" DESTINATION share/licenses/wgse-minibwa
    RENAME MINIBWA-LICENSE)
install(FILES "${libsais_license_SOURCE_DIR}/LICENSE" DESTINATION share/licenses/wgse-minibwa
    RENAME LIBSAIS-LICENSE)
