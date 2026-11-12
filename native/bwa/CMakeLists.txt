find_package(ZLIB REQUIRED)
find_package(Threads REQUIRED)

FetchContent_Declare(bwa
    URL https://github.com/lh3/bwa/archive/refs/tags/v0.7.19.tar.gz
    URL_HASH SHA256=cdff5db67652c5b805a3df08c4e813a822c65791913eccfb3cf7d528588f37bc
    SOURCE_SUBDIR unused)
# bwa-mem3 0.16.0 (as in ../bwa-mem2 and ../bwa-mem3; the first declaration
# wins), for its exact mate-rescue pruning headers.
FetchContent_Declare(mem3
    URL https://github.com/fg-labs/bwa-mem3/archive/refs/tags/v0.16.0.tar.gz
    URL_HASH SHA256=28f7e4be7cf359e97b59f2925bb67b27905058c13e6c8af08507b13f2c10662c
    SOURCE_SUBDIR unused)
FetchContent_MakeAvailable(bwa mem3)

# Bump when the wgse-bwa package changes; it is versioned separately from BWA.
set(WGSE_BWA_VERSION 0.12.1)
set(generated "${CMAKE_CURRENT_BINARY_DIR}/generated")
file(MAKE_DIRECTORY "${generated}")
include("${CMAKE_CURRENT_SOURCE_DIR}/upstream_seams.cmake")

# Mirrors upstream's Makefile LOBJS, AOBJS and main.o.
set(bwa_names
    utils kthread kstring ksw bwt bntseq bwa bwamem bwamem_pair bwamem_extra
    malloc_wrap QSufSort bwt_gen rope rle is bwtindex
    bwashm bwase bwaseqio bwtgap bwtaln bamlite bwape kopen pemerge maxk
    bwtsw2_core bwtsw2_main bwtsw2_aux bwt_lite bwtsw2_chain fastmap bwtsw2_pair
    main)
foreach(name IN LISTS bwa_names)
    if(name IN_LIST patched_sources)
        list(APPEND bwa_sources "${generated}/${name}.c")
    else()
        list(APPEND bwa_sources "${bwa_SOURCE_DIR}/${name}.c")
    endif()
endforeach()

file(READ seeding_types.h seeding_types)
file(READ seeding.metal seeding_source)
file(READ rescue_types.h rescue_types)
file(READ rescue.metal rescue_source)
file(READ extend_types.h extend_types)
file(READ extend.metal extend_source)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/extend_types.h" "${CMAKE_CURRENT_SOURCE_DIR}/extend.metal"
    "${CMAKE_CURRENT_SOURCE_DIR}/seeding_types.h" "${CMAKE_CURRENT_SOURCE_DIR}/seeding.metal"
    "${CMAKE_CURRENT_SOURCE_DIR}/rescue_types.h" "${CMAKE_CURRENT_SOURCE_DIR}/rescue.metal")
configure_file(seeding_source.h.in "${generated}/seeding_source.h" @ONLY)

# The CPU backend's interleaved seeding runs each read as a C++20 coroutine.
add_library(wgse_bwa_smem OBJECT smem_batch.cpp)
set_target_properties(wgse_bwa_smem PROPERTIES CXX_STANDARD 20)
target_include_directories(wgse_bwa_smem PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}" "${generated}")
target_include_directories(wgse_bwa_smem SYSTEM PRIVATE "${bwa_SOURCE_DIR}")
target_compile_options(wgse_bwa_smem PRIVATE -Wall -Wextra -Werror)

# bwa-mem3's rescue-window pruning behind wgse-bwa-mem2's adapter
# (../bwa-mem2/wgse_rescue_prune.h), compiled apart: bwa-mem3's source
# directory has headers named like BWA's.
add_library(wgse_bwa_rescue_prune STATIC ../bwa-mem2/rescue_prune.cpp)
target_include_directories(wgse_bwa_rescue_prune SYSTEM PRIVATE "${mem3_SOURCE_DIR}/src")
target_compile_options(wgse_bwa_rescue_prune PRIVATE -Wall -Wextra -Werror)

# BWA plus the GPU scheduling and its devices, shared by the executable and the tests.
set(device_sources)
if(APPLE)
    list(APPEND device_sources metal_device.mm)
