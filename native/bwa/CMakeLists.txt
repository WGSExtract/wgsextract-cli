find_package(ZLIB REQUIRED)
find_package(Threads REQUIRED)

FetchContent_Declare(bwa
    URL https://github.com/lh3/bwa/archive/refs/tags/v0.7.19.tar.gz
    URL_HASH SHA256=cdff5db67652c5b805a3df08c4e813a822c65791913eccfb3cf7d528588f37bc
    SOURCE_SUBDIR unused)
FetchContent_MakeAvailable(bwa)

# Mirrors upstream's Makefile LOBJS, AOBJS and main.o.
set(bwa_names
    utils kthread kstring ksw bwt bntseq bwa bwamem bwamem_pair bwamem_extra
    malloc_wrap QSufSort bwt_gen rope rle is bwtindex
    bwashm bwase bwaseqio bwtgap bwtaln bamlite bwape kopen pemerge maxk
    bwtsw2_core bwtsw2_main bwtsw2_aux bwt_lite bwtsw2_chain fastmap bwtsw2_pair
    main)
foreach(name IN LISTS bwa_names)
    list(APPEND bwa_sources "${bwa_SOURCE_DIR}/${name}.c")
endforeach()
add_executable(wgse-bwa ${bwa_sources})
target_compile_definitions(wgse-bwa PRIVATE HAVE_PTHREAD USE_MALLOC_WRAPPERS
    PACKAGE_VERSION="0.7.19-r1273-wgse-${PROJECT_VERSION}")
target_compile_options(wgse-bwa PRIVATE -Wno-unused-function -Wno-unused-but-set-variable
    -Wno-deprecated-non-prototype)
target_link_libraries(wgse-bwa PRIVATE ZLIB::ZLIB Threads::Threads m)

if(BUILD_TESTING)
    add_test(NAME bwa_smoke COMMAND "${PROJECT_SOURCE_DIR}/tests/smoke.sh"
        $<TARGET_FILE:wgse-bwa> "${CMAKE_CURRENT_BINARY_DIR}/smoke")

    # The oracle includes upstream bwamem.c to expose its otherwise-static
    # mem_collect_intv. No patches or instrumentation enter the release aligner.
    set(oracle_sources ${bwa_sources})
    list(REMOVE_ITEM oracle_sources "${bwa_SOURCE_DIR}/main.c"
        "${bwa_SOURCE_DIR}/bwamem.c")
    add_library(bwa_seed_oracle STATIC ${oracle_sources} seed_oracle.c)
    set_source_files_properties(seed_oracle.c PROPERTIES
        COMPILE_OPTIONS "-Wall;-Wextra;-Werror")
    target_include_directories(bwa_seed_oracle SYSTEM PUBLIC "${bwa_SOURCE_DIR}")
    target_include_directories(bwa_seed_oracle PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
    target_compile_definitions(bwa_seed_oracle PRIVATE HAVE_PTHREAD USE_MALLOC_WRAPPERS
        PACKAGE_VERSION="0.7.19-r1273-wgse-${PROJECT_VERSION}")
    target_compile_options(bwa_seed_oracle PRIVATE -Wno-unused-function
        -Wno-unused-but-set-variable -Wno-deprecated-non-prototype)
    target_link_libraries(bwa_seed_oracle PUBLIC ZLIB::ZLIB Threads::Threads m)
    file(READ "${CMAKE_CURRENT_SOURCE_DIR}/seeding_types.h" seeding_types)
    file(READ "${CMAKE_CURRENT_SOURCE_DIR}/seeding.metal" seeding_source)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
        "${CMAKE_CURRENT_SOURCE_DIR}/seeding_types.h"
        "${CMAKE_CURRENT_SOURCE_DIR}/seeding.metal")
    configure_file(seeding_source.h.in generated/seeding_source.h @ONLY)
    add_executable(seeding_benchmark seeding_benchmark.mm)
    target_include_directories(seeding_benchmark PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated")
    target_compile_options(seeding_benchmark PRIVATE -fobjc-arc -Wall -Wextra -Werror)
    target_link_libraries(seeding_benchmark PRIVATE bwa_seed_oracle wgse_metal_common)
    add_test(NAME metal_seeding_parity COMMAND sh
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/seeding_parity.sh"
        $<TARGET_FILE:wgse-bwa> $<TARGET_FILE:seeding_benchmark>
        "${CMAKE_CURRENT_BINARY_DIR}/seeding-parity")
    set_tests_properties(metal_seeding_parity PROPERTIES SKIP_RETURN_CODE 77)
endif()

install(TARGETS wgse-bwa RUNTIME DESTINATION bin)
install(FILES "${bwa_SOURCE_DIR}/COPYING" DESTINATION share/licenses/wgse-bwa
    RENAME BWA-LICENSE)
