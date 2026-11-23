find_package(ZLIB REQUIRED)
find_package(Threads REQUIRED)

# clang-cl's -Wall is -Weverything; /W4 is clang's -Wall -Wextra.
if(MSVC)
    set(wgse_strict_warnings /W4 /WX)
else()
    set(wgse_strict_warnings -Wall -Wextra -Werror)
endif()

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
if(WIN32)
    # The Windows bwa package's patch (64-bit file offsets, no POSIX pipes or
    # shared memory): without it the human index fails to load ([ftell]).
    file(READ "${bwa_SOURCE_DIR}/utils.h" bwa_utils_header)
    if(NOT bwa_utils_header MATCHES "int64_t err_ftell")
        message(FATAL_ERROR "${bwa_SOURCE_DIR} lacks packaging/windows/recipes/bwa/patches/"
            "0001-windows-native-build.patch: point FETCHCONTENT_SOURCE_DIR_BWA at a patched copy")
    endif()
endif()

# Bump when the wgse-bwa package changes; it is versioned separately from BWA.
set(WGSE_BWA_VERSION 0.14.1)
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

# POSIX for BWA on Windows: these shims, then packaging/windows's MinGW ones
# (drand48, getrusage, binary I/O), as the Windows bwa package has.
add_library(wgse_bwa_platform INTERFACE)
if(WIN32)
    set(windows_compat "${CMAKE_CURRENT_SOURCE_DIR}/../../packaging/windows/compat")
    target_include_directories(wgse_bwa_platform BEFORE INTERFACE
        "${CMAKE_CURRENT_SOURCE_DIR}/../windows/include" "${windows_compat}/include")
    target_compile_definitions(wgse_bwa_platform INTERFACE
        $<$<COMPILE_LANGUAGE:C,CXX>:_CRT_SECURE_NO_WARNINGS _CRT_NONSTDC_NO_DEPRECATE _USE_MATH_DEFINES>)
    target_compile_options(wgse_bwa_platform INTERFACE $<$<COMPILE_LANGUAGE:C,CXX>:/FIwgse_msvc.h>)
    target_link_libraries(wgse_bwa_platform INTERFACE psapi)
endif()

# The CPU backend's interleaved seeding runs each read as a C++20 coroutine.
add_library(wgse_bwa_smem OBJECT smem_batch.cpp)
set_target_properties(wgse_bwa_smem PROPERTIES CXX_STANDARD 20)
target_include_directories(wgse_bwa_smem PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}" "${generated}")
target_include_directories(wgse_bwa_smem SYSTEM PRIVATE "${bwa_SOURCE_DIR}")
target_compile_options(wgse_bwa_smem PRIVATE ${wgse_strict_warnings})
target_link_libraries(wgse_bwa_smem PRIVATE ZLIB::ZLIB wgse_bwa_platform)

# bwa-mem3's rescue-window pruning behind wgse-bwa-mem2's adapter
# (../bwa-mem2/wgse_rescue_prune.h), compiled apart: bwa-mem3's source
# directory has headers named like BWA's.
add_library(wgse_bwa_rescue_prune STATIC ../bwa-mem2/rescue_prune.cpp)
target_include_directories(wgse_bwa_rescue_prune SYSTEM PRIVATE "${mem3_SOURCE_DIR}/src")
target_compile_options(wgse_bwa_rescue_prune PRIVATE ${wgse_strict_warnings})
target_link_libraries(wgse_bwa_rescue_prune PRIVATE wgse_bwa_platform)

# BWA plus the GPU scheduling and its devices, shared by the executable and the tests.
set(device_sources)
if(APPLE)
    list(APPEND device_sources metal_device.mm)
elseif(WGSE_CUDA)
    list(APPEND device_sources cuda_device.cu)
endif()
if(WIN32)
    list(APPEND device_sources ../windows/msvc_compat.c "${windows_compat}/wgse_compat.c")
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
    $<$<COMPILE_LANGUAGE:CXX>:${wgse_strict_warnings}>
    $<$<COMPILE_LANGUAGE:OBJCXX>:-fobjc-arc -Wall -Wextra -Werror>)
target_link_libraries(wgse_bwa_core PUBLIC ZLIB::ZLIB Threads::Threads wgse_bwa_rescue_prune
    wgse_bwa_platform)
if(NOT WIN32)
    target_link_libraries(wgse_bwa_core PUBLIC m)
endif()
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
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
        $<$<COMPILE_LANGUAGE:CUDA>:--Werror=all-warnings
        $<IF:$<PLATFORM_ID:Windows>,-Xcompiler=/Zc:lambda,-Xcompiler=-Wall$<COMMA>-Wextra>>)
    # The static runtime needs only the NVIDIA driver at run time; without one,
    # the CUDA backend reports itself unavailable.
    target_link_libraries(wgse_bwa_core PUBLIC CUDA::cudart_static)
endif()

set(cli_sources main.cpp)
if(MSVC)
    # The classic heap can serialize CUDA mate-rescue workers at large -K.
    list(APPEND cli_sources windows-heap.manifest)
