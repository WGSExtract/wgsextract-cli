// bwa-mem3's 8-bit seed extension (getScores8) with one GPU thread per pair, adapted from
// wgse-bwa-mem2's extend_striped (native/bwa-mem2/extension_tiled.metal). The host packs
// the bases of each group of 32 pairs as interleaved 4-bit words (8 bases per word) and
// gives each pair a column of DP state in `scratch`.
#include <metal_stdlib>
using namespace metal;

struct Pair {
    int idr, idq, id, len1, len2, h0, seqid, regid;
    int score, tle, gtle, qle, gscore, max_off;
    int bandwidth;
};

struct Parameters {
    int count, bandwidth, open_del, extend_del, open_ins, extend_ins;
    int zdrop, end_bonus, max_substitution;
    char matrix[25];
    bool simple_matrix;
    uchar precision;
};

struct ExtensionState {
    int best, best_i, best_j, global_score, global_end, max_offset;
    int begin, end;
    bool done;
};

int extension_width(Pair pair, constant Parameters &p) {
    int width = min(pair.bandwidth, max(1,
        (pair.len2 * p.max_substitution + p.end_bonus - p.open_ins) / p.extend_ins + 1));
    return min(width, max(1,
        (pair.len2 * p.max_substitution + p.end_bonus - p.open_del) / p.extend_del + 1));
}

void finish_row(thread ExtensionState &s, Pair pair, constant Parameters &p,
    int i, int begin, int end, int h, int best, int position, int first, int last)
{
    if (max(begin, end) == pair.len2 && h >= s.global_score) {
        s.global_score = h;
        s.global_end = i;
    }
    if (best == 0) s.done = true;
    else if (best > s.best) {
        s.best = best;
        s.best_i = i;
        s.best_j = position;
        s.max_offset = max(s.max_offset, abs(position - i));
    } else if (p.zdrop > 0) {
        int drift = (i - s.best_i) - (position - s.best_j);
        int penalty = drift > 0 ? drift * p.extend_del : -drift * p.extend_ins;
        if (s.best - best - penalty > p.zdrop) s.done = true;
    }
    s.begin = first;
    s.end = min(pair.len2, max(last, first - 1) + 2);
}

// Substitution scores of one target base against query bases.
struct RowScores {
    int match, mismatch, ambiguous, base;
    constant char *row;
};

template<bool simple>
int substitution(RowScores r, int q)
{
    if (!simple) return int(r.row[q]);
    return q == r.base ? r.match : q == 4 ? r.ambiguous : r.mismatch;
}

template<bool simple>
void serial_row(device ushort2 *boundary, const device uint *query,
    Pair pair, constant Parameters &p, thread ExtensionState &s, int i, int base, int width)
{
    int begin = max(s.begin, i - width);
    int end = min(s.end, min(pair.len2, i + width + 1));
    if (p.precision != 0 && begin >= end) {
        s.done = true;
        return;
    }
    const int e_del = p.extend_del, e_ins = p.extend_ins;
    const int oe_del = p.open_del + e_del, oe_ins = p.open_ins + e_ins;
    // In a simple matrix an N scores matrix[24] against everything.
    const int ambiguous = int(p.matrix[24]);
    RowScores scores{base == 4 ? ambiguous : int(p.matrix[0]),
        base == 4 ? ambiguous : int(p.matrix[1]), ambiguous, base, p.matrix + base * 5};
    int h = begin == 0 ? max(0, pair.h0 - oe_del - e_del * i) : 0;
    // The row maximum and its last column, packed as h * 512 + j + 1 (len2 <= 256).
    int f = 0, packed_best = 0, first = end, last = begin - 1;
    for (int tile = (begin / 8) * 8; tile < end; tile += 8) {
        uint word = query[pair.idq + (tile / 8) * 32];
        ushort2 state[8];
        #pragma unroll
        for (int k = 0; k < 8; ++k) state[k] = boundary[(tile + k) * 32];
        const bool interior = tile >= begin && tile + 8 <= end;
        #pragma unroll
        for (int k = 0; k < 8; ++k) {
            int j = tile + k;
            if (!interior && (j < begin || j >= end)) continue;
            int diagonal = int(state[k].x), e = int(state[k].y);
            int q = int((word >> (k * 4)) & 15);
            int match = diagonal == 0 ? 0 : diagonal + substitution<simple>(scores, q);
            state[k].x = ushort(h);
            h = max(match, max(e, f));
            e = max(e - e_del, max(0, match - oe_del));
            f = max(f - e_ins, max(0, match - oe_ins));
            state[k].y = ushort(e);
            if (state[k].x != 0 || e != 0) {
                first = min(first, j);
                last = j;
            }
            packed_best = max(packed_best, h * 512 + j + 1);
        }
        #pragma unroll
        for (int k = 0; k < 8; ++k) boundary[(tile + k) * 32] = state[k];
    }
    boundary[end * 32] = ushort2(h, 0);
    if (h != 0) last = end;
    finish_row(s, pair, p, i, begin, end, h, packed_best / 512, packed_best % 512 - 1,
        first, last);
}

