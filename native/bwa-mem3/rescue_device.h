// The GPU that runs mate rescue for rescue_host.cpp: CUDA (cuda_rescue.cu) or none
// (rescue_device_none.cpp). It runs wgse-bwa's mate-rescue kernel (native/bwa/rescue.cuh),
// BWA's ksw_align2 with KSW_XBYTE, both passes.
#pragma once

#include "../bwa/rescue_types.h"

#include <cstddef>
#include <cstdint>
#include <string>

// Whether this build and machine have a GPU for mate rescue, or the reason not.
bool wgse_mem3_rescue_device_available(std::string &reason);
// Aligns jobs[0, count) (offsets into `bases` and into a row scratch of `rows` bytes),
// writing results[0, count); a result with status rescue_cpu is left to the CPU. Returns
// false, with the reason, if the GPU failed, and then is not used again.
bool wgse_mem3_rescue_device_align(const RescueParameters &p, const uint8_t *bases,
    size_t base_count, const RescueJob *jobs, size_t count, size_t rows, RescueResult *results,
    double &seconds, std::string &reason);