endif()
add_executable(wgse-bwa ${cli_sources})
target_compile_definitions(wgse-bwa PRIVATE WGSE_VERSION="${WGSE_BWA_VERSION}")
target_compile_options(wgse-bwa PRIVATE ${wgse_strict_warnings})
target_link_libraries(wgse-bwa PRIVATE wgse_bwa_core)

if(BUILD_TESTING)
    if(WIN32)
        # Native CTest cannot execute a shell script directly. Prefer Git Bash
        # to Windows' own bash.exe (a WSL launcher).
        find_package(Git REQUIRED)
        get_filename_component(git_bin "${GIT_EXECUTABLE}" DIRECTORY)
        find_program(WGSE_TEST_SHELL NAMES bash HINTS "${git_bin}/../bin" "${git_bin}"
            REQUIRED NO_DEFAULT_PATH)
    endif()
    # Exercise the shipped parser too: benchmark flags must not hide public options.
    add_executable(wgse-bwa-release-cli ${cli_sources})
    target_compile_definitions(wgse-bwa-release-cli PRIVATE WGSE_VERSION="${WGSE_BWA_VERSION}")
    target_compile_options(wgse-bwa-release-cli PRIVATE ${wgse_strict_warnings})
    target_link_libraries(wgse-bwa-release-cli PRIVATE wgse_bwa_core)
    add_test(NAME bwa_release_dense_sa
        COMMAND ${WGSE_TEST_SHELL} "${PROJECT_SOURCE_DIR}/tests/smoke.sh"
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
    add_test(NAME bwa_smoke COMMAND ${WGSE_TEST_SHELL} "${PROJECT_SOURCE_DIR}/tests/smoke.sh"
        $<TARGET_FILE:wgse-bwa> "${CMAKE_CURRENT_BINARY_DIR}/smoke" force-gpu)
    if(WGSE_CUDA)
        add_test(NAME bwa_cuda_cpu_fallback
            COMMAND ${WGSE_TEST_SHELL} "${PROJECT_SOURCE_DIR}/tests/smoke.sh"
            $<TARGET_FILE:wgse-bwa-release-cli> "${CMAKE_CURRENT_BINARY_DIR}/cuda-cpu-fallback"
            cuda-cpu-fallback)
        set_tests_properties(bwa_cuda_cpu_fallback PROPERTIES ENVIRONMENT "CUDA_VISIBLE_DEVICES=-1")
    endif()
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
            COMMAND ${WGSE_TEST_SHELL} "${CMAKE_CURRENT_SOURCE_DIR}/tests/seeding_parity.sh"
            $<TARGET_FILE:wgse-bwa> - "${CMAKE_CURRENT_BINARY_DIR}/seeding-parity")
        set_tests_properties(bwa_gpu_seeding_parity PROPERTIES SKIP_RETURN_CODE 77)
    endif()
    add_executable(ksw_batch_check tests/ksw_batch_check.c)
    target_compile_options(ksw_batch_check PRIVATE ${wgse_strict_warnings})
    target_link_libraries(ksw_batch_check PRIVATE wgse_bwa_core)
    add_test(NAME bwa_ksw_batch COMMAND ksw_batch_check)
    add_executable(occ_check tests/occ_check.cpp)
    target_compile_options(occ_check PRIVATE ${wgse_strict_warnings})
    target_link_libraries(occ_check PRIVATE wgse_bwa_core)
    add_test(NAME bwa_occ COMMAND occ_check)
    add_test(NAME bwa_occ_upstream COMMAND occ_check --cpu-upstream)
    add_executable(extend_neon_check tests/extend_neon_check.c)
    target_compile_options(extend_neon_check PRIVATE ${wgse_strict_warnings})
    target_link_libraries(extend_neon_check PRIVATE wgse_bwa_core)
    add_test(NAME bwa_extend_neon COMMAND extend_neon_check 200000)
    add_executable(pac_check tests/pac_check.c)
    target_compile_options(pac_check PRIVATE ${wgse_strict_warnings})
    target_link_libraries(pac_check PRIVATE wgse_bwa_core)
    add_test(NAME bwa_pac COMMAND pac_check)
    add_test(NAME bwa_gpu_rescue_parity
        COMMAND ${WGSE_TEST_SHELL} "${CMAKE_CURRENT_SOURCE_DIR}/tests/rescue_parity.sh"
        $<TARGET_FILE:wgse-bwa> "${CMAKE_CURRENT_BINARY_DIR}/rescue-parity")
    add_test(NAME bwa_metal_watchdog
        COMMAND ${WGSE_TEST_SHELL} "${CMAKE_CURRENT_SOURCE_DIR}/tests/rescue_parity.sh"
        $<TARGET_FILE:wgse-bwa> "${CMAKE_CURRENT_BINARY_DIR}/watchdog" watchdog)
    add_test(NAME bwa_gpu_extend_parity
        COMMAND ${WGSE_TEST_SHELL} "${CMAKE_CURRENT_SOURCE_DIR}/tests/extend_parity.sh"
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
install(FILES "${mem3_SOURCE_DIR}/LICENSE" DESTINATION share/licenses/wgse-bwa
    RENAME BWA-MEM3-LICENSE)
install(FILES SEEDING-LICENSE DESTINATION share/licenses/wgse-bwa
    RENAME METAL-SEEDING-LICENSE)
