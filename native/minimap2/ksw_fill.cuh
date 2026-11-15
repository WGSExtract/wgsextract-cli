// ksw_extd2_sse's KSW_EZ_APPROX_MAX gap fill for CUDA, ported from metal_ksw.mm's
// Metal kernel: the same cells, approximate score and backtrack, one warp per fill.
#pragma once

#include "ksw_engine.h"
#include "portable.h"

#include <climits>
#include <cstdint>

namespace wgse::kernels::ksw {

using wgse::ksw::Fill;
using wgse::ksw::Scoring;

constexpr int lanes = 32;

template <typename T>
WGSE_DEVICE T tmax(T a, T b) { return a > b ? a : b; }
template <typename T>
WGSE_DEVICE T tmin(T a, T b) { return a < b ? a : b; }

WGSE_DEVICE int warp_sum(int value) {
    for (int d = lanes / 2; d > 0; d >>= 1) value += wgse_shfl_xor(value, d, lanes);
    return value;
}

WGSE_DEVICE int warp_max(int value) {
    for (int d = lanes / 2; d > 0; d >>= 1) {
        const int other = wgse_shfl_xor(value, d, lanes);
        value = other > value ? other : value;
    }
    return value;
}

WGSE_DEVICE void push_cigar(uint32_t *cigar, int &n, uint32_t op, int len) {
    if (n == 0 || op != (cigar[n - 1] & 0xf)) cigar[n++] = (uint32_t)len << 4 | op;
    else cigar[n - 1] += (uint32_t)len << 4;
}

// Lane l owns query columns [l*C, l*C+C) and at step s computes target row i = s - l,
// so each cell sees the same (i-1, j) and (i, j-1) int8 values as the CPU's
// anti-diagonal loop. The CPU's approximate score walk telescopes to
// v(0,0) - qe + sum u(i,0) + sum v(T-1,j), which is exact unless u or v wrap; a wrap
// returns INT_MIN and the CPU redoes the fill. The backtrack is ksw_backtrack's
// (is_rot=0, no offsets), emitted in reverse.
//
// T = int8_t computes in int8 like the CPU. T = int16_t (Scoring "narrow") is faster
// and equals it while no int8 value would wrap: while every diagonal step z stays at
// most the match score before the cap and u, v >= -(q+e), which the kernel checks.
template <int C, typename T>
WGSE_DEVICE void fill(const Fill F, const Scoring &S, const uint8_t *seq, uint8_t *pbuf,
    int *score, uint32_t *cigars, int *n_cigar, uint32_t fi, int lane) {
    constexpr bool wide = sizeof(T) == 2;
    const int Tl = F.tlen, Q = F.qlen, stride = (Q + C - 1) / C * C;
    const T qe = (T)(S.q + S.e), qe2 = (T)(S.q2 + S.e2), q = (T)S.q, q2 = (T)S.q2;
    const T mch = (T)S.mch, mis = (T)S.mis, scN = (T)S.scN;
    const int m1 = S.m1, lt = S.long_thres;
    const uint8_t *qs = seq + F.qoff, *ts = seq + F.toff;
    uint8_t *p = pbuf + F.poff;
    const int j0 = lane * C;
    T x[C], v[C], x2[C], lo[C], hi[C]; // lo, hi: min of u and v, max of z before the cap
    uint8_t qb[C];
#pragma unroll
    for (int k = 0; k < C; ++k) {
        const int r = j0 + k; // boundary (-1, j)
        x[k] = -qe; x2[k] = -qe2;
        v[k] = r == 0 ? (T)-qe : r < lt ? (T)-S.e : r == lt ? (T)S.long_diff : (T)-S.e2;
        qb[k] = r < Q ? qs[r] : (uint8_t)m1;
        lo[k] = 0, hi[k] = mch;
    }
    uint32_t carry = 0; // (u, y, y2) of (i, j0 - 1) from lane - 1's previous step
    int H = 0, wrapped = 0;
    for (int s = 0; s < Tl + lanes - 1; ++s) {
        const int i = s - lane;
        const uint32_t in = wgse_shfl_up(carry, 1, lanes);
        T u, y, y2;
        if (lane == 0) {
            u = i == 0 ? (T)-qe : i < lt ? (T)-S.e : i == lt ? (T)S.long_diff : (T)-S.e2;
            y = -qe; y2 = -qe2;
        } else {
            u = (T)(int8_t)(in & 0xff); y = (T)(int8_t)(in >> 8 & 0xff); y2 = (T)(int8_t)(in >> 16 & 0xff);
        }
        if (i >= 0 && i < Tl) {
            const int sb = ts[i];
            uint8_t dd[C];
#pragma unroll
            for (int k = 0; k < C; ++k) {
                const int sq = qb[k];
                T z = (sb == m1 || sq == m1) ? scN : (sb == sq ? mch : mis);
                const T xp = x[k], vp = v[k], x2p = x2[k], ut = u, yt = y, y2t = y2;
                T a = (T)(xp + vp), b = (T)(yt + ut), a2 = (T)(x2p + vp), b2 = (T)(y2t + ut);
                uint8_t d = a > z ? 1 : 0; z = tmax(z, a);
                d = b > z ? 2 : d; z = tmax(z, b);
                d = a2 > z ? 3 : d; z = tmax(z, a2);
                d = b2 > z ? 4 : d; z = tmax(z, b2);
                if (wide) hi[k] = tmax(hi[k], z);
                z = tmin(z, mch);
                if (wide) {
                    u = (T)(z - vp); v[k] = (T)(z - ut);
                    lo[k] = tmin(lo[k], tmin(u, v[k]));
                } else {
                    const int ui = (int)z - (int)vp, vi = (int)z - (int)ut;
                    if (j0 + k < Q) wrapped |= ((uint32_t)(ui + 128) | (uint32_t)(vi + 128)) > 255u ? 1 : 0;
                    u = (T)ui; v[k] = (T)vi;
                }
                T tmp = (T)(z - q); a = (T)(a - tmp); b = (T)(b - tmp);
                tmp = (T)(z - q2); a2 = (T)(a2 - tmp); b2 = (T)(b2 - tmp);
                x[k] = (T)(tmax(a, (T)0) - qe); d |= a > 0 ? 0x08 : 0;
                y = (T)(tmax(b, (T)0) - qe); d |= b > 0 ? 0x10 : 0;
                x2[k] = (T)(tmax(a2, (T)0) - qe2); d |= a2 > 0 ? 0x20 : 0;
                y2 = (T)(tmax(b2, (T)0) - qe2); d |= b2 > 0 ? 0x40 : 0;
                dd[k] = d;
                if (k == 0 && lane == 0) H += i == 0 ? (int)v[0] - (int)qe : (int)u;
            }
            if (j0 < Q) {
                uint8_t *row = p + (size_t)i * stride + j0;
                if (C % 4 == 0) {
#pragma unroll
                    for (int k = 0; k < C; k += 4)
                        ((uint32_t *)row)[k >> 2] = (uint32_t)dd[k] | (uint32_t)dd[k + 1] << 8
                            | (uint32_t)dd[k + 2] << 16 | (uint32_t)dd[k + 3] << 24;
                } else {
#pragma unroll
                    for (int k = 0; k < C; ++k) row[k] = dd[k];
                }
            }
        }
        carry = (uint32_t)(uint8_t)u | (uint32_t)(uint8_t)y << 8 | (uint32_t)(uint8_t)y2 << 16;
    }
#pragma unroll
    for (int k = 0; k < C; ++k)
        if (j0 + k < Q) {
            if (wide) wrapped |= lo[k] < -qe || hi[k] > mch ? 1 : 0;
            if (j0 + k > 0) H += v[k]; // v(T-1, j)
        }
    H = warp_sum(H);
    wrapped = warp_max(wrapped);
    __syncwarp(); // the matrix rows of every lane, for lane 0's backtrack
    if (lane != 0) return;
    score[fi] = wrapped ? INT_MIN : H;
    if (wrapped) return;
    uint32_t *cigar = cigars + F.coff;
    int n = 0, i = Tl - 1, j = Q - 1, state = 0;
    while (i >= 0 && j >= 0) {
        const uint32_t d = p[(size_t)i * stride + j];
        if (state == 0) state = d & 7;
        else if (!(d >> (state + 2) & 1)) state = 0;
        if (state == 0) state = d & 7;
        if (state == 0) push_cigar(cigar, n, 0, 1), --i, --j;
        else if (state == 1 || state == 3) push_cigar(cigar, n, 2, 1), --i;
        else push_cigar(cigar, n, 1, 1), --j;
    }
    if (i >= 0) push_cigar(cigar, n, 2, i + 1);
    if (j >= 0) push_cigar(cigar, n, 1, j + 1);
    n_cigar[fi] = n;
}

// Fills [0, n) of this launch, a warp each.
template <int C, typename T>
__global__ void ksw_fill(const Fill *fills, Scoring S, const uint8_t *seq, uint8_t *p, int *score,
    uint32_t *cigars, int *n_cigar, uint32_t n) {
    const uint32_t gid = blockIdx.x * blockDim.x + threadIdx.x;
    const uint32_t fi = gid / lanes;
    if (fi < n) fill<C, T>(fills[fi], S, seq, p, score, cigars, n_cigar, fi, (int)(threadIdx.x % lanes));
}

} // namespace wgse::kernels::ksw
