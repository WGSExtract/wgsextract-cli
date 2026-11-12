#pragma once

// GPU mate rescue for bwa-mem3's batched kswv (mem_sam_pe_batch): the 8-bit pairs' ksw_align2,
// both passes at once, on the GPU, with the CPU's kswv for the pairs the GPU leaves.
#include "bandedSWA.h"
#include "bwamem.h"
#include "ksw.h"

#include "rescue_api.h"

// While it lives, this thread's mem_sam_pe_batch_run (non-meth, `count` pairs keyed by
// regid) may use the GPU.
class WgseRescueScope {
public:
    WgseRescueScope(const mem_opt_t *opt, int64_t count);
    ~WgseRescueScope();
    WgseRescueScope(const WgseRescueScope &) = delete;
    WgseRescueScope &operator=(const WgseRescueScope &) = delete;
};

// Pass 0 (getScores8, phase 0) of `count` 8-bit pairs: aligns those it can on the GPU, both
// passes, into aln[regid], and returns the pairs the CPU must still score, setting count to
// their number (with room for kswv's padding lanes after them).
SeqPair *wgse_mem3_rescue_pass0(SeqPair *pairs, int64_t &count, const uint8_t *ref,
    const uint8_t *qer, kswr_t *aln);
// Pass 1 (getScores8, phase 1): the pairs whose start the GPU has not found already.
SeqPair *wgse_mem3_rescue_pass1(SeqPair *pairs, int64_t &count);
