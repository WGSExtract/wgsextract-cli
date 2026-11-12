// Seed extension for CUDA/HIP: a port of extend.metal, BWA 0.7.19's ksw_extend2
// with mem_chain2aln's band retry, for the first seed of each chain. Each job
// uses a segment of 16 lanes, which hold the query and one row of H and E in
// registers; Metal's simd_* calls are width-16 shuffles. Adapted from ksw.c
// (Attractive Chaos, MIT; see SEEDING-LICENSE).
#pragma once

#include "extend_types.h"
#include "portable.h"

namespace wgse::kernels::extend {
constexpr int lanes = int(extend_lanes);

struct Call { int score, qle, tle, gtle, gscore, max_off; };

WGSE_DEVICE short sh_up(short v, int d) { return short(wgse_shfl_up(int(v), d, lanes)); }
WGSE_DEVICE short sh(short v, int src) { return short(wgse_shfl(int(v), src, lanes)); }
WGSE_DEVICE int sh_xor(int v, int d) { return wgse_shfl_xor(v, d, lanes); }

template <int T, int columns>
WGSE_DEVICE Call extend(bool run, int lane, const unsigned char *query, int qlen,
    const unsigned char *target, int tlen, int requested_w, int end_bonus, int h0,
    const ExtendParameters &p)
{
    short H[T][columns], E[T][columns], JE[T][columns];
    unsigned char Q[T][columns];
#pragma unroll
    for (int t = 0; t < T; ++t)
#pragma unroll
        for (int k = 0; k < columns; ++k) {
            const int j = lanes * columns * t + lane * columns + k;
            H[t][k] = short(j == 0 ? h0 : max(0, h0 - p.o_ins - p.e_ins * j));
            E[t][k] = 0;
            Q[t][k] = run && j < qlen ? query[j] : 0;
            JE[t][k] = short(j * p.e_ins);
        }
    int w = min(requested_w, max(1, (qlen * p.max + end_bonus - p.o_ins) / p.e_ins + 1));
    w = min(w, max(1, (qlen * p.max + end_bonus - p.o_del) / p.e_del + 1));
    int best = h0, best_i = -1, best_j = -1, gscore = -1, gend = -1, max_off = 0;
    int beg = 0, end = qlen;
    const int oe_del = p.o_del + p.e_del;
    const short e_del = short(p.e_del), o_ins = short(p.o_ins);
    bool live = run;
    for (int i = 0;; ++i) {
        live = live && i < tlen;
        if (!wgse_any(live)) break;
        short previous = 0, carry = 0, h1_init = 0;
        int row_best = 0, first = 1 << 20, last = -1, base = 0;
        if (live) {
            beg = max(beg, i - w);
            end = min(end, min(qlen, i + w + 1));
            h1_init = short(beg == 0 ? max(0, h0 - oe_del - p.e_del * i) : 0);
            previous = h1_init;
            base = int(target[i]);
        }
#pragma unroll
        for (int t = 0; t < T; ++t) {
            const int tile = lanes * columns * t;
            const bool on = live && tile <= end && tile + lanes * columns > beg;
            if (!wgse_any(on)) continue;
            const int j0 = tile + lane * columns;
            short match[columns], prefix = 0;
#pragma unroll
            for (int k = 0; k < columns; ++k) {
                const int j = j0 + k;
                const bool valid = on && j >= beg && j < end;
                const short diagonal = H[t][k];
                const int q = int(Q[t][k]);
                const short score = short(base == 4 || q == 4 ? p.ambiguous : base == q ? p.match : p.mismatch);
                match[k] = !valid || diagonal == 0 ? short(0) : short(diagonal + score);
                if (valid) prefix = max(prefix, short(match[k] + JE[t][k]));
            }
#pragma unroll
            for (int d = 1; d < lanes; d *= 2) {
                const short shifted = sh_up(prefix, d);
                if (lane >= d) prefix = max(prefix, shifted);
            }
            prefix = max(prefix, carry);
            short x = sh_up(prefix, 1);
            if (lane == 0) x = carry;
            short value[columns];
#pragma unroll
            for (int k = 0; k < columns; ++k) {
                value[k] = max(match[k], max(E[t][k], max(short(0), short(x - o_ins - JE[t][k]))));
                x = max(x, short(match[k] + JE[t][k]));
            }
            short last_value = value[0];
#pragma unroll
            for (int k = 1; k < columns; ++k)
                if (j0 + k < end) last_value = value[k];
            short preceding = sh_up(last_value, 1);
            if (lane == 0) preceding = previous;
#pragma unroll
            for (int k = 0; k < columns; ++k) {
                const int j = j0 + k;
                if (!on || j < beg || j > end) continue;
                const short before = j == beg ? h1_init : k == 0 ? preceding : value[k - 1];
                const short next_e = j == end ? short(0) : max(short(E[t][k] - e_del), short(max(0, int(match[k]) - oe_del)));
                H[t][k] = before;
                E[t][k] = next_e;
                if (j < end) {
                    if (before != 0 || next_e != 0) {
                        first = min(first, j);
                        last = max(last, j);
                    }
                    row_best = max(row_best, int(value[k]) * 512 + j + 1);
                }
            }
            const bool any = on && tile < end && tile + lanes * columns > beg;
            const short next_previous = sh(last_value, min(lanes - 1, max(0, (end - tile - 1) / columns)));
            const short next_carry = sh(prefix, lanes - 1);
            if (any) {
                previous = next_previous;
                carry = next_carry;
            }
        }
#pragma unroll
        for (int d = lanes / 2; d > 0; d /= 2) {
            row_best = max(row_best, sh_xor(row_best, d));
            first = min(first, sh_xor(first, d));
            last = max(last, sh_xor(last, d));
        }
        if (live) {
            if (previous != 0) last = max(last, end);
            if (first == (1 << 20)) first = end;
            const int m = row_best >> 9, mj = (row_best & 511) - 1;
            if (max(beg, end) == qlen && int(previous) >= gscore) {
                gscore = previous;
                gend = i;
            }
            if (m == 0) {
                live = false;
            } else if (m > best) {
                best = m;
                best_i = i;
                best_j = mj;
                max_off = max(max_off, abs(mj - i));
            } else if (p.zdrop > 0) {
                const int drift = (i - best_i) - (mj - best_j);
                const int penalty = drift > 0 ? drift * p.e_del : -drift * p.e_ins;
                if (best - m - penalty > p.zdrop) live = false;
            }
            beg = first;
            end = min(qlen, max(last, beg - 1) + 2);
        }
    }
    return {best, best_j + 1, best_i + 1, gend + 1, gscore, max_off};
}

template <int T, int C>
__global__ void extend_side(bool right, const unsigned char *__restrict__ bases,
    const ExtendJob *__restrict__ jobs, ExtendResult *results, ExtendParameters p,
    const unsigned *__restrict__ order)
{
    const unsigned gid = blockIdx.x * blockDim.x + threadIdx.x;
    const int lane = int(threadIdx.x) % lanes;
    const unsigned slot = gid / lanes;
    const bool valid = slot < p.count;
    const unsigned index = valid ? order[slot] : 0;
    const ExtendJob job = valid ? jobs[index] : ExtendJob{0, 0, 0, 0, 1, 0, 0, 0, 0};
    ExtendResult &r = results[index];
    int h0 = int(job.left_h0);
    if (right && valid && job.left_qlen > 0) h0 = r.calls[r.left_tries - 1].score;
    const unsigned query = right ? job.right_query : job.left_query;
    const unsigned target = right ? job.right_target : job.left_target;
    const int qlen = int(right ? job.right_qlen : job.left_qlen);
    const int tlen = int(right ? job.right_tlen : job.left_tlen);
    const int bonus = right ? p.right_bonus : p.left_bonus;
    int score = right ? h0 : -1, tries = 0;
    bool more = valid && qlen > 0;
    Call calls[extend_tries];
    for (int t = 0; t < int(extend_tries); ++t) {
        if (!wgse_any(more)) break;
        const int w = p.w << t;
        const Call call = extend<T, C>(more, lane, bases + query, qlen, bases + target, tlen, w, bonus, h0, p);
        if (more) {
            calls[t] = call;
            tries = t + 1;
            more = !(call.score == score || call.max_off < (w >> 1) + (w >> 2));
            score = call.score;
        }
    }
    if (!valid || lane != 0) return;
    const int first = right ? int(extend_tries) : 0;
    for (int t = 0; t < tries; ++t) {
        const Call &c = calls[t];
        r.calls[first + t] = ExtendCall{c.score, c.qle, c.tle, c.gtle, c.gscore, c.max_off};
    }
    if (right) {
        r.right_h0 = h0;
        r.right_tries = tries;
    } else {
        r.left_tries = tries;
    }
}
} // namespace wgse::kernels::extend
