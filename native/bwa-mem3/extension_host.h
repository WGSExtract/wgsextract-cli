// The host side of the GPU seed extension that the Metal and CUDA backends share: the
// kernel's parameters, and each getScores8 call's pairs sorted and packed for the kernel.
#pragma once

#include "metal_extension.h"

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <vector>

namespace wgse::mem3 {

// extension.metal's Parameters.
struct Parameters {
    int32_t count, bandwidth, open_deletion, extend_deletion, open_insertion, extend_insertion;
    int32_t zdrop, end_bonus, max_substitution;
    int8_t matrix[25];
    bool simple_matrix;
    uint8_t precision;
};
static_assert(sizeof(Parameters) == 64, "extension.metal's Parameters");
static_assert(sizeof(WgseBswJob) == 15 * sizeof(int32_t), "extension.metal's Pair");

// Threads of a group of pairs, whose bases extension.metal reads as interleaved words.
constexpr int32_t lanes = 32;

inline bool same_scoring(const Parameters &a, const Parameters &b) {
    return std::memcmp(&a.open_deletion, &b.open_deletion,
        sizeof(Parameters) - offsetof(Parameters, open_deletion)) == 0;
}

inline bool scoring_less(const Parameters &a, const Parameters &b) {
    return std::memcmp(&a.open_deletion, &b.open_deletion,
        sizeof(Parameters) - offsetof(Parameters, open_deletion)) < 0;
}

inline uint64_t now_ns() {
    return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
}

// One worker's getScores8 call, packed for extension.metal, waiting for its results.
struct Request {
    WgseBswJob *jobs;
    int32_t count;
    Parameters parameters;
    // The pairs sorted by length (seqid: the index in jobs), with offsets relative to the
    // request's words and scratch.
    const WgseBswJob *packed;
    const uint32_t *ref_words, *qer_words;
    size_t ref_word_count, qer_word_count, scratch_cells;
    // packed's last long_count pairs go to extend_long.
    int32_t long_count;
    uint64_t enqueued;
    bool done = false, ok = false;
};

// Band cells above which a pair is scored by a SIMD group (extend_long), not one thread.
constexpr int64_t long_cells = 30000;

// extension.metal's Parameters for a getScores8 call's scoring.
inline Parameters parameters(const WgseBswScoring &scoring) {
    Parameters p{};
    p.open_deletion = scoring.open_deletion;
    p.extend_deletion = scoring.extend_deletion;
    p.open_insertion = scoring.open_insertion;
    p.extend_insertion = scoring.extend_insertion;
    p.zdrop = scoring.zdrop;
    p.end_bonus = scoring.end_bonus;
    p.simple_matrix = true;
    p.precision = 8;
    const int8_t *m = scoring.matrix;
    for (int i = 0; i < 25; ++i) {
        p.matrix[i] = m[i];
        p.max_substitution = std::max<int32_t>(p.max_substitution, m[i]);
        const int r = i / 5, c = i % 5;
        p.simple_matrix &= m[i] == (r == 4 || c == 4 ? m[24] : r == c ? m[0] : m[1]);
    }
    return p;
}

// Eight bases (each at most 4) as the 4-bit fields of a word, the first base lowest.
inline uint32_t pack_bases(const uint8_t *bases, int32_t length) {
    if (length >= 8) {
        uint64_t x;
        std::memcpy(&x, bases, sizeof(x));
        x = (x | x >> 4) & 0x00ff00ff00ff00ffull;
        x = (x | x >> 8) & 0x0000ffff0000ffffull;
        return uint32_t(x | x >> 16);
    }
    uint32_t word = 0;
    for (int32_t k = 0; k < length; ++k) word |= uint32_t(bases[k]) << (k * 4);
    return word;
}

// Sorts the pairs by length into groups of `lanes`, and packs each group's bases as
// interleaved words, one column per lane, with its DP state columns after the last group's.
inline void pack(Request &request, const WgseBswJob *jobs, int32_t count, const uint8_t *ref,
    const uint8_t *qer) {
    thread_local std::vector<int32_t> order;
    thread_local std::vector<WgseBswJob> packed;
    thread_local std::vector<uint32_t> ref_words, qer_words;
    order.resize(size_t(count));
    for (int32_t i = 0; i < count; ++i) order[size_t(i)] = i;
    const auto is_long = [jobs](int32_t i) {
        return int64_t(jobs[i].len1) * std::min(jobs[i].len2, 2 * jobs[i].bandwidth + 1) > long_cells;
    };
    const auto split = std::stable_partition(order.begin(), order.end(),
        [&is_long](int32_t i) { return !is_long(i); });
    const int32_t short_count = int32_t(split - order.begin());
    const auto shorter = [jobs](int32_t a, int32_t b) {
        const int32_t qa = (jobs[a].len2 + 7) / 8, qb = (jobs[b].len2 + 7) / 8;
        return qa != qb ? qa < qb : jobs[a].len1 < jobs[b].len1;
    };
    std::stable_sort(order.begin(), split, shorter);
    packed.resize(size_t(count));
    ref_words.clear();
    qer_words.clear();
    size_t cells = 0;
    // Groups of one-thread pairs, then of long pairs.
    for (int32_t first = 0, last; first < count; first = last) {
        const bool long_group = first >= short_count;
        last = std::min(long_group ? count : short_count, first + lanes);
        int32_t rmax = 0, qmax = 0;
        for (int32_t i = first; i < last; ++i) {
            rmax = std::max(rmax, (jobs[order[size_t(i)]].len1 + 7) / 8);
            qmax = std::max(qmax, (jobs[order[size_t(i)]].len2 + 7) / 8);
        }
        const size_t rbase = ref_words.size(), qbase = qer_words.size();
        ref_words.resize(rbase + size_t(rmax) * lanes);
        qer_words.resize(qbase + size_t(qmax) * lanes);
        for (int32_t i = first; i < last; ++i) {
            const int32_t lane = i - first, original = order[size_t(i)];
            WgseBswJob job = jobs[original];
            const uint8_t *r = ref + job.idr, *q = qer + job.idq;
            for (int32_t w = 0; w < (job.len1 + 7) / 8; ++w)
                ref_words[rbase + size_t(lane) + size_t(w) * lanes] = pack_bases(r + 8 * w, job.len1 - 8 * w);
            for (int32_t w = 0; w < (job.len2 + 7) / 8; ++w)
                qer_words[qbase + size_t(lane) + size_t(w) * lanes] = pack_bases(q + 8 * w, job.len2 - 8 * w);
            job.idr = int32_t(rbase) + lane;
            job.idq = int32_t(qbase) + lane;
            job.id = int32_t(cells) + lane;
            job.seqid = original;
            packed[size_t(i)] = job;
        }
        // A lane's DP state: one cell per query base, and one past the last word's. Long
        // pairs keep theirs in threadgroup memory.
        if (!long_group) cells += size_t(qmax * 8 + 1) * lanes;
    }
    request.long_count = count - short_count;
    request.packed = packed.data();
    request.ref_words = ref_words.data();
    request.qer_words = qer_words.data();
    request.ref_word_count = ref_words.size();
    request.qer_word_count = qer_words.size();
    request.scratch_cells = cells;
}

} // namespace wgse::mem3
