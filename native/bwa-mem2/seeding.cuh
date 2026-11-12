// BWA-MEM2 2.3's SMEM collection (mem_collect_smem's three passes), one read per thread, for
// CUDA and HIP: a line-for-line port of seeding.metal. Adapted from FMI_search.cpp (Intel
// Corporation and Heng Li, MIT; see BWA-MEM2-LICENSE).
#pragma once

#include "portable.h"
#include "seeding_types.h"

#include <cstdint>

namespace wgse::kernels::mem2_seeding {

// FMI_search's CP_OCC: occurrence counts at each 64-symbol checkpoint and the symbols after
// it as one-hot bit strings.
struct Checkpoint {
    int64_t count[4];
    uint64_t one_hot[4];
};
static_assert(sizeof(Checkpoint) == 64, "FMI_search's CP_OCC");

struct Interval {
    int64_t k, l, s;
    uint32_t m, n;
};

struct Occurrences {
    int64_t at[4];
};

// GET_OCC for all four bases: the checkpoint's counts plus the matches among its first
// p & 63 symbols.
WGSE_DEVICE Occurrences occurrences(const Checkpoint *cp, int64_t p) {
    const Checkpoint &entry = cp[p >> 6];
    const uint32_t y = uint32_t(p & 63);
    const uint64_t mask = y ? ~0ull << (64 - y) : 0ull;
    Occurrences o;
#pragma unroll
    for (int a = 0; a < 4; ++a) o.at[a] = entry.count[a] + popcount64(entry.one_hot[a] & mask);
    return o;
}

// FMI_search::backwardExt.
WGSE_DEVICE Interval backward(const Checkpoint *cp, const SeedParameters &p, Interval in,
    uint32_t a) {
    const Occurrences sp = occurrences(cp, in.k), end = occurrences(cp, in.k + in.s);
    int64_t s[4];
#pragma unroll
    for (int b = 0; b < 4; ++b) s[b] = end.at[b] - sp.at[b];
    const int64_t l3 = in.l + int64_t(in.k <= p.sentinel && in.k + in.s > p.sentinel);
    const int64_t l2 = l3 + s[3], l1 = l2 + s[2], l0 = l1 + s[1];
    Interval out = in;
    out.k = p.count[a] + sp.at[a];
    out.l = a == 0 ? l0 : a == 1 ? l1 : a == 2 ? l2 : l3;
    out.s = s[a];
    return out;
}

// Forward extension is backward extension with the BWT of the reverse complement.
WGSE_DEVICE Interval forward(const Checkpoint *cp, const SeedParameters &p, Interval in,
    uint32_t a) {
    Interval swapped = in;
    swapped.k = in.l;
    swapped.l = in.k;
    const Interval extended = backward(cp, p, swapped, 3 - a);
    Interval out = extended;
    out.k = extended.l;
    out.l = extended.k;
    return out;
}

struct Output {
    SeedSmem *data;
    uint32_t capacity, size;

    WGSE_DEVICE bool push(const Interval &value) {
        if (size < capacity) data[size] = {value.m, value.n, value.k, value.l, value.s};
        return ++size <= capacity;
    }
};

struct Scratch {
    Interval *data;
    uint32_t stride, capacity;

