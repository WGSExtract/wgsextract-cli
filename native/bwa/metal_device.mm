// The Metal device for wgse-bwa's GPU scheduling (backend.cpp): runtime-compiled
// kernels on a unified-memory GPU, which uses the index in place.
#include "gpu_device.h"
#include "seeding_engine.h"

#include <stdexcept>
#include <unistd.h>

namespace wgse::gpu {
namespace {
using wgse::extend::GpuExtend;
using wgse::rescue::GpuRescue;
using wgse::seeding::GpuLocate;
using wgse::seeding::GpuSeeds;
using wgse::seeding::SeedKernel;

constexpr size_t threadgroup_size = 64;

struct MetalExtendBuffers : ExtendBuffers {
    id<MTLBuffer> bases_buffer = nil, jobs_buffer = nil, results_buffer = nil, order = nil;
};

class MetalDevice final : public Device {
public:
    explicit MetalDevice(const DeviceOptions &options) : options_(options) {}

    SeedKernel kernel;

    const char *name() const override { return "Metal"; }

    size_t seed_capacity() const override { return options_.seed_batch; }

    void attach(const bwt_t &bwt, const mem_opt_t &opt) override {
        @autoreleasepool {
            if (!seeds_) seeds_ = std::make_unique<GpuSeeds>(kernel, options_.seed_batch);
            if (shared_bwt_ != bwt.bwt) {
                const size_t page = static_cast<size_t>(getpagesize());
                if (reinterpret_cast<uintptr_t>(bwt.bwt) % page)
                    throw std::runtime_error(
                        "Metal needs a disk-loaded index, not a shared-memory one");
                const size_t bytes = (bwt.bwt_size * sizeof(uint32_t) + page - 1) / page * page;
                // The C index keeps ownership; no GPU command outlives mem_process_seqs.
                seeds_->index = [seeds_->runtime.device() newBufferWithBytesNoCopy:bwt.bwt
                    length:bytes options:MTLResourceStorageModeShared deallocator:nil];
                if (!seeds_->index) throw std::runtime_error("cannot share the BWT with Metal");
                shared_bwt_ = bwt.bwt;
            }
            seeds_->configure(bwt, opt, seed_scratch_capacity, seed_output_capacity);
        }
    }

    void seed(const Reads &input, std::vector<Intervals> &out, std::vector<long> &failed) override {
        seeds_->run(input, out, threadgroup_size, 0, nullptr, nullptr);
        failed = seeds_->failed;
    }

    bool attach_sa(const bwt_t &bwt) override {
        @autoreleasepool {
            locator_.reset();
            const size_t page = static_cast<size_t>(getpagesize());
            if (!GpuLocate::supported(bwt) || reinterpret_cast<uintptr_t>(bwt.sa) % page)
                return false;
            auto shared = std::make_unique<GpuLocate>(kernel, options_.locate_rows);
            const size_t bytes = (bwt.n_sa * sizeof(uint64_t) + page - 1) / page * page;
            shared->sa = [seeds_->runtime.device() newBufferWithBytesNoCopy:bwt.sa length:bytes
                options:MTLResourceStorageModeShared deallocator:nil];
            if (!shared->sa) return false;
            shared->configure(bwt);
            locator_ = std::move(shared);
            return true;
        }
    }

    void locate(const std::vector<uint64_t> &rows, std::vector<uint64_t> &positions) override {
        locator_->run(seeds_->index, rows, positions, threadgroup_size);
    }

    bool densify(unsigned shift, size_t slice, const uint32_t **low,
        const uint32_t **high) override {
        @autoreleasepool {
            if (!locator_->densify(seeds_->index, shift, slice, threadgroup_size)) return false;
            *low = static_cast<const uint32_t *>(locator_->low.contents);
            *high = static_cast<const uint32_t *>(locator_->high.contents);
            return true;
        }
    }

    void rescue(const RescueParameters &p, const std::vector<uint8_t> &bases,
        const std::vector<RescueJob> &jobs, size_t row_bytes,
        std::vector<RescueResult> &results) override {
        if (!rescuer_) rescuer_ = std::make_unique<GpuRescue>(kernel.runtime, kernel.rescue);
        rescuer_->run(p, bases, jobs, row_bytes, results, threadgroup_size);
    }

    double rescue_kernel_seconds() const override {
        return rescuer_ ? rescuer_->kernel_seconds : 0;
    }

    std::unique_ptr<ExtendBuffers> extend_buffers(size_t bases, size_t jobs) override {
        @autoreleasepool {
            if (!extender_) extender_ = std::make_unique<GpuExtend>(kernel.extend_runtime,
                kernel.extend);
            auto buffers = std::make_unique<MetalExtendBuffers>();
            extender_->allocate(bases, jobs, buffers->bases_buffer, buffers->jobs_buffer,
                buffers->results_buffer, buffers->order);
            buffers->bases = static_cast<uint8_t *>(buffers->bases_buffer.contents);
            buffers->jobs = static_cast<ExtendJob *>(buffers->jobs_buffer.contents);
            buffers->results = static_cast<ExtendResult *>(buffers->results_buffer.contents);
            buffers->base_capacity = bases;
            buffers->job_capacity = jobs;
            return buffers;
        }
    }

    void extend(const ExtendParameters &p, ExtendBuffers &base, size_t count, int attempt,
        ExtendDone done) override {
        auto &buffers = static_cast<MetalExtendBuffers &>(base);
        extender_->submit(p, buffers.bases_buffer, buffers.jobs_buffer, buffers.results_buffer,
            buffers.order, count, buffers.job_capacity, ^(id<MTLCommandBuffer> command) {
                if (attempt < attempts && wgse::metal::Runtime::interrupted(command, attempt)) {
                    done(Outcome::interrupted, 0, "");
                    return;
                }
                try {
                    const double kernel = GpuExtend::finished(command);
                    done(Outcome::done, kernel, "");
                } catch (const std::exception &error) {
                    done(Outcome::failed, 0, error.what());
                }
            });
    }

    size_t interruptions() const override { return wgse::metal::Runtime::interruptions(); }

private:
    DeviceOptions options_;
    std::unique_ptr<GpuSeeds> seeds_;
    std::unique_ptr<GpuLocate> locator_;
    std::unique_ptr<GpuRescue> rescuer_;
    std::unique_ptr<GpuExtend> extender_;
    const uint32_t *shared_bwt_ = nullptr;
};
} // namespace

std::unique_ptr<Device> make_metal_device(const DeviceOptions &options, std::string &reason) {
    @autoreleasepool {
        wgse::metal::Runtime::inject_interruptions(options.interrupt_every);
        auto device = std::make_unique<MetalDevice>(options);
        if (!device->kernel.available(reason)) return nullptr;
        return device;
    }
}

bool metal_hardware_supported() {
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        return device && device.hasUnifiedMemory;
    }
}
} // namespace wgse::gpu
