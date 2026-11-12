// Mate-rescue local alignment for CUDA/HIP: a port of rescue.metal, BWA 0.7.19's
// ksw_align2 with KSW_XBYTE (ksw_u8, Farrar's striped Smith-Waterman with 16
// byte lanes, and its reverse pass). Each job uses a segment of 16 lanes; the
// striping is reproduced exactly, not just the scores. Adapted from ksw.c
// (Attractive Chaos, MIT; see SEEDING-LICENSE).
#pragma once

#include "portable.h"
#include "rescue_types.h"

namespace wgse::kernels::rescue {
constexpr int lanes = int(rescue_lanes), segments = int(rescue_max_segments);

// Maximum over the 16 lanes of this job's segment.
WGSE_DEVICE int half_max(int value) {
    for (int offset = 8; offset > 0; offset >>= 1)
        value = max(value, wgse_shfl_xor(value, offset, lanes));
    return value;
}

WGSE_DEVICE uint16_t up16(uint16_t value) {
    return uint16_t(wgse_shfl_up(int(value), 1, lanes));
}

struct Costs {
    uint16_t shift, ambiguous_s, e_del, e_ins, oe_del, oe_ins;
};

// Whether segment j is part of a query with slen segments; S is the forward
// pass's segment count, which every job of a launch has.
template <int S>
WGSE_DEVICE bool live(int j, int slen) {
    return j < S && j < slen;
}

// One target row over this lane's segments; returns the row maximum. h enters
// as the H carried from the left neighbour and f leaves as this lane's final F.
template <int S, bool may_be_n>
WGSE_DEVICE uint16_t row(uint16_t (&H)[segments], uint16_t (&E)[segments],
    const uint32_t (&P)[segments], int slen, uint16_t base, int real, uint16_t h, uint16_t &f,
    Costs c) {
    const bool n = may_be_n && base == 4;
    const uint32_t lane_shift = n ? 0u : 8u * uint32_t(base);
    uint16_t row_max = 0;
#pragma unroll
    for (int j = 0; j < segments; ++j) {
        if (live<S>(j, slen)) {
            const uint16_t profile = uint16_t((P[j] >> lane_shift) & 255u);
            const uint16_t score = !n ? profile : j < real ? c.ambiguous_s : c.shift;
            const uint16_t previous = H[j];
            h = subsat(min(uint16_t(h + score), uint16_t(255)), c.shift);
            const uint16_t e = E[j];
            h = max(h, max(e, f));
            row_max = max(row_max, h);
            H[j] = h;
            E[j] = max(subsat(e, c.e_del), subsat(h, c.oe_del));
            f = max(subsat(f, c.e_ins), subsat(h, c.oe_ins));
            h = previous;
        }
    }
    return row_max;
}

struct Pass {
    int gmax, te, qe, rows;
};

// One ksw_u8 call; every lane of the warp calls it (uniform control flow), and
// lanes whose segment has no job pass run = false. In the reverse pass the query
// is query[qe..0] and the target target[te..0] followed by target[te+1..tlen).
template <int S, bool forward>
WGSE_DEVICE Pass striped(bool run, int lane, const uint8_t *query, int qlen, int reverse_from,
    const uint8_t *target, int tlen, int target_from, int endsc, uint8_t *rows,
    const RescueParameters &p) {
    const int slen = forward ? S : (qlen + lanes - 1) / lanes;
    const int oe_del = p.o_del + p.e_del, oe_ins = p.o_ins + p.e_ins;
    uint32_t P[segments];
    uint16_t H[segments], E[segments], M[segments];
    const uint16_t shift = uint16_t(p.shift), e_ins = uint16_t(p.e_ins),
                   oe_ins16 = uint16_t(oe_ins);
    const uint16_t match_s = uint16_t(p.match + p.shift), mismatch_s = uint16_t(p.mismatch + p.shift),
                   ambiguous_s = uint16_t(p.ambiguous + p.shift);
    const Costs c{shift, ambiguous_s, uint16_t(p.e_del), e_ins, uint16_t(oe_del), oe_ins16};
#pragma unroll
    for (int j = 0; j < segments; ++j) {
        const int position = lane * slen + j;
        const uint32_t q = !run || !live<S>(j, slen) || position >= qlen ? 255u
            : uint32_t(query[reverse_from >= 0 ? reverse_from - position : position]);
        uint32_t packed = 0;
        for (uint32_t b = 0; b < 4; ++b) {
            const uint32_t value = q == 255 ? uint32_t(shift) : q == 4 ? uint32_t(ambiguous_s)
                : b == q ? uint32_t(match_s) : uint32_t(mismatch_s);
            packed |= value << (8 * b);
        }
        P[j] = packed;
        H[j] = E[j] = M[j] = 0;
    }
    Pass out{0, -1, -1, 0};
    for (int i = 0; wgse_any(run); ++i) {
        uint16_t base = 0, last = 0;
        if (run) {
            base = uint16_t(target[target_from >= i ? target_from - i : i]);
#pragma unroll
            for (int j = 0; j < segments; ++j)
                if (live<S>(j, slen) && j == slen - 1) last = H[j];
        }
        uint16_t h = up16(last);
        if (lane == 0) h = 0;
        uint16_t f = 0, row_max = 0;
        // Rows without an N in the target need no per-cell ambiguity test.
        const bool plain = wgse_all(base != 4);
        if (run) {
            const int real = qlen - lane * slen;
            row_max = plain ? row<S, false>(H, E, P, slen, base, real, h, f, c)
                            : row<S, true>(H, E, P, slen, base, real, h, f, c);
        }
        // Lazy F (ksw_u8's SWPS3 loop), as in rescue.metal.
        uint16_t round_f = up16(f);
        if (lane == 0) round_f = 0;
        bool lazy = run;
        for (int k = 0; k < lanes && wgse_any(lazy); ++k) {
            {
                const uint16_t value = max(H[0], round_f);
                const bool r0 = lazy && subsat(round_f, e_ins) > subsat(value, oe_ins16);
                if (!wgse_any(r0)) {
                    if (lazy) H[0] = value;
                    break;
                }
            }
            uint32_t fails = 0;
            uint16_t g = round_f;
#pragma unroll
            for (int j = 0; j < segments; ++j) {
                if (live<S>(j, slen)) {
                    const uint16_t value = max(H[j], g);
                    g = subsat(g, e_ins);
                    fails |= uint32_t(g > subsat(value, oe_ins16)) << j;
                }
            }
            for (int offset = 8; offset > 0; offset >>= 1)
                fails |= uint32_t(wgse_shfl_xor(int(fails), offset, lanes));
            const int stop = min(ctz32(~fails), slen);
            if (lazy) {
                g = round_f;
#pragma unroll
                for (int j = 0; j < segments; ++j) {
                    if (live<S>(j, slen)) {
                        if (j <= stop) H[j] = max(H[j], g);
                        g = subsat(g, e_ins);
                    }
                }
                if (stop < slen) lazy = false;
            }
            const uint16_t carried = up16(g);
            round_f = lane == 0 ? uint16_t(0) : carried;
        }
        const int imax = half_max(int(row_max));
        if (run) {
            if (rows && lane == 0) rows[i] = uint8_t(imax);
            out.rows = i + 1;
            if (imax > out.gmax) {
                out.gmax = imax;
                out.te = i;
#pragma unroll
                for (int j = 0; j < segments; ++j)
                    if (live<S>(j, slen)) M[j] = H[j];
                if (out.gmax + p.shift >= 255 || out.gmax >= endsc) run = false;
            }
            if (i + 1 >= tlen) run = false;
        }
    }
    // The query end: the leftmost position of the best row's maximum.
    int key = 0;
#pragma unroll
    for (int j = 0; j < segments; ++j)
        if (live<S>(j, slen)) key = max(key, int(M[j]) << 8 | (255 - (lane * slen + j)));
    key = half_max(key);
    out.qe = 255 - (key & 255);
    return out;
}

// The jobs order[0, p.count), all with S forward segments; 16 lanes per job.
template <int S>
__global__ void rescue_align(const uint8_t *__restrict__ bases, const RescueJob *__restrict__ jobs,
    uint8_t *rows, RescueResult *results, RescueParameters p, const uint32_t *__restrict__ order) {
    const uint32_t thread = blockIdx.x * blockDim.x + threadIdx.x;
    const int lane = int(threadIdx.x % lanes);
    const uint32_t slot = thread / lanes;
    const bool valid = slot < p.count;
    const uint32_t index = valid ? order[slot] : 0;
    const RescueJob job = valid ? jobs[index] : RescueJob{0, 0, 0, 0, 0, 0};
    const int xtra = int(job.xtra);
    const bool subo = (xtra & int(rescue_xsubo)) != 0;
    const int minsc = subo ? xtra & 0xffff : 0x10000;
    const int endsc = (xtra & int(rescue_xstop)) ? xtra & 0xffff : 0x10000;
    RescueResult r{0, -1, -1, -1, -1, -1, -1, rescue_ok};
    // A job in the wrong group would be striped differently; leave it to the CPU.
    if (valid && (int(job.qlen) + lanes - 1) / lanes != S) r.status = rescue_cpu;
    const bool run = valid && job.tlen > 0 && r.status == rescue_ok;
    const Pass forward = striped<S, true>(run, lane, bases + job.query, int(job.qlen), -1,
        bases + job.target, int(job.tlen), -1, endsc, subo && valid ? rows + job.rows : nullptr, p);
    if (valid && r.status == rescue_ok) {
        r.score = forward.gmax + p.shift < 255 ? forward.gmax : 255;
        r.te = forward.te;
        if (r.score != 255) {
            r.qe = forward.qe;
            // Replays ksw_u8's b[] list of row maxima >= minsc (see rescue.metal).
            if (subo && lane == 0) {
                const int width = (r.score + p.max - 1) / p.max;
                const int low = r.te - width, high = r.te + width;
                int entry_score = -1, entry_row = -1;
                bool have = false;
                for (int i = 0; i < forward.rows; ++i) {
                    const int value = int(rows[job.rows + i]);
                    if (value < minsc) continue;
                    if (!have || entry_row + 1 != i) {
                        if (have && (entry_row < low || entry_row > high) && entry_score > r.score2)
                            r.score2 = entry_score, r.te2 = entry_row;
                        entry_score = value;
                        entry_row = i;
                        have = true;
                    } else if (entry_score < value) {
                        entry_score = value;
                        entry_row = i;
                    }
                }
                if (have && (entry_row < low || entry_row > high) && entry_score > r.score2)
                    r.score2 = entry_score, r.te2 = entry_row;
            }
        }
    }
    const bool start = valid && r.status == rescue_ok && (xtra & int(rescue_xstart))
        && !(subo && r.score < (xtra & 0xffff));
    if (start && (r.qe < 0 || r.te < 0)) r.status = rescue_cpu;
    const bool reverse = start && r.status == rescue_ok;
    const Pass back = striped<S, false>(reverse, lane, bases + job.query, r.qe + 1, r.qe,
        bases + job.target, int(job.tlen), r.te, r.score & 0xffff, nullptr, p);
    if (reverse) {
        const int score = back.gmax + p.shift < 255 ? back.gmax : 255;
        const int qe = score != 255 ? back.qe : -1;
        if (r.score == score) {
            r.tb = r.te - back.te;
            r.qb = r.qe - qe;
        }
    }
    if (valid && lane == 0) results[index] = r;
}
} // namespace wgse::kernels::rescue
