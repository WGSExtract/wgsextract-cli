#pragma once

// Shared by wgse-bwa's GPU scheduling and wgse-bwa-mem2's Metal backend, each
// with its own bwamem.h (mem_opt_t).
#include "rescue_types.h"

#include "bwamem.h"

namespace wgse::rescue {
// The rescue kernel's parameters for opt's scoring, or false if ksw_u8 would
// score differently than it models: a bwa_fill_scmat-style matrix (one match,
// one mismatch and one N score), a positive match score and byte-sized gaps.
inline bool parameters(const mem_opt_t &opt, RescueParameters &p) {
    const int8_t *mat = opt.mat;
    p = {};
    p.match = mat[0];
    p.mismatch = mat[1];
    p.ambiguous = mat[4];
    int lowest = 127, highest = 0;
    for (int i = 0; i < 5; ++i)
        for (int j = 0; j < 5; ++j) {
            const int value = mat[i * 5 + j];
            const int expected = i == 4 || j == 4 ? p.ambiguous : i == j ? p.match : p.mismatch;
            if (value != expected) return false;
            lowest = value < lowest ? value : lowest;
            highest = value > highest ? value : highest;
        }
    // ksw_qinit's uint8_t shift and max.
    p.shift = -lowest;
    p.max = highest;
    p.o_del = opt.o_del;
    p.e_del = opt.e_del;
    p.o_ins = opt.o_ins;
    p.e_ins = opt.e_ins;
    const bool gaps = opt.o_del >= 0 && opt.e_del >= 0 && opt.o_ins >= 0 && opt.e_ins >= 0
        && opt.o_del + opt.e_del <= 255 && opt.o_ins + opt.e_ins <= 255;
    return lowest < 0 && highest > 0 && gaps;
}
} // namespace wgse::rescue
