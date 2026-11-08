// Metal offload of minimap2's base-level gap fills (ksw_extd2_sse with only
// KSW_EZ_APPROX_MAX, as mm_align1 calls it between anchors). Results are exact:
// the GPU computes the same int8 cells and backtrack, and a fill is taken from
// the GPU only if its inputs are byte-identical to the ones the CPU code asks for.
#pragma once

#include <stdint.h>

#include "ksw2.h"

#ifdef __cplusplus
extern "C" {
#endif

// Set up the GPU for the rest of the process. Returns 0 and a reason if Metal
// is unavailable.
int wgse_ksw_metal_start(char *reason, int reason_size);
// Benchmark and test settings, applied by the next wgse_ksw_metal_start: reads
// in flight per CPU thread, fills per GPU batch (0 keeps the default), and
// failing every GPU batch.
void wgse_ksw_tune(int fibers_per_thread, int batch_fills, int max_cost, int fail_batches);
// Whether the calling read may queue fills for the GPU.
int wgse_ksw_metal_enabled(void);

// kt_for for mapping reads. With --metal each of n_threads CPU threads runs
// several reads as fibers and switches to another while one waits for the GPU,
// so func's tid ranges over wgse_ksw_workers(n_threads) per-read buffers.
int wgse_ksw_workers(int n_threads);
void wgse_ksw_for(int n_threads, void (*func)(void *, long, int), void *data, long n);
// A read's kalloc arena is its thread's, shared by the thread's fibers: share
// swaps it into *km and returns the read's own (NULL outside fibers), unshare
// swaps back (keeping a reset arena), and minimap2's per-read arena check and
// reset may run only when no other read on the thread holds memory in it.
void *wgse_ksw_share_km(void **km);
void wgse_ksw_unshare_km(void **km, void *own);
int wgse_ksw_km_check(void);

// One read's fills, enumerated by the prefetch before the CPU code aligns it.
// The scoring is set once per run.
void wgse_ksw_set_scoring(int q, int e, int q2, int e2, const int8_t mat[25]);
void wgse_ksw_begin_read(void);
// Whether the GPU takes a fill of this shape and band.
int wgse_ksw_eligible(int qlen, int tlen, int w);
// Queues a fill; the caller writes its tlen target bases to the result.
uint8_t *wgse_ksw_add(int qlen, const uint8_t *qseq, int tlen, int w);
// Runs the queued fills on the GPU, running other reads until they are done.
void wgse_ksw_run_read(void);
// Fills ez with the GPU result of an identical queued fill, if there is one.
int wgse_ksw_take(void *km, int qlen, const uint8_t *qseq, int tlen, const uint8_t *tseq,
    int w, int flag, ksw_extz_t *ez);

// Summary on stderr; whether a GPU batch failed (its fills ran on the CPU).
void wgse_ksw_print_metrics(void);
int wgse_ksw_metal_failed(char *reason, int reason_size);

#ifdef __cplusplus
}
#endif