template<bool simple>
void extend(device Pair &out, Pair pair, const device uint *reference, const device uint *query,
    constant Parameters &p, device ushort2 *boundary)
{
    for (int j = 0; j <= ((pair.len2 + 7) / 8) * 8; ++j)
        boundary[j * 32] = ushort2(j == 0 ? pair.h0
            : max(0, pair.h0 - p.open_ins - p.extend_ins * j), 0);
    ExtensionState s{pair.h0, -1, -1, -1, -1, 0, 0, pair.len2, pair.len2 == 0};
    const int width = extension_width(pair, p);
    for (int block = 0; block < pair.len1 && !s.done; block += 8) {
        uint word = reference[pair.idr + (block / 8) * 32];
        for (int r = 0; r < 8 && block + r < pair.len1 && !s.done; ++r)
            serial_row<simple>(boundary, query, pair, p, s, block + r,
                int((word >> (r * 4)) & 15), width);
    }
    out.score = s.best;
    out.qle = s.best_j + 1;
    out.tle = s.best_i + 1;
    out.gtle = s.global_end + 1;
    out.gscore = s.global_score;
    out.max_off = s.max_offset;
}

kernel void extend_striped(
    device Pair *pairs [[buffer(0)]], const device uint *reference [[buffer(1)]],
    const device uint *query [[buffer(2)]], constant Parameters &p [[buffer(3)]],
    device ushort2 *scratch [[buffer(4)]], uint index [[thread_position_in_grid]])
{
    if (index >= uint(p.count)) return;
    Pair pair = pairs[index];
    if (p.simple_matrix) extend<true>(pairs[index], pair, reference, query, p, scratch + pair.id);
    else extend<false>(pairs[index], pair, reference, query, p, scratch + pair.id);
}

int packed_base(const device uint *words, int start, int j)
{
    return int((words[start + (j >> 3) * 32] >> ((j & 7) * 4)) & 15);
}

