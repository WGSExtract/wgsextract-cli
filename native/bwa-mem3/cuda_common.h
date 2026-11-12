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
// Why the watchdog gave up on the GPU (the first hung command), or empty while it hasn't;
// once set, no stage uses the GPU again.
std::string wgse_mem3_cuda_hang();
void wgse_mem3_cuda_set_hang(const std::string &reason);
