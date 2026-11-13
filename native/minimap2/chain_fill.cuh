// mg_lchain_dp's score fill (upstream lchain.c, now wgse_lchain_dp_fill) on the GPU,
// for chain_cuda.cu. Each run of anchors (chain_engine.h) is filled on its own: short
// runs by one thread with upstream's loop, long ones by a warp that scores 32
// predecessors at a time and replays the loop's order (running maximum, skip count
// and early exit) with warp votes. Float arithmetic uses explicitly rounded operations,
// as the CPU computes it without fused multiply-adds, so every score is identical.
#pragma once

#include "chain_engine.h"

#include <climits>
#include <cstdint>

namespace wgse::kernels::chain {

using wgse::chain::Anchor;
using wgse::chain::Params;
using wgse::chain::Run;
using wgse::chain::Score;

// mmpriv.h's mg_log2 (valid for x >= 2).
__device__ __forceinline__ float mg_log2(float x) {
    uint32_t i = __float_as_uint(x);
    float log_2 = __int2float_rn((int)((i >> 23) & 255) - 128);
    i &= ~(255u << 23);
    i += 127u << 23;
    const float z = __uint_as_float(i);
    const float poly = __fsub_rn(__fmul_rn(__fadd_rn(__fmul_rn(-0.34484843f, z), 2.02466578f), z), 0.67487759f);
    return __fadd_rn(log_2, poly);
}

// lchain.c's comput_sc for anchors of one read segment (n_seg == 1, so sidi == sidj)
// on one reference sequence and strand (so only the low 32 bits of x differ).
__device__ __forceinline__ int32_t comput_sc(Anchor ai, Anchor aj, const Params &p) {
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
        const float lin_pen = __fadd_rn(__fmul_rn(p.pen_gap, __int2float_rn(dd)), __fmul_rn(p.pen_skip, __int2float_rn(dg)));
        const float log_pen = dd >= 1 ? mg_log2(__int2float_rn(dd + 1)) : 0.0f;
        if (p.is_cdna && dr > dq) sc -= __float2int_rz(lin_pen < log_pen ? lin_pen : log_pen);
        else sc -= __float2int_rz(__fadd_rn(lin_pen, __fmul_rn(.5f, log_pen)));
    }
    return sc;
}

// a.x - b.x > max_dist_x for a[].x with equal high bits, as upstream compares it.
__device__ __forceinline__ bool beyond(Anchor a, Anchor b, const Params &p) {
    return (uint64_t)a.x - b.x > (uint64_t)(int64_t)p.max_dist_x;
}

__device__ __forceinline__ int32_t span(Anchor a) { return (int32_t)(a.y >> 24); }