// The longest extensions, with a SIMD group per pair (wgse-bwa-mem2's extend_batch on the same
// packed bases): a long pair on one thread would set the command buffer's latency.
kernel void extend_long(
    device Pair *pairs [[buffer(0)]], const device uint *reference [[buffer(1)]],
    const device uint *query [[buffer(2)]], constant Parameters &p [[buffer(3)]],
    uint thread_index [[thread_position_in_grid]],
    uint lane [[thread_index_in_simdgroup]],
    uint simd_index [[simdgroup_index_in_threadgroup]])
{
    uint index = thread_index / 32;
    if (index >= uint(p.count)) return;
    Pair pair = pairs[index];
    // Four SIMD groups per threadgroup. Scores fit ushort: h0 <= 32767 plus at most
    // 256 * 127 matches.
    threadgroup ushort rows_h[4][257], rows_e[4][257];
    threadgroup uchar queries[4][256];
    threadgroup ushort *h = rows_h[simd_index], *e = rows_e[simd_index];
    threadgroup uchar *q = queries[simd_index];
    for (int j = int(lane); j <= pair.len2; j += 32) {
        h[j] = j == 0 ? pair.h0 : max(0, pair.h0 - p.open_ins - p.extend_ins * j);
        e[j] = 0;
        if (j < pair.len2) q[j] = uchar(packed_base(query, pair.idq, j));
    }
    simdgroup_barrier(mem_flags::mem_threadgroup);
    const int width = extension_width(pair, p);
    int best = pair.h0, best_i = -1, best_j = -1;
    int global_score = -1, global_end = -1, max_offset = 0;
    int begin = 0, end = pair.len2;
    for (int i = 0; pair.len2 > 0 && i < pair.len1; ++i) {
        begin = max(begin, i - width);
        end = min(end, min(pair.len2, i + width + 1));
        if (p.precision != 0 && begin >= end) break;
        int previous = begin == 0 ? max(0, pair.h0 - p.open_del - p.extend_del * (i + 1)) : 0;
        int prefix_carry = -1000000000;
        int local_best = 0;
        int first_active = end, last_active = begin - 1;
        const int base = packed_base(reference, pair.idr, i);
        for (int tile = begin; tile < end; tile += 32) {
            int j = tile + int(lane);
            bool valid = j < end;
            int diagonal = valid ? h[j] : 0, deletion = valid ? e[j] : 0;
            int query_base = valid ? int(q[j]) : 0;
            int substitution = p.simple_matrix
                ? (base == 4 || query_base == 4 ? int(p.matrix[24])
                    : base == query_base ? int(p.matrix[0]) : int(p.matrix[1]))
                : int(p.matrix[base * 5 + query_base]);
            int match = !valid || diagonal == 0 ? 0 : diagonal + substitution;
            // F(j) is an exclusive prefix maximum of M(k) + k*gap_extend.
            int prefix = valid ? match + j * p.extend_ins : -1000000000;
            for (ushort distance = 1; distance < 32; distance *= 2) {
                int shifted = simd_shuffle_up(prefix, distance);
                if (lane >= distance) prefix = max(prefix, shifted);
            }
            prefix = max(prefix, prefix_carry);
            int exclusive = simd_shuffle_up(prefix, 1);
            if (lane == 0) exclusive = prefix_carry;
            int insertion = max(0, exclusive - p.open_ins - j * p.extend_ins);
            int value = max(match, max(deletion, insertion));
            int preceding = simd_shuffle_up(value, 1);
            if (lane == 0) preceding = previous;
            if (valid) {
                h[j] = preceding;
                e[j] = max(deletion - p.extend_del, max(0, match - p.open_del - p.extend_del));
                if (preceding != 0 || e[j] != 0) {
                    first_active = min(first_active, j);
                    last_active = max(last_active, j);
                }
                // Nine endpoint bits preserve rightmost ties in one reduction.
                local_best = max(local_best, value * 512 + j + 1);
            }
            previous = simd_shuffle(value, uint(min(31, end - tile - 1)));
            prefix_carry = simd_shuffle(prefix, 31);
        }
        if (lane == 0) {
            h[end] = previous;
            e[end] = 0;
            if (previous != 0) last_active = end;
        }
        int packed_best = simd_max(local_best);
        int row_best = packed_best / 512, row_j = packed_best % 512 - 1;
        if (max(begin, end) == pair.len2 && previous >= global_score) {
            global_score = previous;
            global_end = i;
        }
        if (row_best == 0) break;
        if (row_best > best) {
            best = row_best;
            best_i = i;
            best_j = row_j;
            max_offset = max(max_offset, abs(row_j - i));
        } else if (p.zdrop > 0) {
            int drift = (i - best_i) - (row_j - best_j);
            int penalty = drift > 0 ? drift * p.extend_del : -drift * p.extend_ins;
            if (best - row_best - penalty > p.zdrop) break;
        }
        begin = simd_min(first_active);
        end = min(pair.len2, max(simd_max(last_active), begin - 1) + 2);
        simdgroup_barrier(mem_flags::mem_threadgroup);
    }
    if (lane == 0) {
        pairs[index].score = best;
        pairs[index].qle = best_j + 1;
        pairs[index].tle = best_i + 1;
        pairs[index].gtle = global_end + 1;
        pairs[index].gscore = global_score;
        pairs[index].max_off = max_offset;
    }
}