elseif(WGSE_CUDA)
    list(APPEND device_sources cuda_device.cu)
endif()
add_library(wgse_bwa_core STATIC ${bwa_sources} backend.cpp ${device_sources}
    $<TARGET_OBJECTS:wgse_bwa_smem>)
target_include_directories(wgse_bwa_core PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}" "${generated}")
target_include_directories(wgse_bwa_core SYSTEM PUBLIC "${bwa_SOURCE_DIR}")
target_compile_definitions(wgse_bwa_core PRIVATE
    $<$<COMPILE_LANGUAGE:C>:HAVE_PTHREAD USE_MALLOC_WRAPPERS
    PACKAGE_VERSION="0.7.19-r1273-wgse-${WGSE_BWA_VERSION}">)
target_compile_options(wgse_bwa_core PRIVATE
    $<$<COMPILE_LANGUAGE:C>:-Wno-unused-function -Wno-unused-but-set-variable
    $<$<C_COMPILER_ID:Clang,AppleClang>:-Wno-deprecated-non-prototype>>
    $<$<COMPILE_LANGUAGE:CXX>:-Wall -Wextra -Werror>
    $<$<COMPILE_LANGUAGE:OBJCXX>:-fobjc-arc -Wall -Wextra -Werror>)
target_link_libraries(wgse_bwa_core PUBLIC ZLIB::ZLIB Threads::Threads m wgse_bwa_rescue_prune)
if(NOT APPLE)
    # shm_open, as upstream's Makefile links on Linux (older glibc keeps it in librt).
    target_link_libraries(wgse_bwa_core PUBLIC rt)
endif()
if(APPLE)
    target_compile_definitions(wgse_bwa_core PRIVATE WGSE_HAVE_METAL=1)
    target_link_libraries(wgse_bwa_core PUBLIC wgse_metal_common)
