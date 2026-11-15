// A build without a GPU backend (Linux without -DWGSE_CUDA=ON): bwa-mem3's CPU code only.
#include "metal_extension.h"

namespace {
constexpr const char *no_gpu = "this build has no GPU backend";
}

const char *wgse_mem3_gpu_name() { return "CUDA"; }
bool wgse_mem3_gpu_hardware_supported() { return false; }
void wgse_mem3_gpu_inject_interruptions(size_t) {}
void wgse_mem3_gpu_inject_hang(const char *) {}

bool wgse_mem3_bsw_available(std::string &reason) {
    reason = no_gpu;
    return false;
}
WgseMem3MetalLimits wgse_mem3_metal_limits() { return {}; }
bool wgse_mem3_bsw_start(std::string &reason) { return wgse_mem3_bsw_available(reason); }
int wgse_mem3_bsw_finish() { return 0; }
bool wgse_mem3_bsw_enabled() { return false; }
bool wgse_mem3_bsw_busy() { return false; }
void wgse_mem3_bsw_set_backlog(int64_t) {}
int wgse_mem3_compute_threads(int threads) { return threads; }
void wgse_mem3_bsw_set_threads(int) {}
void wgse_mem3_bsw_inject_failure() {}
void wgse_mem3_bsw_decline() {}
bool wgse_mem3_bsw_score(WgseBswJob *, int32_t, const uint8_t *, const uint8_t *,
    const WgseBswScoring &) {
    return false;
}
