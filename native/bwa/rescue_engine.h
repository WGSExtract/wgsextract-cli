#pragma once

#include "rescue_parameters.h"
#include "rescue_types.h"
#include "runtime.h"

#include "bwamem.h"

#include <algorithm>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

namespace wgse::rescue {
// The rescue kernel for forward-pass queries of `segments` 16-lane segments.
inline id<MTLComputePipelineState> pipeline(wgse::metal::Runtime &runtime, id<MTLLibrary> library,
    int segments) {
    MTLFunctionConstantValues *constants = [MTLFunctionConstantValues new];
    const int32_t value = segments;
    [constants setConstantValue:&value type:MTLDataTypeInt atIndex:0];
    return runtime.pipeline(library, "rescue_align", constants);
}

// Runs batches of mate-rescue alignments; buffers grow to the largest batch.
// Each query length class (16-lane segment count) has its own kernel pipeline,
// compiled when a batch first needs it.
class GpuRescue {
public:
    wgse::metal::Runtime &runtime;
    id<MTLLibrary> library;
    id<MTLComputePipelineState> pipelines[rescue_max_segments + 1] = {};
    id<MTLBuffer> bases = nil, jobs = nil, rows = nil, results = nil, order = nil;
    double kernel_seconds = 0;

    GpuRescue(wgse::metal::Runtime &metal, id<MTLLibrary> kernels)
        : runtime(metal), library(kernels) {
        if (!library || !runtime.queue()) throw std::runtime_error("rescue kernel unavailable");
    }

    // results[i] is ksw_align2's result for job i, unless its status is rescue_cpu.
    void run(const RescueParameters &p, const std::vector<uint8_t> &input,
        const std::vector<RescueJob> &work, size_t row_bytes, std::vector<RescueResult> &output,
        size_t group) {
        output.resize(work.size());
        if (work.empty()) return;
        @autoreleasepool {
            reserve(bases, input.size());
            reserve(jobs, work.size() * sizeof(RescueJob));
            reserve(rows, row_bytes);
            reserve(results, work.size() * sizeof(RescueResult));
            reserve(order, work.size() * sizeof(uint32_t));
            memcpy(bases.contents, input.data(), input.size());
            memcpy(jobs.contents, work.data(), work.size() * sizeof(RescueJob));
            // Jobs grouped by segment count; the host only queues queries of
            // 1..rescue_max_query bases.
            size_t start[rescue_max_segments + 2] = {};
            for (const RescueJob &job : work) ++start[segments(job) + 1];
            for (size_t s = 1; s <= rescue_max_segments + 1; ++s) start[s] += start[s - 1];
            auto *indices = static_cast<uint32_t *>(order.contents);
            size_t next[rescue_max_segments + 1];
            std::copy_n(start, rescue_max_segments + 1, next);
            for (size_t i = 0; i < work.size(); ++i)
                indices[next[segments(work[i])]++] = static_cast<uint32_t>(i);
            // Every dispatch rewrites the results and its rows scratch from the
            // unchanged inputs, so an interrupted command can run again.
            id<MTLCommandBuffer> command = nil;
            for (int attempt = 1;; ++attempt) {
                command = [runtime.queue() commandBuffer];
                id<MTLComputeCommandEncoder> encoder =
                    [command computeCommandEncoderWithDispatchType:MTLDispatchTypeConcurrent];
                if (!command || !encoder)
                    throw std::runtime_error("Metal command allocation failed");
                command.label = encoder.label = @"rescue_align";
                [encoder setBuffer:bases offset:0 atIndex:0];
                [encoder setBuffer:jobs offset:0 atIndex:1];
                [encoder setBuffer:rows offset:0 atIndex:2];
                [encoder setBuffer:results offset:0 atIndex:3];
                for (size_t s = 1; s <= rescue_max_segments; ++s) {
                    const size_t count = start[s + 1] - start[s];
                    if (count == 0) continue;
                    id<MTLComputePipelineState> kernel = specialised(s);
                    if (!kernel || group > kernel.maxTotalThreadsPerThreadgroup) {
                        [encoder endEncoding];
                        throw std::runtime_error(kernel ? "rescue kernel threadgroup too large"
                                                        : runtime.failure());
                    }
                    RescueParameters current = p;
                    current.count = static_cast<uint32_t>(count);
                    [encoder setComputePipelineState:kernel];
                    [encoder setBytes:&current length:sizeof(current) atIndex:4];
                    [encoder setBuffer:order offset:start[s] * sizeof(uint32_t) atIndex:5];
                    // Each job uses one half (16 lanes) of a 32-wide SIMD-group.
                    [encoder dispatchThreads:MTLSizeMake((count + 1) / 2 * 32, 1, 1)
                        threadsPerThreadgroup:MTLSizeMake(group, 1, 1)];
                }
                [encoder endEncoding];
                wgse::metal::Runtime::run(command);
                if (attempt < wgse::metal::Runtime::attempts
                    && wgse::metal::Runtime::interrupted(command, attempt)) continue;
                if (command.status == MTLCommandBufferStatusCompleted) break;
                throw std::runtime_error("GPU mate rescue: "
                    + wgse::metal::Runtime::describe(command));
            }
            kernel_seconds += std::max(0.0, command.GPUEndTime - command.GPUStartTime);
            memcpy(output.data(), results.contents, work.size() * sizeof(RescueResult));
        }
    }

private:
    static size_t segments(const RescueJob &job) {
        return std::clamp<size_t>((job.qlen + rescue_lanes - 1) / rescue_lanes, 1,
            rescue_max_segments);
    }

    id<MTLComputePipelineState> specialised(size_t segments) {
        if (!pipelines[segments])
            pipelines[segments] = pipeline(runtime, library, static_cast<int>(segments));
        return pipelines[segments];
    }

    void reserve(__strong id<MTLBuffer> &buffer, size_t bytes) {
        bytes = std::max<size_t>(bytes, 1);
        if (buffer && buffer.length >= bytes) return;
        buffer = nil;
        buffer = runtime.allocate(bytes + bytes / 4);
        if (!buffer) throw std::runtime_error("GPU rescue allocation failed");
    }
};
} // namespace wgse::rescue
