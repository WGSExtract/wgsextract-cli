# Writes the SPIR-V module INPUT as `static const uint32_t NAME[]` in the header OUTPUT.
file(READ "${INPUT}" bytes HEX)
string(LENGTH "${bytes}" length)
math(EXPR remainder "${length} % 8")
if(length EQUAL 0 OR NOT remainder EQUAL 0)
    message(FATAL_ERROR "${INPUT} is not a SPIR-V module")
endif()
# SPIR-V words are little-endian.
string(REGEX REPLACE "(..)(..)(..)(..)" "0x\\4\\3\\2\\1u," words "${bytes}")
string(REGEX REPLACE "((0x[0-9a-f]+u,){8})" "\\1\n    " words "${words}")
file(WRITE "${OUTPUT}" "// Generated from ${INPUT} by embed_spirv.cmake.\n#pragma once\n#include <cstdint>\n\nstatic const uint32_t ${NAME}[] = {\n    ${words}\n};\n")
