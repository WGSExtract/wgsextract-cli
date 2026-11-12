// A build without GPU seeding: bwa-mem3 collects every read's SMEMs on the CPU.
#include "metal_seeding.h"

void wgse_mem3_seed_set_enabled(bool) {}
void wgse_mem3_seed_set_verify(bool) {}
void wgse_mem3_seed_set_wait(bool) {}
void wgse_mem3_seed_inject_failure() {}
bool wgse_mem3_seed_finish() { return false; }
void wgse_mem3_seed_preseed(FMI_search *, const mem_opt_t *, const bseq1_t *, int) {}
void wgse_mem3_seed_chunk(FMI_search *, const mem_opt_t *, const bseq1_t *, int) {}
void wgse_mem3_seed_forget(const bseq1_t *) {}
bool wgse_mem3_seed_release(const FMI_search *) { return false; }
void wgse_mem3_seed_chunk_done(const bseq1_t *) {}
bool wgse_mem3_seed_collect(FMI_search *, const mem_opt_t *, const bseq1_t *, int, mem_cache *,
    int64_t &, int, SMEM *&) {
    return false;
}
