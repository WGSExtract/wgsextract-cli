#pragma once

#include "runtime.h"
#include "extend_types.h"
#include "gpu_device.h"

#include "bwamem.h"

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>

namespace wgse::extend {
using wgse::gpu::extend_class_caps;
using wgse::gpu::order_side;
constexpr size_t classes = wgse::gpu::extend_classes;
constexpr size_t threadgroup_size = 64;

// Both sides' pipelines for every class, or false if any fails to build.
inline bool pipelines(wgse::metal::Runtime &runtime, const char *source,
    __strong id<MTLComputePipelineState> (&out)[2][classes]) {
    id<MTLLibrary> library = runtime.library(source);
    if (!library) return false;
    for (int side = 0; side < 2; ++side)
        for (size_t k = 0; k < classes; ++k) {
            const std::string name = std::string(side ? "extend_right_" : "extend_left_")
                + std::to_string(extend_class_caps[k]);
            out[side][k] = runtime.pipeline(library, name.c_str());
            if (!out[side][k] || threadgroup_size > out[side][k].maxTotalThreadsPerThreadgroup)
                return false;
        }
    return true;
}

// Runs batches of seed extensions on their own command queue, so they can
// overlap the seeding batches and each other.
class GpuExtend {
public:
    wgse::metal::Runtime &runtime;
    id<MTLComputePipelineState> kernels[2][classes];

    GpuExtend(wgse::metal::Runtime &metal, id<MTLComputePipelineState> const (&built)[2][classes])
        : runtime(metal) {
        if (!runtime.queue()) throw std::runtime_error("extension kernel unavailable");
        for (int side = 0; side < 2; ++side)
            for (size_t k = 0; k < classes; ++k) {
                kernels[side][k] = built[side][k];
                if (!kernels[side][k]) throw std::runtime_error("extension kernel unavailable");
            }
    }

    // Shared buffers for up to `count` jobs (their results and two orders) and `bytes` bases.
    void allocate(size_t bytes, size_t count, __strong id<MTLBuffer> &bases,
        __strong id<MTLBuffer> &jobs, __strong id<MTLBuffer> &results,
        __strong id<MTLBuffer> &order) {
        bases = runtime.allocate(bytes);
        jobs = runtime.allocate(count * sizeof(ExtendJob));
        results = runtime.allocate(count * sizeof(ExtendResult));
        order = runtime.allocate(2 * count * sizeof(uint32_t));
        if (!bases || !jobs || !results || !order)
            throw std::runtime_error("GPU extension allocation failed");
    }

    // Commits the left then the right extensions of jobs[0, count) without
    // waiting; `done` runs on completion with the command buffer, whose
    // results[i] are then mem_chain2aln's ksw_extend2 results for jobs[i].
    // `capacity` is the jobs the buffers were allocated for.
    void submit(const ExtendParameters &p, id<MTLBuffer> bases, id<MTLBuffer> jobs,
        id<MTLBuffer> results, id<MTLBuffer> order, size_t count, size_t capacity,
        void (^done)(id<MTLCommandBuffer>)) {
        @autoreleasepool {
            auto *indices = static_cast<uint32_t *>(order.contents);
            const auto *job = static_cast<const ExtendJob *>(jobs.contents);
            const size_t sides[2] = {order_side(job, count, false, indices),
                order_side(job, count, true, indices + capacity)};
            id<MTLCommandBuffer> command = [runtime.queue() commandBuffer];
            if (!command) throw std::runtime_error("Metal command allocation failed");
            for (int side = 0; side < 2; ++side) {
                if (sides[side] == 0) continue;
                // The jobs of each class are a contiguous run of the descending
                // order; the classes' dispatches are independent.
                id<MTLComputeCommandEncoder> encoder =
                    [command computeCommandEncoderWithDispatchType:MTLDispatchTypeConcurrent];
                if (!encoder) throw std::runtime_error("Metal command allocation failed");
                command.label = encoder.label = side ? @"extend_right" : @"extend_left";
                const uint32_t *slots = indices + side * capacity;
                auto length = [&](size_t slot) {
                    const ExtendJob &j = job[slots[slot]];
                    return side ? j.right_qlen : j.left_qlen;
                };
                size_t low = 0;
                for (size_t k = classes; k-- > 0 && low < sides[side];) {
                    const uint32_t below = k ? extend_class_caps[k - 1] : 0;
                    size_t high = low;
                    while (high < sides[side] && length(high) > below) ++high;
                    if (high == low) continue;
                    ExtendParameters current = p;
                    current.count = static_cast<uint32_t>(high - low);
                    [encoder setComputePipelineState:kernels[side][k]];
                    [encoder setBuffer:bases offset:0 atIndex:0];
                    [encoder setBuffer:jobs offset:0 atIndex:1];
                    [encoder setBuffer:results offset:0 atIndex:2];
                    [encoder setBytes:&current length:sizeof(current) atIndex:3];
                    [encoder setBuffer:order offset:(side * capacity + low) * sizeof(uint32_t)
                               atIndex:4];
                    const size_t per_group = 32 / extend_lanes;
                    const size_t threads = (current.count + per_group - 1) / per_group * 32;
                    [encoder dispatchThreads:MTLSizeMake(threads, 1, 1)
                        threadsPerThreadgroup:MTLSizeMake(threadgroup_size, 1, 1)];
                    low = high;
                }
                [encoder endEncoding];
            }
            wgse::metal::Runtime::commit(command, done);
        }
    }

    // A completed command buffer's GPU time, or a failure.
    static double finished(id<MTLCommandBuffer> command) {
        if (command.status != MTLCommandBufferStatusCompleted)
            throw std::runtime_error("GPU extension: " + wgse::metal::Runtime::describe(command));
        return std::max(0.0, command.GPUEndTime - command.GPUStartTime);
    }
};
} // namespace wgse::extend
