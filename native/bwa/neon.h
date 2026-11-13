/* The NEON intrinsics of extend_neon.c.in and pac_oracle.c.in:
 * <arm_neon.h> on AArch64, and on x86-64 the same operations in SSE2 (which
 * every x86-64 CPU has), so the exact CPU replacements run there too.
 * WGSE_NEON is defined when either is available. Only the intrinsics those
 * files use are defined; each has NEON's exact lane semantics. */
#pragma once

#if defined(__ARM_NEON)
#include <arm_neon.h>
#define WGSE_NEON 1
#elif defined(__x86_64__) && defined(__SSE2__)
#include <emmintrin.h>
#include <stdint.h>
#define WGSE_NEON 1

typedef int16_t int16x8_t __attribute__((vector_size(16)));
typedef uint16_t uint16x8_t __attribute__((vector_size(16)));
typedef uint8_t uint8x16_t __attribute__((vector_size(16)));
typedef struct { uint8x16_t val[4]; } uint8x16x4_t;

#define WGSE_X86 static inline __attribute__((always_inline))
#define WGSE_M(v) ((__m128i)(v))

WGSE_X86 int16x8_t vdupq_n_s16(int16_t x) { return (int16x8_t)_mm_set1_epi16(x); }
WGSE_X86 int16x8_t vld1q_s16(const int16_t *p) { return (int16x8_t)_mm_loadu_si128((const __m128i *)p); }
WGSE_X86 void vst1q_s16(int16_t *p, int16x8_t v) { _mm_storeu_si128((__m128i *)p, WGSE_M(v)); }
WGSE_X86 int16x8_t vaddq_s16(int16x8_t a, int16x8_t b) { return (int16x8_t)_mm_add_epi16(WGSE_M(a), WGSE_M(b)); }
WGSE_X86 int16x8_t vsubq_s16(int16x8_t a, int16x8_t b) { return (int16x8_t)_mm_sub_epi16(WGSE_M(a), WGSE_M(b)); }
WGSE_X86 int16x8_t vqsubq_s16(int16x8_t a, int16x8_t b) { return (int16x8_t)_mm_subs_epi16(WGSE_M(a), WGSE_M(b)); }
WGSE_X86 int16x8_t vmaxq_s16(int16x8_t a, int16x8_t b) { return (int16x8_t)_mm_max_epi16(WGSE_M(a), WGSE_M(b)); }
WGSE_X86 int16x8_t vandq_s16(int16x8_t a, int16x8_t b) { return (int16x8_t)_mm_and_si128(WGSE_M(a), WGSE_M(b)); }
WGSE_X86 int16x8_t vmulq_n_s16(int16x8_t a, int16_t b) { return (int16x8_t)_mm_mullo_epi16(WGSE_M(a), _mm_set1_epi16(b)); }
WGSE_X86 int16x8_t vreinterpretq_s16_u16(uint16x8_t a) { return (int16x8_t)a; }
WGSE_X86 uint16x8_t vceqq_s16(int16x8_t a, int16x8_t b) { return (uint16x8_t)_mm_cmpeq_epi16(WGSE_M(a), WGSE_M(b)); }
/* a >= b: not b > a */
WGSE_X86 uint16x8_t vcgeq_s16(int16x8_t a, int16x8_t b) { return ~(uint16x8_t)_mm_cmpgt_epi16(WGSE_M(b), WGSE_M(a)); }
/* (a & b) != 0 */
WGSE_X86 uint16x8_t vtstq_s16(int16x8_t a, int16x8_t b)
{
	return ~(uint16x8_t)_mm_cmpeq_epi16(_mm_and_si128(WGSE_M(a), WGSE_M(b)), _mm_setzero_si128());
}
/* Bitwise select: mask's bits from a, the others from b. */
WGSE_X86 int16x8_t vbslq_s16(uint16x8_t mask, int16x8_t a, int16x8_t b)
{
	return (int16x8_t)_mm_or_si128(_mm_and_si128(WGSE_M(mask), WGSE_M(a)), _mm_andnot_si128(WGSE_M(mask), WGSE_M(b)));
}
#define vgetq_lane_s16(a, n) ((int16_t)(a)[n])
#define vdupq_laneq_s16(a, n) __builtin_shufflevector((a), (a), n, n, n, n, n, n, n, n)
/* The last 8 - n lanes of a, then the first n of b. */
#define vextq_s16(a, b, n) __builtin_shufflevector((a), (b), \
	(n), (n) + 1, (n) + 2, (n) + 3, (n) + 4, (n) + 5, (n) + 6, (n) + 7)