// Upstream's loop over one run, by one thread; t[] holds zeros.
__device__ void fill_thread(const Anchor *a, Score *out, int32_t *t, int64_t n, const Params &p) {
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

// The same loop by a warp. Lane l scores predecessor j = base - l, so the lanes in
// order are the loop's iterations; a prefix maximum over them gives each iteration's
// running maximum. An iteration that scores (sc != INT32_MIN) marks t[p[j]] = i, and
// p[j] < j, so the marks a lane reads come from earlier iterations, which upstream
// also made before reaching it (marks made past an exit are never read). Most rows
// look back fewer than 32 anchors, so lane l keeps anchor i - 1 - l and its f and p
// in registers, and marks within those 32 anchors are a warp-wide bit mask; anchors
// further back are read from memory, with marks in t[].
__device__ void fill_warp(const Anchor *a, Score *out, int32_t *t, int64_t n, const Params &p) {
    constexpr unsigned all = 0xffffffffu;
    const unsigned lane = threadIdx.x & 31;
    const uint64_t d = (uint64_t)(int64_t)p.max_dist_x;
    Anchor w{0, 0}, ahead{0, 0}; // w: anchor i - 1 - lane; ahead: anchor ahead_base + lane
    int32_t wf = 0, wp = -1;
    int64_t st = 0, max_ii = -1, ahead_base = -32;
    uint32_t x_st = a[0].x;   // a[st].x
    Anchor am{0, 0};          // a[max_ii], with f[max_ii]
    int32_t fm = 0;
    for (int64_t i = 0; i < n; ++i) {
        if (i - ahead_base >= 32) {
            ahead_base = i;
            if (i + lane < n) ahead = a[i + lane];
        }
        const unsigned at = unsigned(i - ahead_base);
        const Anchor ai{__shfl_sync(all, ahead.x, at), __shfl_sync(all, ahead.y, at)};
        // Upstream's st loop: anchors out of range form a prefix of [st, i).
        const unsigned out_of_range = __ballot_sync(all, i - 1 - (int64_t)lane >= st && (uint64_t)ai.x > (uint64_t)w.x + d);
        if (out_of_range) {
            st = i - (__ffs(out_of_range) - 1);
            x_st = st < i ? __shfl_sync(all, w.x, unsigned(i - 1 - st)) : ai.x;
        } else {
            while (st < i - 32 && (uint64_t)ai.x > (uint64_t)x_st + d) x_st = a[++st].x;
        }
        if (i - st > p.max_iter) st = i - p.max_iter, x_st = a[st].x;
        int32_t max_f = span(ai), n_skip = 0;
        int64_t max_j = -1, end_j = st - 1;
        for (int64_t base = i - 1; base >= st; base -= 32) {
            const int64_t j = base - (int64_t)lane;
            const bool first = base == i - 1;
            Anchor aj = w;
            int32_t fj = wf, pj = wp;
            if (!first && j >= st) {
                aj = a[j];
                const Score s = out[j];
                fj = s.f, pj = s.p;
            }
            int32_t sc = j >= st ? comput_sc(ai, aj, p) : INT32_MIN;
            const bool valid = sc != INT32_MIN;
            if (valid) sc += fj;
            bool hit;
            if (first) {
                const bool near = valid && pj >= 0 && pj > base - 32;
                const unsigned marks = __reduce_or_sync(all, near ? 1u << unsigned(base - pj) : 0u);
                if (valid && pj >= 0 && !near) t[pj] = (int32_t)i;
                hit = valid && (marks >> lane & 1);
            } else {
                if (valid && pj >= 0) t[pj] = (int32_t)i;
                __syncwarp();
                hit = valid && t[j] == (int32_t)i;
            }
            // Each lane's running maximum before it: the prefix maximum of earlier lanes.
            int32_t prefix = sc;
            for (int k = 1; k < 32; k <<= 1) {
                const int32_t o = __shfl_up_sync(all, prefix, k);
                if (lane >= (unsigned)k && o > prefix) prefix = o;
            }
            int32_t before = __shfl_up_sync(all, prefix, 1);
            if (lane == 0 || before < max_f) before = max_f;
            const bool improves = valid && sc > before;
            const bool skips = hit && !improves;
            // n_skip after each lane: --n_skip (if positive) on improving, ++n_skip on a
            // mark. With e = -1 or +1 and S its prefix sum, the count after lane l is
            // S[l] + max(n_skip, -min(S[0..l])); the loop exits at the first mark past max_skip.
            int32_t sum = improves ? -1 : skips ? 1 : 0;
            for (int k = 1; k < 32; k <<= 1) {
                const int32_t o = __shfl_up_sync(all, sum, k);
                if (lane >= (unsigned)k) sum += o;
            }
            int32_t low = sum;
            for (int k = 1; k < 32; k <<= 1) {
                const int32_t o = __shfl_up_sync(all, low, k);
                if (lane >= (unsigned)k && o < low) low = o;
            }
            const int32_t count = sum + (n_skip > -low ? n_skip : -low);
            const unsigned exits = __ballot_sync(all, skips && count > p.max_skip);
            const unsigned up = __ballot_sync(all, improves);
            const int exit_lane = exits ? __ffs(exits) - 1 : 32;
            const unsigned taken = exit_lane == 32 ? up : up & ((1u << exit_lane) - 1);
            if (taken) {
                const int last = 31 - __clz(taken);
                max_f = __shfl_sync(all, sc, last);
                max_j = base - last;
            }
            if (exit_lane < 32) {
                end_j = base - exit_lane;
                break;
            }
            n_skip = __shfl_sync(all, count, 31);
            __syncwarp();
        }
        if (max_ii < 0 || (uint64_t)ai.x - am.x > d) {
            // The highest f in [st, i), the highest j among equals.
            int32_t best = INT32_MIN;
            int64_t best_j = -1;
            for (int64_t j = i - 1 - (int64_t)lane; j >= st; j -= 32) {
                const int32_t fj = j >= i - 32 ? wf : out[j].f; // lane l's j starts at i - 1 - l
                if (best < fj) best = fj, best_j = j;
            }
            for (int k = 16; k; k >>= 1) {
                const int32_t ob = __shfl_xor_sync(all, best, k);
                const int64_t oj = __shfl_xor_sync(all, best_j, k);
                if (ob > best || (ob == best && oj > best_j)) best = ob, best_j = oj;
            }
            max_ii = best_j;
            if (max_ii >= i - 32) {
                const unsigned l = unsigned(i - 1 - max_ii);
                am = {__shfl_sync(all, w.x, l), __shfl_sync(all, w.y, l)}, fm = __shfl_sync(all, wf, l);
            } else if (max_ii >= 0) {
                am = a[max_ii], fm = out[max_ii].f;
            }
        }
        if (max_ii >= 0 && max_ii < end_j) {
            const int32_t tmp = comput_sc(ai, am, p);
            if (tmp != INT32_MIN && max_f < tmp + fm) max_f = tmp + fm, max_j = max_ii;
        }
        if (lane == 0) out[i] = {max_f, (int32_t)max_j};
        if (max_ii < 0 || ((uint64_t)ai.x - am.x <= d && fm < max_f)) max_ii = i, am = ai, fm = max_f;
        // Slide the window: lane 0 takes anchor i.
        w.x = __shfl_up_sync(all, w.x, 1), w.y = __shfl_up_sync(all, w.y, 1);
        wf = __shfl_up_sync(all, wf, 1), wp = __shfl_up_sync(all, wp, 1);
        if (lane == 0) w = ai, wf = max_f, wp = (int32_t)max_j;
        __syncwarp();
    }
}

// Runs [0, n_short) by a thread each, runs [n_short, n_runs) by a warp each.
__global__ void fill_short(const Anchor *anchors, const Run *runs, Score *out, int32_t *t, uint32_t n_short, Params p) {
    const uint32_t r = blockIdx.x * blockDim.x + threadIdx.x;
    if (r >= n_short) return;
    const Run run = runs[r];
    fill_thread(anchors + run.first, out + run.first, t + run.first, run.n, p);
}

__global__ void fill_long(const Anchor *anchors, const Run *runs, Score *out, int32_t *t, uint32_t first_run,
    uint32_t n_runs, Params p) {
    const uint32_t r = first_run + (blockIdx.x * blockDim.x + threadIdx.x) / 32;
    if (r >= n_runs) return;
    const Run run = runs[r];
    fill_warp(anchors + run.first, out + run.first, t + run.first, run.n, p);
}

} // namespace wgse::kernels::chain
