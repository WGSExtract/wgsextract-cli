// Compiled only for the benchmark. Keep the oracle's three-pass collection and
// sorting literally upstream, including its reusable per-worker scratch.
#include "bwamem.c"
#include "seed_oracle.h"

void *wgse_seed_scratch_create(void) {
    return smem_aux_init();
}

void wgse_seed_scratch_destroy(void *scratch) {
    smem_aux_destroy(scratch);
}

const bwtintv_v *wgse_seed_collect(const mem_opt_t *options, const bwt_t *bwt,
    int length, const uint8_t *sequence, void *scratch) {
    smem_aux_t *aux = scratch;
    mem_collect_intv(options, bwt, length, sequence, aux);
    return &aux->mem;
}
