// The adapter to bwa-mem3's rescue pruning (wgse_rescue_prune.h). Built
// alone: bwa-mem3's source directory has headers named like BWA-MEM2's.
#include "wgse_rescue_prune.h"
#include "rescue_prune.h"  // bwa-mem3's, from its source directory

namespace {
// bwa-mem3's hit gate without its banded rescue kernel (BWA-MEM2 has none):
// denser windows cost the filter more than the rows it saves.
const int max_hits = rescue_prune_max_hits_default(false, false);
}

int wgse_rescue_prune(int a, int b, int o_del, int e_del, int o_ins, int e_ins, int minsc,
    const uint8_t *ref, int len1, const uint8_t *q, int len2, int *hb, int *he) {
    // One scoring per run, so the parameters are derived once per thread.
    struct Cache {
        int key[7] = {-1, -1, -1, -1, -1, -1, -1};
        rescue_prune_params params;
    };
    static thread_local Cache cache;
    const int key[7] = {a, b, o_del, e_del, o_ins, e_ins, minsc};
    bool same = true;
    for (int i = 0; i < 7; ++i) same &= cache.key[i] == key[i];
    if (!same) {
        // bwa-mem3 production: K-mers from 5 up to its filters' largest.
        cache.params = rescue_prune_params::from(a, b, o_del, e_del, o_ins, e_ins, minsc, 5,
            rescue_prune_scratch_kmax);
        for (int i = 0; i < 7; ++i) cache.key[i] = key[i];
    }
    switch (rescue_prune_window(ref, len1, q, len2, cache.params, max_hits, hb, he)) {
    case RESCUE_PRUNE_B1: return wgse_prune_none;
    case RESCUE_PRUNE_B2: return wgse_prune_rows;
    default: return wgse_prune_full;
    }
}
