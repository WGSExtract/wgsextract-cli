#pragma once

#include "bwamem.h"

#include <cstddef>
#include <cstdint>
#include <vector>

namespace wgse::smem {
struct Read {
    const uint8_t *seq; // 2-bit codes, as mem_collect_intv() reads them
    int length;
    std::vector<bwtintv_t> *out;
};

// mem_collect_intv() for every read, with the reads' FM-index walks interleaved:
// each read prefetches the occurrence blocks of its next bwt_extend() and lets
// the others step while they load. Each read's intervals are upstream's, in
// upstream's order (the same steps on the same data, and the same sort).
void collect(const mem_opt_t *opt, const bwt_t *bwt, Read *reads, size_t count);
} // namespace wgse::smem