WGSE_X86 int16_t vmaxvq_s16(int16x8_t a)
{
	a = vmaxq_s16(a, __builtin_shufflevector(a, a, 4, 5, 6, 7, 0, 1, 2, 3));
	a = vmaxq_s16(a, __builtin_shufflevector(a, a, 2, 3, 0, 1, 2, 3, 0, 1));
	a = vmaxq_s16(a, __builtin_shufflevector(a, a, 1, 0, 1, 0, 1, 0, 1, 0));
	return a[0];
}

WGSE_X86 uint8x16_t vdupq_n_u8(uint8_t x) { return (uint8x16_t)_mm_set1_epi8((char)x); }
WGSE_X86 uint8x16_t vld1q_u8(const uint8_t *p) { return (uint8x16_t)_mm_loadu_si128((const __m128i *)p); }
WGSE_X86 void vst1q_u8(uint8_t *p, uint8x16_t v) { _mm_storeu_si128((__m128i *)p, WGSE_M(v)); }
WGSE_X86 uint8x16_t vandq_u8(uint8x16_t a, uint8x16_t b) { return (uint8x16_t)_mm_and_si128(WGSE_M(a), WGSE_M(b)); }
WGSE_X86 uint8x16_t veorq_u8(uint8x16_t a, uint8x16_t b) { return (uint8x16_t)_mm_xor_si128(WGSE_M(a), WGSE_M(b)); }
#define vshrq_n_u8(a, n) ((uint8x16_t)(a) >> (n))
/* Bytes reversed within each 8-byte half. */
#define vrev64q_u8(a) __builtin_shufflevector((a), (a), 7, 6, 5, 4, 3, 2, 1, 0, 15, 14, 13, 12, 11, 10, 9, 8)
#define vextq_u8(a, b, n) __builtin_shufflevector((a), (b), (n), (n) + 1, (n) + 2, (n) + 3, (n) + 4, \
	(n) + 5, (n) + 6, (n) + 7, (n) + 8, (n) + 9, (n) + 10, (n) + 11, (n) + 12, (n) + 13, (n) + 14, (n) + 15)
/* Stores lane i of val[j] at p[4 * i + j]. */
WGSE_X86 void vst4q_u8(uint8_t *p, uint8x16x4_t v)
{
	const __m128i ab_lo = _mm_unpacklo_epi8(WGSE_M(v.val[0]), WGSE_M(v.val[1]));
	const __m128i ab_hi = _mm_unpackhi_epi8(WGSE_M(v.val[0]), WGSE_M(v.val[1]));
	const __m128i cd_lo = _mm_unpacklo_epi8(WGSE_M(v.val[2]), WGSE_M(v.val[3]));
	const __m128i cd_hi = _mm_unpackhi_epi8(WGSE_M(v.val[2]), WGSE_M(v.val[3]));
	_mm_storeu_si128((__m128i *)p, _mm_unpacklo_epi16(ab_lo, cd_lo));
	_mm_storeu_si128((__m128i *)p + 1, _mm_unpackhi_epi16(ab_lo, cd_lo));
	_mm_storeu_si128((__m128i *)p + 2, _mm_unpacklo_epi16(ab_hi, cd_hi));
	_mm_storeu_si128((__m128i *)p + 3, _mm_unpackhi_epi16(ab_hi, cd_hi));
}
#undef WGSE_M
#undef WGSE_X86
#endif
