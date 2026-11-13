// The gap-fill offload API (ksw_gpu.h) for builds without a GPU backend: every
// fill runs on the CPU, in upstream's code, and mapping uses kt_for as upstream does.
#include "chain_gpu.h"
#include "ksw_gpu.h"

extern "C" {
#include "kthread.h"
}

#include <cstdio>

extern "C" {

int wgse_ksw_gpu_start(char *reason, int reason_size) {
    snprintf(reason, reason_size, "this build has no GPU backend");
    return 0;
}
int wgse_ksw_gpu_probe(char *reason, int reason_size) {
    return wgse_ksw_gpu_start(reason, reason_size);
}
void wgse_ksw_tune(int, int, int, int) {}
int wgse_ksw_gpu_enabled(void) { return 0; }
int wgse_ksw_gpu_hardware_supported(void) { return 0; }
void wgse_ksw_inject_interruptions(int) {}
void wgse_ksw_inject_hang(const char *, double) {}

int wgse_ksw_workers(int n_threads) { return n_threads; }
void wgse_ksw_for(int n_threads, void (*func)(void *, long, int), void *data, long n) {
    kt_for(n_threads, func, data, n);
}
void *wgse_ksw_share_km(void **) { return nullptr; }
void wgse_ksw_unshare_km(void **, void *) {}
int wgse_ksw_km_check(const void *, int64_t) { return 1; }
void wgse_ksw_km_reset(void *) {}

// Never called: wgse_ksw_gpu_enabled is 0, so align.c's prefetch queues nothing.
void wgse_ksw_set_scoring(int, int, int, int, const int8_t[25]) {}
void wgse_ksw_begin_read(void) {}
int wgse_ksw_eligible(int, int, int) { return 0; }
uint8_t *wgse_ksw_add(int, const uint8_t *, int, int) { return nullptr; }
void wgse_ksw_run_read(void) {}
int wgse_ksw_take(void *, int, const uint8_t *, int, const uint8_t *, int, int, ksw_extz_t *) {
    return 0;
}

int wgse_chain_dp_gpu(int, int, int, int, int, float, float, int, int, int64_t, const mm128_t *,
    int32_t *, int64_t *, int32_t *) {
    return 0;
}

int wgse_chain_gpu_start(char *reason, int reason_size) {
    return wgse_ksw_gpu_start(reason, reason_size);
}
void wgse_chain_tune(int, int, int) {}
void wgse_chain_inject_hang(const char *, double) {}
void wgse_chain_print_metrics(void) {}
int wgse_chain_gpu_failed(char *, int) { return 0; }

void wgse_ksw_print_metrics(void) {}
int wgse_ksw_gpu_failed(char *, int) { return 0; }

} // extern "C"
