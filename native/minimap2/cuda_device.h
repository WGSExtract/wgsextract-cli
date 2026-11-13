// The CUDA GPU wgse-minimap2's offloads use (ksw_cuda.cu): the usable one with the most
// free memory.
#pragma once

#include <cstddef>
#include <string>

namespace wgse::cuda {

// Left free: the GPU may be shared.
constexpr size_t spare_bytes = size_t(1) << 30;

// The device ordinal and its free memory, or -1 with the reason.
int choose_device(size_t &free_bytes, std::string &reason);

} // namespace wgse::cuda
