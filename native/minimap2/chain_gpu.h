// GPU offload of mg_lchain_dp's score fill (Linux CUDA builds): f[], p[] and v[] for
// the anchors a[0..n), identical to upstream's loop, now wgse_lchain_dp_fill in the
// generated lchain.c. The fill splits into independent runs of anchors wherever the
// next anchor is too far along the reference to chain to any earlier one; the GPU
// fills each run.
#pragma once

#include <stdint.h>

#include "minimap.h"

#ifdef __cplusplus
extern "C" {
#endif

// Upstream's fill loop, verbatim; t[] must be zero.
void wgse_lchain_dp_fill(int max_dist_x, int max_dist_y, int bw, int max_skip, int max_iter,
    float chn_pen_gap, float chn_pen_skip, int is_cdna, int n_seg, int64_t n, const mm128_t *a,
    int32_t *f, int64_t *p, int32_t *v, int32_t *t);

// Fills f, p and v on the GPU, running other reads while it waits (t[] is zero, and
// scratch). Returns 0 if the
// GPU did not (no GPU, not a read's fiber, an unsupported job, or a failed batch).
int wgse_chain_dp_gpu(int max_dist_x, int max_dist_y, int bw, int max_skip, int max_iter,
    float chn_pen_gap, float chn_pen_skip, int is_cdna, int n_seg, int64_t n, const mm128_t *a,
    int32_t *f, int64_t *p, int32_t *v, int32_t *t);

// Set up chaining on the GPU of wgse_ksw_gpu_start's engine (call after it succeeds).
// Returns 0 and a reason if it is unavailable; chaining then stays on the CPU.
int wgse_chain_gpu_start(char *reason, int reason_size);
// Benchmark and test settings, applied by the next wgse_chain_gpu_start: anchors per
// GPU batch, the shortest and longest runs the GPU fills (0 keeps the defaults), and
// failing every batch.
void wgse_chain_tune(int batch_anchors, int min_gpu_run, int max_gpu_run, int fail_batches);
// Tests only: withhold the first chaining batch if label is a prefix of "chain_fill",
// as a hung GPU would, giving up on it after timeout seconds.
void wgse_chain_inject_hang(const char *label, double timeout);
// Summary on stderr; whether a GPU batch failed (its runs were filled on the CPU).
void wgse_chain_print_metrics(void);
int wgse_chain_gpu_failed(char *reason, int reason_size);

#ifdef __cplusplus
}
#endif
