# wgse-deepvariant: a self-contained Apple Silicon build of DeepVariant 1.10.0.
#
# Builds the native macOS port of DeepVariant (IPNP-BIPN/deepvariant,
# feature/apple-silicon-native-v2) with the patches in patches/, which make
# its WGS make_examples match google/deepvariant:1.10.0. Every dependency is
# built here as a static library, so the binary links only macOS system
# libraries and frameworks. See README.md.
cmake_minimum_required(VERSION 3.27)
set(CMAKE_OSX_DEPLOYMENT_TARGET 14.0 CACHE STRING "Minimum macOS version")
project(wgse_deepvariant VERSION 0.1.0 LANGUAGES C CXX)

if(NOT APPLE OR NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(arm64|aarch64)$")
    message(FATAL_ERROR "wgse-deepvariant requires Apple Silicon macOS")
endif()
include(ExternalProject)
cmake_host_system_information(RESULT cores QUERY NUMBER_OF_LOGICAL_CORES)

# Bump when the wgse-deepvariant package changes; versioned separately from DeepVariant.
set(WGSE_DEEPVARIANT_VERSION 0.1.0)
set(PORT_COMMIT 0b7b9c57ad84c55918fc0429dacf16f943808c0b)
set(deps "${CMAKE_BINARY_DIR}/deps")
set(common_args
    -DCMAKE_BUILD_TYPE=Release
    -DCMAKE_INSTALL_PREFIX=${deps}
    -DCMAKE_PREFIX_PATH=${deps}
    -DCMAKE_OSX_DEPLOYMENT_TARGET=${CMAKE_OSX_DEPLOYMENT_TARGET}
    -DCMAKE_OSX_ARCHITECTURES=arm64
    -DCMAKE_CXX_STANDARD=17
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
    -DBUILD_SHARED_LIBS=OFF
    -DBUILD_TESTING=OFF)
set(autotools_env
    "CC=${CMAKE_C_COMPILER}"
    "CFLAGS=-O2 -arch arm64 -mmacosx-version-min=${CMAKE_OSX_DEPLOYMENT_TARGET}")

ExternalProject_Add(abseil
    URL https://github.com/abseil/abseil-cpp/archive/refs/tags/20260817.0.tar.gz
    URL_HASH SHA256=f7e05179df39c45434cad433f5783840bb3788ef322976f9138bc6b72b3a107d
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CMAKE_ARGS ${common_args} -DABSL_PROPAGATE_CXX_STD=ON -DABSL_BUILD_TESTING=OFF)

ExternalProject_Add(protobuf
    URL https://github.com/protocolbuffers/protobuf/releases/download/v36.2/protobuf-36.2.tar.gz
    URL_HASH SHA256=3d9642a662d10e68ebae5e53f14dcce5105684212d5078f8e0d47d1ab3ae6b64
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CMAKE_ARGS ${common_args} -Dprotobuf_ABSL_PROVIDER=package -Dprotobuf_BUILD_TESTS=OFF
        -Dprotobuf_BUILD_CONFORMANCE=OFF -Dprotobuf_BUILD_EXAMPLES=OFF -Dprotobuf_INSTALL=ON
    DEPENDS abseil)

ExternalProject_Add(re2
    URL https://github.com/google/re2/archive/refs/tags/2025-11-05.tar.gz
    URL_HASH SHA256=87f6029d2f6de8aa023654240a03ada90e876ce9a4676e258dd01ea4c26ffd67
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CMAKE_ARGS ${common_args} -DRE2_BUILD_TESTING=OFF
    DEPENDS abseil)

ExternalProject_Add(libdeflate
    URL https://github.com/ebiggers/libdeflate/archive/refs/tags/v1.26.tar.gz
    URL_HASH SHA256=bba03fffc5538576213675ce6968fcff6ce2e67d82e4d5febea2d05f9f13cf85
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CMAKE_ARGS ${common_args} -DLIBDEFLATE_BUILD_SHARED_LIB=OFF -DLIBDEFLATE_BUILD_GZIP=OFF)

# liblzma, for CRAM's LZMA codec (macOS has the library but not its header).
# libcurl and libbz2 are macOS's own; libcurl lets DeepVariant read https:// BAMs.
ExternalProject_Add(xz
    URL https://github.com/tukaani-project/xz/releases/download/v5.8.1/xz-5.8.1.tar.gz
    URL_HASH SHA256=507825b599356c10dca1cd720c9d0d0c9d5400b9de300af00e4d1ea150795543
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CMAKE_ARGS ${common_args} -DXZ_NLS=OFF -DXZ_TOOL_XZ=OFF -DXZ_TOOL_XZDEC=OFF
        -DXZ_TOOL_LZMADEC=OFF -DXZ_TOOL_LZMAINFO=OFF -DXZ_TOOL_SCRIPTS=OFF -DXZ_DOC=OFF)

ExternalProject_Add(htslib
    URL https://github.com/samtools/htslib/releases/download/1.25/htslib-1.25.tar.bz2
    URL_HASH SHA256=158c271e4e2986bb2dd11283b640edc10c56695b293df77a109ce99480303730
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    BUILD_IN_SOURCE TRUE
    CONFIGURE_COMMAND ./configure --prefix=${deps} ${autotools_env}
        "CPPFLAGS=-I${deps}/include" "LDFLAGS=-L${deps}/lib"
        --with-libdeflate --enable-bz2 --enable-lzma --enable-libcurl --disable-gcs
        --disable-s3 --disable-plugins
    BUILD_COMMAND make -j4 lib-static
    # The static library and headers only, so nothing can pick up a dylib.
    INSTALL_COMMAND ${CMAKE_COMMAND} -E copy <SOURCE_DIR>/libhts.a ${deps}/lib/libhts.a
        COMMAND ${CMAKE_COMMAND} -E copy_directory <SOURCE_DIR>/htslib ${deps}/include/htslib
    DEPENDS libdeflate xz)

# Boost: header-only use (graph, interprocess, math, process headers).
ExternalProject_Add(boost
    URL https://archives.boost.io/release/1.92.0/source/boost_1_92_0.tar.bz2
    URL_HASH SHA256=5c1d40cb8e19adbf740a4ec2da35b3e58f3f5804b1dce44deb53df72193cbc6c
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    CONFIGURE_COMMAND ""
    BUILD_COMMAND ""
    INSTALL_COMMAND ${CMAKE_COMMAND} -E copy_directory <SOURCE_DIR>/boost ${deps}/include/boost)

file(GLOB port_patches CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/patches/*.patch")
list(SORT port_patches)
set(apply_patches "")
foreach(p IN LISTS port_patches)
    list(APPEND apply_patches COMMAND patch -p1 --forward --batch -i ${p})
endforeach()
list(POP_FRONT apply_patches)  # PATCH_COMMAND supplies the first COMMAND itself.
# A fresh source tree whenever the patches change.
set(patch_text "")
foreach(p IN LISTS port_patches)
    file(READ ${p} text)
    string(APPEND patch_text "${text}")
endforeach()
string(SHA256 patch_hash "${patch_text}")
string(SUBSTRING ${patch_hash} 0 12 patch_hash)

ExternalProject_Add(port
    URL https://github.com/IPNP-BIPN/deepvariant/archive/${PORT_COMMIT}.tar.gz
    URL_HASH SHA256=a66c61c564752faf58fba086d0ff971124d4c6cfa66555b0ef0746003e341d9b
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    SOURCE_DIR ${CMAKE_BINARY_DIR}/port-src-${patch_hash}
    BINARY_DIR ${CMAKE_BINARY_DIR}/port-build-${patch_hash}
    PATCH_COMMAND ${apply_patches}
    CMAKE_ARGS ${common_args} -DWGSE_DEPS_PREFIX=${deps}
        -DDV_VERSION=${WGSE_DEEPVARIANT_VERSION} -DDV_SOURCE_ID=${PORT_COMMIT}+wgse
        -DFETCHCONTENT_BASE_DIR=${CMAKE_BINARY_DIR}/port-deps
    BUILD_COMMAND ${CMAKE_COMMAND} --build <BINARY_DIR> --target deepvariant --parallel ${cores}
    INSTALL_COMMAND ""
    DEPENDS abseil protobuf re2 htslib boost)
ExternalProject_Get_Property(port SOURCE_DIR BINARY_DIR)
install(PROGRAMS "${BINARY_DIR}/bin/deepvariant" DESTINATION bin RENAME wgse-deepvariant)

set(licenses share/licenses/wgse-deepvariant)
install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/../../LICENSE" DESTINATION ${licenses}
    RENAME WGSE-LICENSE)
# Google's DeepVariant and the port share this BSD-3-Clause licence; Google
# distributes the 1.10.0 models (converted into the weight assets) under it too.
install(FILES "${SOURCE_DIR}/LICENSE" DESTINATION ${licenses} RENAME DeepVariant-LICENSE)
# libssw (MIT; its licence is in its README) and sse2neon, which the port builds in.
install(FILES "${CMAKE_BINARY_DIR}/port-deps/libssw-src/README.md" DESTINATION ${licenses}
    RENAME libssw-LICENSE)
install(FILES "${SOURCE_DIR}/release/vendored/sse2neon.h" DESTINATION ${licenses}
    RENAME sse2neon-LICENSE)
foreach(dep_file IN ITEMS abseil:LICENSE protobuf:LICENSE re2:LICENSE htslib:LICENSE
        htslib:htscodecs/LICENSE.md libdeflate:COPYING xz:COPYING xz:COPYING.0BSD
        boost:LICENSE_1_0.txt)
    string(REPLACE ":" ";" parts "${dep_file}")
    list(GET parts 0 dep)
    list(GET parts 1 file)
    ExternalProject_Get_Property(${dep} SOURCE_DIR)
    string(REGEX REPLACE "^htscodecs/" "htscodecs-" name "${file}")
    install(FILES "${SOURCE_DIR}/${file}" DESTINATION ${licenses} RENAME ${dep}-${name})
endforeach()
