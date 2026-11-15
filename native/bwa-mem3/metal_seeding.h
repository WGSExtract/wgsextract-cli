#pragma once

// GPU SMEM collection (--metal) for bwa-mem3's mem_collect_smem, with wgse-bwa-mem2's
// seeding.metal: the three passes give the same SMEMs as upstream's, which then sorts,
// tags and deduplicates them per read.
#include <cstdint>

#include "FMI_search.h"
#include "bwamem.h"

#include "metal_seeding_api.h"

// Queues a chunk's reads (ASCII or 2-bit bases) for GPU seeding as soon as they are read.
void wgse_mem3_seed_preseed(FMI_search *fmi, const mem_opt_t *opt, const bseq1_t *seqs, int n);
// The chunk starts to map: the GPU stops reading its bases, and its finished batches are used.
void wgse_mem3_seed_chunk(FMI_search *fmi, const mem_opt_t *opt, const bseq1_t *seqs, int n);
// The reads are about to be freed: the GPU stops reading them if it still does.
void wgse_mem3_seed_forget(const bseq1_t *seqs);
// The index is about to be freed (FMI_search's destructor): GPU seeding stops. True if an
// abandoned GPU command (see Runtime::unsettled) may still read it, so it must not be freed.
bool wgse_mem3_seed_release(const FMI_search *fmi);
// The chunk has mapped: its GPU SMEMs are dropped.
void wgse_mem3_seed_chunk_done(const bseq1_t *seqs);
// mem_collect_smem for these reads from the GPU's SMEMs (and the CPU's for reads the GPU
// left), or false if upstream must collect them.
bool wgse_mem3_seed_collect(FMI_search *fmi, const mem_opt_t *opt, const bseq1_t *seq, int nseq,
    mem_cache *mmc, int64_t &total, int tid, SMEM *&smems);
