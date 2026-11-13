// mg_lchain_dp's score fill (upstream lchain.c, now wgse_lchain_dp_fill) for one run of
// anchors (chain_engine.h), for the CPU (chain_host.cpp) and the GPU (chain_fill.cuh):
// upstream's loop with its float arithmetic rounded after each operation, as the CPU
// computes it without fused multiply-adds (lchain.c and chain_host.cpp are compiled with
// -ffp-contract=off), so every score is identical.
#pragma once

#include "chain_engine.h"

#include <climits>
#include <cstdint>
#include <cstring>

#ifdef __CUDACC__
#define WGSE_CHAIN_FN __host__ __device__ __forceinline__
#else
#define WGSE_CHAIN_FN inline
#endif

namespace wgse::kernels::chain {

using wgse::chain::Anchor;
using wgse::chain::Params;
using wgse::chain::Run;
using wgse::chain::Score;

#ifdef __CUDA_ARCH__
WGSE_CHAIN_FN float mul(float a, float b) { return __fmul_rn(a, b); }
WGSE_CHAIN_FN float add(float a, float b) { return __fadd_rn(a, b); }
WGSE_CHAIN_FN float to_float(int32_t x) { return __int2float_rn(x); }
WGSE_CHAIN_FN int32_t to_int(float x) { return __float2int_rz(x); }
WGSE_CHAIN_FN uint32_t bits(float x) { return __float_as_uint(x); }
WGSE_CHAIN_FN float from_bits(uint32_t x) { return __uint_as_float(x); }
#else
WGSE_CHAIN_FN float mul(float a, float b) { return a * b; }
WGSE_CHAIN_FN float add(float a, float b) { return a + b; }
WGSE_CHAIN_FN float to_float(int32_t x) { return (float)x; }
WGSE_CHAIN_FN int32_t to_int(float x) { return (int32_t)x; }
WGSE_CHAIN_FN uint32_t bits(float x) { uint32_t u; memcpy(&u, &x, 4); return u; }
WGSE_CHAIN_FN float from_bits(uint32_t x) { float f; memcpy(&f, &x, 4); return f; }
#endif

// mmpriv.h's mg_log2 (valid for x >= 2).
WGSE_CHAIN_FN float mg_log2(float x) {
    uint32_t i = bits(x);
    const float log_2 = to_float((int32_t)((i >> 23) & 255) - 128);
    i &= ~(255u << 23);
    i += 127u << 23;
    const float z = from_bits(i);
    return add(log_2, add(mul(add(mul(-0.34484843f, z), 2.02466578f), z), -0.67487759f));
}

// lchain.c's comput_sc for anchors of one read segment (n_seg == 1, so sidi == sidj)
// on one reference sequence and strand (so only the low 32 bits of x differ).
WGSE_CHAIN_FN int32_t comput_sc(Anchor ai, Anchor aj, const Params &p) {
    const int32_t dq = (int32_t)(ai.y & 0xffffff) - (int32_t)(aj.y & 0xffffff);
    if (dq <= 0 || dq > p.max_dist_x) return INT32_MIN;
    const int32_t dr = (int32_t)(ai.x - aj.x);
    if (dr == 0 || dq > p.max_dist_y) return INT32_MIN;
    const int32_t dd = dr > dq ? dr - dq : dq - dr;
    if (dd > p.bw) return INT32_MIN;
    const int32_t dg = dr < dq ? dr : dq;
    const int32_t q_span = (int32_t)(aj.y >> 24);
    int32_t sc = q_span < dg ? q_span : dg;
    if (dd || dg > q_span) {
        const float lin_pen = add(mul(p.pen_gap, to_float(dd)), mul(p.pen_skip, to_float(dg)));
        const float log_pen = dd >= 1 ? mg_log2(to_float(dd + 1)) : 0.0f;
        if (p.is_cdna && dr > dq) sc -= to_int(lin_pen < log_pen ? lin_pen : log_pen);
        else sc -= to_int(add(lin_pen, mul(.5f, log_pen)));
    }
    return sc;
}

// a.x - b.x > max_dist_x for a[].x with equal high bits, as upstream compares it.
WGSE_CHAIN_FN bool beyond(Anchor a, Anchor b, const Params &p) {
    return (uint64_t)a.x - b.x > (uint64_t)(int64_t)p.max_dist_x;
}

WGSE_CHAIN_FN int32_t span(Anchor a) { return (int32_t)(a.y >> 24); }

// Upstream's loop over one run, by one thread; t[] holds zeros.
WGSE_CHAIN_FN void fill_thread(const Anchor *a, Score *out, int32_t *t, int64_t n, const Params &p) {
    int64_t st = 0, max_ii = -1;
    for (int64_t i = 0; i < n; ++i) {
        const Anchor ai = a[i];
        int64_t max_j = -1, end_j, j;
        int32_t max_f = span(ai), n_skip = 0;
        while (st < i && (uint64_t)ai.x > (uint64_t)a[st].x + (uint64_t)(int64_t)p.max_dist_x) ++st;
        if (i - st > p.max_iter) st = i - p.max_iter;
        for (j = i - 1; j >= st; --j) {
            int32_t sc = comput_sc(ai, a[j], p);
            if (sc == INT32_MIN) continue;
            sc += out[j].f;
            if (sc > max_f) {
                max_f = sc, max_j = j;
                if (n_skip > 0) --n_skip;
            } else if (t[j] == (int32_t)i) {
                if (++n_skip > p.max_skip) break;
            }
            if (out[j].p >= 0) t[out[j].p] = (int32_t)i;
        }
        end_j = j;
        if (max_ii < 0 || beyond(ai, a[max_ii], p)) {
            int32_t max = INT32_MIN;
            max_ii = -1;
            for (j = i - 1; j >= st; --j)
                if (max < out[j].f) max = out[j].f, max_ii = j;
        }
        if (max_ii >= 0 && max_ii < end_j) {
            const int32_t tmp = comput_sc(ai, a[max_ii], p);
            if (tmp != INT32_MIN && max_f < tmp + out[max_ii].f) max_f = tmp + out[max_ii].f, max_j = max_ii;
        }
        out[i] = {max_f, (int32_t)max_j};
        if (max_ii < 0 || (!beyond(ai, a[max_ii], p) && out[max_ii].f < max_f)) max_ii = i;
    }
}

} // namespace wgse::kernels::chain