    WGSE_DEVICE Interval get(uint32_t i) const { return data[uint64_t(i) * stride]; }
    WGSE_DEVICE void set(uint32_t i, const Interval &value) const { data[uint64_t(i) * stride] = value; }
};

WGSE_DEVICE Interval initial(const SeedParameters &p, uint32_t a, uint32_t x) {
    return {p.count[a], p.count[3 - a], p.count[a + 1] - p.count[a], x, x};
}

// The interval of every string of 1..seed_prefix_depth bases, from initial() and forward()
// as the walks below compute it: shallow intervals are wide, so each forward step there
// reads two far-apart checkpoints, and a lookup replaces them. Strings of d bases start at
// entry (4^d - 4) / 3, the first base most significant.
struct Prefix {
    int64_t k, l, s;
};

WGSE_DEVICE uint32_t prefix_entry(uint32_t depth, uint32_t code) {
    return ((1u << (2 * depth)) - 4) / 3 + code;
}

WGSE_DEVICE Interval extend_prefix(const Prefix *prefixes, Interval in, uint32_t depth,
    uint32_t code) {
    const Prefix t = prefixes[prefix_entry(depth, code)];
    in.k = t.k;
    in.l = t.l;
    in.s = t.s;
    return in;
}

inline constexpr uint32_t prefix_entries = ((1u << (2 * (seed_prefix_depth + 1))) - 4) / 3;

__global__ void seed_prefixes(const Checkpoint *__restrict__ cp, SeedParameters p,
    Prefix *__restrict__ prefixes) {
    const uint32_t id = blockIdx.x * blockDim.x + threadIdx.x;
    if (id >= prefix_entry(seed_prefix_depth + 1, 0)) return;
    uint32_t depth = 1;
    while (id >= prefix_entry(depth + 1, 0)) ++depth;
    const uint32_t code = id - prefix_entry(depth, 0);
    Interval smem = initial(p, code >> (2 * (depth - 1)), 0);
    for (uint32_t d = depth - 1; d-- > 0;) smem = forward(cp, p, smem, (code >> (2 * d)) & 3);
    prefixes[id] = {smem.k, smem.l, smem.s};
}

// FMI_search::getSMEMsOnePosOneThread for one read from x; returns next_x, or -1 when the
// output or scratch is full.
WGSE_DEVICE int one_position(const Checkpoint *cp, const Prefix *prefixes,
    const SeedParameters &p, const uint8_t *q, int length, int x, int min_intv,
    const Scratch &prev, Output &out) {
    int next_x = x + 1;
    uint32_t a = q[x];
    if (a >= 4) return next_x;
    Interval smem = initial(p, a, uint32_t(x));
    uint32_t count = 0, code = a;
    int j;
    for (j = x + 1; j < length; ++j) {
        a = q[j];
        next_x = j + 1;
        if (a >= 4) break;
        const uint32_t depth = uint32_t(j - x) + 1;
        code = code << 2 | a;
        Interval next = depth <= seed_prefix_depth ? extend_prefix(prefixes, smem, depth, code)
            : forward(cp, p, smem, a);
        next.n = uint32_t(j);
        if (count >= prev.capacity) return -1;
        prev.set(count, smem);
        count += next.s != smem.s;
        if (next.s < min_intv) {
            next_x = j;
            break;
        }
        smem = next;
    }
    if (smem.s >= min_intv) {
        if (count >= prev.capacity) return -1;
        prev.set(count++, smem);
    }
    for (uint32_t i = 0; i < count / 2; ++i) {
        Interval t = prev.get(i);
        prev.set(i, prev.get(count - i - 1));
        prev.set(count - i - 1, t);
    }
    for (j = x - 1; j >= 0; --j) {
        uint32_t current = 0;
        // Upstream keeps the last kept size in an int.
        int last_s = -1;
        a = q[j];
        if (a > 3) break;
        uint32_t i = 0;
        for (; i < count; ++i) {
            Interval kept = prev.get(i), next = backward(cp, p, kept, a);
            next.m = uint32_t(j);
            if (next.s < min_intv && kept.n - kept.m + 1 >= uint32_t(p.min_seed_len)) {
                if (!out.push(kept)) return -1;
                break;
            }
            if (next.s >= min_intv && next.s != int64_t(last_s)) {
                last_s = int(next.s);
                prev.set(current++, next);
                break;
            }
        }
        for (++i; i < count; ++i) {
            Interval next = backward(cp, p, prev.get(i), a);
            next.m = uint32_t(j);
            if (next.s >= min_intv && next.s != int64_t(last_s)) {
                last_s = int(next.s);
                prev.set(current++, next);
            }
        }
        count = current;
        if (current == 0) break;
    }
    if (count != 0) {
        Interval first = prev.get(0);
        if (first.n - first.m + 1 >= uint32_t(p.min_seed_len) && !out.push(first)) return -1;
    }
    return next_x;
}

__global__ void collect_smems(const Checkpoint *__restrict__ cp, SeedParameters p,
    const uint8_t *__restrict__ bases, const SeedRead *__restrict__ reads,
    Interval *__restrict__ scratch, SeedSmem *__restrict__ smems, SeedCount *__restrict__ counts,
    const Prefix *__restrict__ prefixes) {
    const uint32_t id = blockIdx.x * blockDim.x + threadIdx.x;
    if (id >= p.reads) return;
    const int length = int(reads[id].length);
    const uint8_t *q = bases + reads[id].offset;
    const Scratch prev = {scratch + id, p.reads, p.scratch};
    Output out = {smems + uint64_t(id) * p.capacity, p.capacity, 0};
    SeedCount result = {seed_overflow, {0, 0, 0}, 0};
    // Pass one: getSMEMsAllPosOneThread with min_intv 1.
    for (int x = 0; x < length;) {
        x = one_position(cp, prefixes, p, q, length, x, 1, prev, out);
        if (x < 0) {
            counts[id] = result;
            return;
        }
    }
    result.pass[0] = out.size;
    // Pass two: re-seed from the middle of long, rare SMEMs.
    for (uint32_t i = 0; i < result.pass[0]; ++i) {
        const SeedSmem smem = out.data[i];
        const int start = int(smem.m), end = int(smem.n) + 1;
        if (end - start < p.split_len || smem.s > p.split_width) continue;
        result.query_bases += uint32_t(end - start);
        // Upstream's int32 min_intv.
        if (one_position(cp, prefixes, p, q, length, (end + start) >> 1, int(smem.s + 1), prev,
                out) < 0) {
            counts[id] = result;
            return;
        }
    }
    result.pass[1] = out.size - result.pass[0];
    // Pass three: bwtSeedStrategyAllPosOneThread.
    if (p.third_pass) {
        const uint32_t min_len = uint32_t(p.min_seed_len + 1);
        for (int x = 0; x < length;) {
            int next_x = x + 1;
            uint32_t a = q[x];
            if (a < 4) {
                Interval smem = initial(p, a, uint32_t(x));
                int j = x + 1;
                // Strings shorter than min_len cannot stop the walk, so it starts at the
                // deepest prefix when that has no N.
                if (seed_prefix_depth < min_len && x + int(seed_prefix_depth) <= length) {
                    uint32_t code = a, d = 1;
                    for (; d < seed_prefix_depth && q[x + int(d)] < 4; ++d)
                        code = code << 2 | q[x + int(d)];
                    if (d == seed_prefix_depth) {
                        smem = extend_prefix(prefixes, smem, d, code);
                        j = x + int(d);
                        smem.n = uint32_t(j - 1);
                        next_x = j;
                    }
                }
                for (; j < length; ++j) {
                    next_x = j + 1;
                    a = q[j];
                    if (a >= 4) break;
                    smem = forward(cp, p, smem, a);
                    smem.n = uint32_t(j);
                    if (smem.s < p.max_mem_intv && smem.n - smem.m + 1 >= min_len) {
                        if (smem.s > 0 && !out.push(smem)) {
                            counts[id] = result;
                            return;
                        }
                        break;
                    }
                }
            }
            x = next_x;
        }
    }
    result.pass[2] = out.size - result.pass[0] - result.pass[1];
    result.status = seed_ok;
    counts[id] = result;
}

} // namespace wgse::kernels::mem2_seeding