elseif(WGSE_CUDA)
    target_compile_definitions(wgse_bwa_core PRIVATE WGSE_HAVE_CUDA=1)
    target_include_directories(wgse_bwa_core PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/../gpu-common")
    target_compile_options(wgse_bwa_core PRIVATE
        $<$<COMPILE_LANGUAGE:CUDA>:--Werror=all-warnings -Xcompiler=-Wall,-Wextra>)
    # The static runtime needs only the NVIDIA driver at run time; without one,
    # the CUDA backend reports itself unavailable.
    target_link_libraries(wgse_bwa_core PUBLIC CUDA::cudart_static)
endif()

add_executable(wgse-bwa main.cpp)
target_compile_definitions(wgse-bwa PRIVATE WGSE_VERSION="${WGSE_BWA_VERSION}")
target_compile_options(wgse-bwa PRIVATE -Wall -Wextra -Werror)
target_link_libraries(wgse-bwa PRIVATE wgse_bwa_core)

if(BUILD_TESTING)
    # Exercise the shipped parser too: benchmark flags must not hide public options.
    add_executable(wgse-bwa-release-cli main.cpp)
    target_compile_definitions(wgse-bwa-release-cli PRIVATE WGSE_VERSION="${WGSE_BWA_VERSION}")
    target_compile_options(wgse-bwa-release-cli PRIVATE -Wall -Wextra -Werror)
    target_link_libraries(wgse-bwa-release-cli PRIVATE wgse_bwa_core)
    add_test(NAME bwa_release_dense_sa
        COMMAND "${PROJECT_SOURCE_DIR}/tests/smoke.sh"
        $<TARGET_FILE:wgse-bwa-release-cli> "${CMAKE_CURRENT_BINARY_DIR}/release-dense-sa" dense-sa)
    # Hidden routing and failure-injection flags for tests and benchmarks only.
    target_compile_definitions(wgse-bwa PRIVATE WGSE_METAL_BENCHMARK_TOOLS=1)
    # Bounds-checked libc++ containers make out-of-range indexing in the backend trap.
    target_compile_definitions(wgse_bwa_core PRIVATE
        $<$<COMPILE_LANGUAGE:CXX,OBJCXX>:_LIBCPP_HARDENING_MODE=_LIBCPP_HARDENING_MODE_FAST
        _GLIBCXX_ASSERTIONS>)
    add_test(NAME bwa_capabilities COMMAND wgse-bwa capabilities --json)
    set_tests_properties(bwa_capabilities PROPERTIES
        PASS_REGULAR_EXPRESSION "\"engine\":\"bwa\".*\"metal_hardware_supported\":.*\"cuda_available\":.*\"cuda_hardware_supported\":")
    add_test(NAME bwa_smoke COMMAND "${PROJECT_SOURCE_DIR}/tests/smoke.sh"
        $<TARGET_FILE:wgse-bwa> "${CMAKE_CURRENT_BINARY_DIR}/smoke" force-gpu)
    if(APPLE)
        add_executable(seeding_benchmark tests/seeding_benchmark.mm)
        target_compile_options(seeding_benchmark PRIVATE -fobjc-arc -Wall -Wextra -Werror)
        target_link_libraries(seeding_benchmark PRIVATE wgse_bwa_core)
        add_test(NAME bwa_metal_seeding_parity
            COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tests/seeding_parity.sh"
            $<TARGET_FILE:wgse-bwa> $<TARGET_FILE:seeding_benchmark>
            "${CMAKE_CURRENT_BINARY_DIR}/seeding-parity")
        set_tests_properties(bwa_metal_seeding_parity PROPERTIES SKIP_RETURN_CODE 77)
    else()
        # Without the Metal benchmark ("-"), the aligner's output is checked.
        add_test(NAME bwa_gpu_seeding_parity
            COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tests/seeding_parity.sh"
            $<TARGET_FILE:wgse-bwa> - "${CMAKE_CURRENT_BINARY_DIR}/seeding-parity")
        set_tests_properties(bwa_gpu_seeding_parity PROPERTIES SKIP_RETURN_CODE 77)
    endif()
    add_executable(ksw_batch_check tests/ksw_batch_check.c)
    target_compile_options(ksw_batch_check PRIVATE -Wall -Wextra -Werror)
    target_link_libraries(ksw_batch_check PRIVATE wgse_bwa_core)
    add_test(NAME bwa_ksw_batch COMMAND ksw_batch_check)
    add_executable(occ_check tests/occ_check.c)
    target_compile_options(occ_check PRIVATE -Wall -Wextra -Werror)
    target_link_libraries(occ_check PRIVATE wgse_bwa_core)
    add_test(NAME bwa_occ COMMAND occ_check)
    add_executable(extend_neon_check tests/extend_neon_check.c)
    target_compile_options(extend_neon_check PRIVATE -Wall -Wextra -Werror)
    target_link_libraries(extend_neon_check PRIVATE wgse_bwa_core)
    add_test(NAME bwa_extend_neon COMMAND extend_neon_check 200000)
    add_test(NAME bwa_gpu_rescue_parity
        COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tests/rescue_parity.sh"
        $<TARGET_FILE:wgse-bwa> "${CMAKE_CURRENT_BINARY_DIR}/rescue-parity")
    add_test(NAME bwa_metal_watchdog
        COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tests/rescue_parity.sh"
        $<TARGET_FILE:wgse-bwa> "${CMAKE_CURRENT_BINARY_DIR}/watchdog" watchdog)
    add_test(NAME bwa_gpu_extend_parity
        COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tests/extend_parity.sh"
        $<TARGET_FILE:wgse-bwa> "${CMAKE_CURRENT_BINARY_DIR}/extend-parity")
    # Exit 77 means the machine has no unified-memory Metal device.
    set_tests_properties(bwa_smoke bwa_gpu_rescue_parity bwa_metal_watchdog
        bwa_gpu_extend_parity PROPERTIES SKIP_RETURN_CODE 77)
endif()

install(TARGETS wgse-bwa RUNTIME DESTINATION bin)
install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/../../LICENSE" DESTINATION share/licenses/wgse-bwa
    RENAME WGSE-LICENSE)
install(FILES "${bwa_SOURCE_DIR}/COPYING" DESTINATION share/licenses/wgse-bwa
    RENAME BWA-LICENSE)
install(FILES SEEDING-LICENSE DESTINATION share/licenses/wgse-bwa
    RENAME METAL-SEEDING-LICENSE)
