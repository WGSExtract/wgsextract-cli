#pragma once

// _mm_movemask_epi8() for the band tests of BWA-MEM2's 128-bit banded
// Smith-Waterman kernels, which only compare the mask with zero or with all
// lanes set. sse2neon builds the full mask in about seven instructions; one
// NEON horizontal minimum and maximum give the same answers.
#include "sse2neon.h"

// _mm_movemask_epi8(m): 0xFFFF if every byte's top bit is set, 0 if none is,
// otherwise 1 (a partial mask, which no comparison distinguishes further).
static inline uint16_t wgse_lanes8(__m128i m)
{
    const uint8x16_t v = vreinterpretq_u8_m128i(m);
    return vminvq_u8(v) >= 0x80 ? 0xFFFF : vmaxvq_u8(v) < 0x80 ? 0 : 1;
}

// _mm_movemask_epi8(m) & 0xAAAA: the top bit of each 16-bit lane.
static inline uint16_t wgse_lanes16(__m128i m)
{
    const uint16x8_t v = vreinterpretq_u16_m128i(m);
    return vminvq_u16(v) >= 0x8000 ? 0xAAAA : vmaxvq_u16(v) < 0x8000 ? 0 : 2;
}
