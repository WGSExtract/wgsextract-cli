// A build without GPU mate rescue (Metal has none yet, nor a Linux build without CUDA).
#include "rescue_device.h"

bool wgse_mem3_rescue_device_available(std::string &reason) {
    reason = "this build has no GPU mate rescue";
    return false;
}

bool wgse_mem3_rescue_device_align(const RescueParameters &, const uint8_t *, size_t,
    const RescueJob *, size_t, size_t, RescueResult *, double &, std::string &reason) {
    reason = "this build has no GPU mate rescue";
    return false;
}
