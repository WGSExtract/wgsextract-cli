// What the CUDA seed extension (cuda_extension.cu) and seeding (cuda_seeding.cu) share.
#pragma once

#include <string>

// The GPU both use: of those this build has kernels for, the one with the most free memory
// when first asked (the GPU may be shared); -1 with the reason if there is none.
int wgse_mem3_cuda_device(std::string &reason);
// Test hook (--gpu-inject-hang LABEL): whether the first GPU command of `stage` should hang,
// once, when LABEL is a prefix of it; and the watchdog's floor in seconds (60, or 2 with the
// hook).
bool wgse_mem3_cuda_take_hang(const char *stage);
double wgse_mem3_cuda_watchdog_floor();
// Allocates every stage's batch buffers at their largest, once, before the first GPU work
// (the seeder's first batch, or the first extension): an allocation can wait for the GPU's
// running kernels, so none may happen once a stage could be waiting on a hung one. After the
// index loads, as pinned memory then slowed loading it and the CPU's index lookups (#674).
void wgse_mem3_cuda_prepare();
// Mate rescue's part of it (cuda_rescue.cu).
void wgse_mem3_rescue_prepare();
// Why the watchdog gave up on the GPU (the first hung command), or empty while it hasn't;
// once set, no stage uses the GPU again.
std::string wgse_mem3_cuda_hang();
void wgse_mem3_cuda_set_hang(const std::string &reason);
