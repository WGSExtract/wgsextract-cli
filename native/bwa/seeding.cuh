// BWA 0.7.19's bidirectional FM-index traversal, three-pass MEM collection and
// suffix-array locate for CUDA/HIP: a port of seeding.metal, one read (or row)
// per thread. Adapted from bwt.c/bwamem.c (Heng Li et al., MIT; see SEEDING-LICENSE).
#pragma once

#include "portable.h"
#include "seeding_types.h"

namespace wgse::kernels::seeding {

struct U64x4 {
    uint64_t v[4];
};

struct SeedVector {
    SeedInterval *data;
    uint32_t stride, size, capacity;

    WGSE_DEVICE SeedInterval get(uint32_t i) const { return data[uint64_t(i) * stride]; }
    WGSE_DEVICE void set(uint32_t i, SeedInterval value) { data[uint64_t(i) * stride] = value; }
    WGSE_DEVICE bool push(SeedInterval value) {
        if (size == capacity) return false;
        set(size++, value);
        return true;
    }
    WGSE_DEVICE void reverse() {
        for (uint32_t i = 0; i < size / 2; ++i) {
            SeedInterval value = get(i);
            set(i, get(size - 1 - i));
            set(size - 1 - i, value);
        }
    }
};

// Occurrences within a 128-symbol block, read as four 64-bit lanes (words 2i and
// 2i + 1). Symbol j of a 32-bit word is bits 31 - 2j (high) and 30 - 2j (low).
WGSE_DEVICE uint64_t seed_keep(uint32_t lane, uint64_t k) {
    const uint32_t word = uint32_t(k & 127) >> 4;
    const uint32_t partial = ~((1u << ((~uint32_t(k) & 15) * 2)) - 1) & 0x55555555u;
    const uint32_t lo = 2 * lane < word ? 0x55555555u : 2 * lane == word ? partial : 0u;
    const uint32_t hi = 2 * lane + 1 < word ? 0x55555555u : 2 * lane + 1 == word ? partial : 0u;
    return uint64_t(lo) | (uint64_t(hi) << 32);
}

// Counts of A, C, G and T up to and including symbol k & 127 of its block.
WGSE_DEVICE U64x4 seed_count(const uint64_t *data, uint64_t k) {
    uint64_t c = 0, g = 0, t = 0;
    for (uint32_t lane = 0; lane < 4; ++lane) {
        const uint64_t keep = seed_keep(lane, k), lo = data[lane] & keep,
                       hi = (data[lane] >> 1) & keep;
        c += popcount64(lo & ~hi);
        g += popcount64(hi & ~lo);
        t += popcount64(lo & hi);
    }
    return {{(k & 127) + 1 - c - g - t, c, g, t}};
}

WGSE_DEVICE U64x4 seed_occ4(const uint32_t *bwt, uint64_t k, const SeedParameters &p) {
    if (k == uint64_t(-1)) return {{0, 0, 0, 0}};
    k -= k >= p.primary;
    const uint32_t *block = bwt + (k >> 7) * 16;
    const auto *counts = reinterpret_cast<const uint64_t *>(block);
    const U64x4 within = seed_count(reinterpret_cast<const uint64_t *>(block + 8), k);
    return {{counts[0] + within.v[0], counts[1] + within.v[1], counts[2] + within.v[2],
        counts[3] + within.v[3]}};
}

WGSE_DEVICE SeedInterval seed_initial(uint32_t c, const SeedParameters &p) {
    return {{p.l2[c] + 1, p.l2[3 - c] + 1, p.l2[c + 1] - p.l2[c]}, 0};
}

WGSE_DEVICE SeedInterval seed_extend(const uint32_t *bwt, SeedInterval interval, uint32_t c,
    bool back, const SeedParameters &p) {
    const uint32_t side = !back;
    const uint64_t begin = interval.x[side];
    const U64x4 left = seed_occ4(bwt, begin - 1, p);
    const U64x4 right = seed_occ4(bwt, begin - 1 + interval.x[2], p);
    uint64_t sizes[4];
    for (int i = 0; i < 4; ++i) sizes[i] = right.v[i] - left.v[i];
    SeedInterval out = interval;
    out.x[side] = p.l2[c] + 1 + left.v[c];
    out.x[2] = sizes[c];
    out.x[back] += begin <= p.primary && begin + interval.x[2] - 1 >= p.primary;
    for (uint32_t i = c + 1; i < 4; ++i) out.x[back] += sizes[i];
    return out;
}

// mem_collect_intv always calls bwt_smem1a with max_intv == 0.
WGSE_DEVICE int seed_smem(const uint32_t *bwt, const uint8_t *q, int len, int x,
    uint64_t minimum, const SeedParameters &p, SeedVector &mem, SeedVector prev,
    SeedVector curr) {
    mem.size = 0;
    if (q[x] > 3) return x + 1;
    minimum = minimum > 1 ? minimum : 1;
    SeedInterval ik = seed_initial(q[x], p);
    ik.info = uint64_t(x + 1);
    int i;
    curr.size = 0;
    for (i = x + 1; i < len; ++i) {
        if (q[i] < 4) {
            SeedInterval next = seed_extend(bwt, ik, 3 - q[i], false, p);
            if (next.x[2] != ik.x[2]) {
                if (!curr.push(ik)) return -1;
                if (next.x[2] < minimum) break;
            }
            ik = next;
            ik.info = uint64_t(i + 1);
        } else {
            if (!curr.push(ik)) return -1;
            break;
        }
    }
    if (i == len && !curr.push(ik)) return -1;
    curr.reverse();
    const int ret = int(curr.get(0).info);
    SeedVector swap = curr;
    curr = prev;
    prev = swap;
    for (i = x - 1; i >= -1; --i) {
        const int c = i < 0 ? -1 : q[i] < 4 ? q[i] : -1;
        curr.size = 0;
        for (uint32_t j = 0; j < prev.size; ++j) {
            SeedInterval candidate = prev.get(j), next{};
            if (c >= 0) next = seed_extend(bwt, candidate, uint32_t(c), true, p);
            if (c < 0 || next.x[2] < minimum) {
                if (curr.size == 0 &&
                    (mem.size == 0 || uint64_t(i + 1) < (mem.get(mem.size - 1).info >> 32))) {
                    candidate.info |= uint64_t(i + 1) << 32;
                    if (!mem.push(candidate)) return -1;
                }
            } else if (curr.size == 0 || next.x[2] != curr.get(curr.size - 1).x[2]) {
                next.info = candidate.info;
                if (!curr.push(next)) return -1;
            }
        }
        if (curr.size == 0) break;
        swap = curr;
        curr = prev;
        prev = swap;
    }
    mem.reverse();
    return ret;
}

WGSE_DEVICE bool seed_append_long(SeedVector &out, const SeedVector &mem, int min_len) {
    for (uint32_t i = 0; i < mem.size; ++i) {
        const SeedInterval interval = mem.get(i);
        if (uint32_t(interval.info) - (interval.info >> 32) >= uint64_t(min_len)
            && !out.push(interval)) return false;
    }
    return true;
}

__global__ void collect_seeds(const uint32_t *__restrict__ bwt, SeedParameters p,
    const uint8_t *__restrict__ sequences, const SeedRead *__restrict__ reads,
    SeedInterval *scratch, SeedInterval *output, SeedResult *results) {
    const uint32_t tid = blockIdx.x * blockDim.x + threadIdx.x;
    if (tid >= p.count) return;
    SeedResult result{0, seed_ok};
    const int len = int(reads[tid].length);
    if (len > int(seed_max_length)) {
        results[tid] = {0, seed_length_limit};
        return;
    }
    const uint8_t *q = sequences + reads[tid].offset;
    const uint64_t region = uint64_t(seed_scratch_capacity) * p.count;
    SeedVector mem = {scratch + tid, p.count, 0, p.scratch_limit};
    SeedVector prev = {scratch + region + tid, p.count, 0, p.scratch_limit};
    SeedVector curr = {scratch + 2 * region + tid, p.count, 0, p.scratch_limit};
    SeedVector out = {output + uint64_t(tid) * seed_output_capacity, 1, 0, p.output_limit};
    for (int x = 0; x < len;) {
        if (q[x] >= 4) {
            ++x;
            continue;
        }
        x = seed_smem(bwt, q, len, x, 1, p, mem, prev, curr);
        if (x < 0) {
            results[tid] = {0, seed_scratch_overflow};
            return;
        }
        if (!seed_append_long(out, mem, p.min_seed_len)) {
            results[tid] = {0, seed_output_overflow};
            return;
        }
    }
    const uint32_t old_size = out.size;
    for (uint32_t k = 0; k < old_size; ++k) {
        const SeedInterval interval = out.get(k);
        const int start = int(interval.info >> 32), end = int(interval.info);
        if (end - start < p.split_len || interval.x[2] > uint64_t(p.split_width)) continue;
        if (seed_smem(bwt, q, len, (start + end) >> 1, interval.x[2] + 1, p, mem, prev, curr)
            < 0) {
            results[tid] = {0, seed_scratch_overflow};
            return;
        }
        if (!seed_append_long(out, mem, p.min_seed_len)) {
            results[tid] = {0, seed_output_overflow};
            return;
        }
    }
    if (p.max_mem_intv > 0) {
        for (int x = 0; x < len;) {
            if (q[x] >= 4) {
                ++x;
                continue;
            }
            SeedInterval interval = seed_initial(q[x], p);
            int i;
            for (i = x + 1; i < len; ++i) {
                if (q[i] >= 4) break;
                interval = seed_extend(bwt, interval, 3 - q[i], false, p);
                if (interval.x[2] < uint64_t(p.max_mem_intv) && i - x >= p.min_seed_len) {
                    interval.info = (uint64_t(x) << 32) | uint32_t(i + 1);
                    if (interval.x[2] > 0 && !out.push(interval)) {
                        results[tid] = {0, seed_output_overflow};
                        return;
                    }
                    break;
                }
            }
            x = i == len ? len : i + 1;
        }
    }
    for (uint32_t i = 1; i < out.size; ++i) {
        const SeedInterval value = out.get(i);
        uint32_t j = i;
        while (j > 0 && value.info < out.get(j - 1).info) {
            out.set(j, out.get(j - 1));
            --j;
        }
        out.set(j, value);
    }
    result.count = out.size;
    results[tid] = result;
}

// bwt_sa(): walk inverse-Psi (LF) steps to the next sampled suffix-array row.
// Mirrors bwt_invPsi/bwt_occ/bwt_B0 in bwt.c exactly, including sa[0] == -1.
WGSE_DEVICE uint64_t locate_occ(const uint32_t *bwt, uint64_t k, uint32_t c,
    const LocateParameters &p) {
    if (k == p.seq_len) return p.l2[c + 1] - p.l2[c];
    k -= k >= p.primary;
    const uint32_t *block = bwt + (k >> 7) * 16;
    const auto *data = reinterpret_cast<const uint64_t *>(block + 8);
    const uint64_t pattern = uint64_t(c) * 0x5555555555555555ull;
    uint64_t count = 0;
    for (uint32_t lane = 0; lane < 4; ++lane) {
        const uint64_t x = data[lane] ^ pattern;
        count += popcount64(~(x | (x >> 1)) & seed_keep(lane, k));
    }
    return reinterpret_cast<const uint64_t *>(block)[c] + count;
}

// Walks LF from row k until (k & mask) == 0 and returns the number of steps.
WGSE_DEVICE uint64_t locate_walk(const uint32_t *bwt, uint64_t &k, uint64_t mask,
    const LocateParameters &p) {
    uint64_t steps = 0;
    while (k & mask) {
        ++steps;
        if (k == p.primary) {
            k = 0;
            continue;
        }
        const uint64_t x = k - (k > p.primary);
        const uint32_t c = (bwt[(x >> 7 << 4) + 8 + ((x & 127) >> 4)] >> ((~uint32_t(x) & 15) << 1)) & 3;
        k = p.l2[c] + locate_occ(bwt, k, c, p);
    }
    return steps;
}

__global__ void locate_rows(const uint32_t *__restrict__ bwt, const uint64_t *__restrict__ sa,
    LocateParameters p, const uint64_t *__restrict__ rows, uint64_t *positions) {
    const uint32_t id = blockIdx.x * blockDim.x + threadIdx.x;
    if (id >= p.count) return;
    uint64_t k = rows[id];
    const uint64_t steps = locate_walk(bwt, k, p.sa_intv - 1, p);
    positions[id] = steps + sa[k >> p.sa_shift];
}

// Denser suffix-array samples: the rows that are multiples of 1 << dense_shift
// but not of sa_intv, stored as low words plus a bit vector of high bits.
__global__ void locate_rows_dense(const uint32_t *__restrict__ bwt,
    const uint64_t *__restrict__ sa, LocateParameters p, const uint64_t *__restrict__ rows,
    uint64_t *positions, const uint32_t *__restrict__ low, const uint32_t *__restrict__ high) {
    const uint32_t id = blockIdx.x * blockDim.x + threadIdx.x;
    if (id >= p.count) return;
    uint64_t k = rows[id];
    const uint64_t base = p.sa_intv - 1;
    const uint64_t steps = locate_walk(bwt, k, (uint64_t(1) << p.dense_shift) - 1, p);
    if (!(k & base)) {
        positions[id] = steps + sa[k >> p.sa_shift];
        return;
    }
    const uint64_t per = (p.sa_intv >> p.dense_shift) - 1;
    const uint64_t i = (k >> p.sa_shift) * per + ((k & base) >> p.dense_shift) - 1;
    positions[id] = steps + (low[i] | (uint64_t((high[i >> 5] >> (i & 31)) & 1) << 32));
}

// Builds the dense samples, one per thread, from entry dense_offset (a multiple
// of 32, so each group of 32 lanes owns one word of high bits). Blocks are whole
// groups of 32 threads; entries for rows past seq_len are left zero.
__global__ void locate_dense_build(const uint32_t *__restrict__ bwt,
    const uint64_t *__restrict__ sa, LocateParameters p, uint32_t *low, uint32_t *high) {
    const uint32_t id = blockIdx.x * blockDim.x + threadIdx.x;
    const uint64_t i = uint64_t(id) + p.dense_offset, per = (p.sa_intv >> p.dense_shift) - 1;
    uint64_t k = (i / per) * p.sa_intv + ((i % per + 1) << p.dense_shift), value = 0;
    const bool present = id < p.count && i < p.dense_entries;
    if (present && k <= p.seq_len) {
        const uint64_t steps = locate_walk(bwt, k, p.sa_intv - 1, p);
        value = steps + sa[k >> p.sa_shift];
    }
    if (present) low[i] = uint32_t(value);
    uint32_t bits = uint32_t(value >> 32) << (i & 31);
    for (int offset = 16; offset > 0; offset >>= 1) bits |= wgse_shfl_xor(bits, offset, 32);
    if (threadIdx.x % 32 == 0 && i < p.dense_entries) high[i >> 5] = bits;
}
} // namespace wgse::kernels::seeding
