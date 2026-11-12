// The shared GPU kernel layer: one kernel source compiled by CUDA (nvcc) and HIP
// (hipcc). Kernels use these wrappers instead of warp intrinsics, and work in
// groups ("segments") of 16 or 32 lanes rather than assuming a warp size, so the
// same code runs on 32-lane NVIDIA warps and on 32- or 64-lane AMD wavefronts.
// Every lane of a warp must reach each wrapper call (uniform control flow).
#pragma once

#include <cstdint>

#if defined(__HIPCC__)
#include <hip/hip_runtime.h>
#define wgse_any(p) __any(p)
#define wgse_all(p) __all(p)
#define wgse_shfl(v, src, width) __shfl((v), (src), (width))
#define wgse_shfl_up(v, d, width) __shfl_up((v), (d), (width))
#define wgse_shfl_xor(v, d, width) __shfl_xor((v), (d), (width))
#else
#include <cuda_runtime.h>
#define WGSE_FULL_MASK 0xffffffffu
#define wgse_any(p) __any_sync(WGSE_FULL_MASK, (p))
#define wgse_all(p) __all_sync(WGSE_FULL_MASK, (p))
#define wgse_shfl(v, src, width) __shfl_sync(WGSE_FULL_MASK, (v), (src), (width))
#define wgse_shfl_up(v, d, width) __shfl_up_sync(WGSE_FULL_MASK, (v), (d), (width))
#define wgse_shfl_xor(v, d, width) __shfl_xor_sync(WGSE_FULL_MASK, (v), (d), (width))
#endif

#define WGSE_DEVICE __device__ __forceinline__

namespace wgse::kernels {
WGSE_DEVICE int popcount64(uint64_t x) {
    return __popcll(x);
}

// Count of trailing zero bits of a nonzero x.
WGSE_DEVICE int ctz32(uint32_t x) {
    return __ffs(int(x)) - 1;
}

// Saturating unsigned subtraction (Metal's subsat).
WGSE_DEVICE uint16_t subsat(uint16_t a, uint16_t b) {
    return a > b ? uint16_t(a - b) : uint16_t(0);
}
} // namespace wgse::kernels
