// The Metal seeder: wgse-bwa-mem2's seeding.metal on the occurrence table in place (FMI_search
// allocates it page-aligned for this).
#include "seeding_device.h"

#include "runtime.h"
#include "seeding_source.h"

#include <algorithm>
#include <stdexcept>
#include <unistd.h>

namespace {

constexpr size_t group = 64;

class MetalSeedingDevice final : public SeedingDevice {
public:
    MetalSeedingDevice(const void *checkpoints, size_t bytes) {
        if (!runtime_.device()) throw std::runtime_error(runtime_.failure());
        id<MTLLibrary> library = runtime_.library(wgse_seeding_source);
        pipeline_ = runtime_.pipeline(library, "collect_smems");
        prefix_pipeline_ = runtime_.pipeline(library, "seed_prefixes");
        if (!pipeline_ || !prefix_pipeline_) throw std::runtime_error(runtime_.failure());
        const size_t page = size_t(getpagesize());
        if (!checkpoints || reinterpret_cast<uintptr_t>(checkpoints) % page)
            throw std::runtime_error("the FM index is not page-aligned for Metal");
        const size_t length = (bytes + page - 1) / page * page;
        if (length > runtime_.device().maxBufferLength)
            throw std::runtime_error("the FM index is larger than the GPU's largest buffer");
        index_ = [runtime_.device() newBufferWithBytesNoCopy:const_cast<void *>(checkpoints)
            length:length options:MTLResourceStorageModeShared deallocator:nil];
        parameters_ = runtime_.allocate(sizeof(SeedParameters));
        if (!index_ || !parameters_) throw std::runtime_error("cannot share the FM index with Metal");
    }

    uint8_t *bases(size_t count) override {
        reserve(bases_, count);
        return static_cast<uint8_t *>(bases_.contents);
    }
    SeedRead *reads(size_t count) override {
        reserve(reads_, count * sizeof(SeedRead));
        return static_cast<SeedRead *>(reads_.contents);
    }

    double collect(const SeedParameters &p, size_t) override {
        @autoreleasepool {
            const size_t count = p.reads;
            reserve(scratch_, count * p.scratch * 32);
            reserve(smems_, count * p.capacity * sizeof(SeedSmem));
            reserve(counts_, count * sizeof(SeedCount));
            memcpy(parameters_.contents, &p, sizeof(p));
            if (!prefixes_) {
                // The table depends only on the index (p.count, p.sentinel).
                const size_t entries = ((size_t(1) << (2 * (seed_prefix_depth + 1))) - 4) / 3;
                id<MTLBuffer> table = runtime_.allocate(entries * 3 * sizeof(int64_t));
                if (!table) throw std::runtime_error("Metal allocation failed");
                double seconds = 0;
                if (!runtime_.dispatch(prefix_pipeline_, {index_, parameters_, table}, entries, group,
                        seconds))
                    throw std::runtime_error(runtime_.failure());
                prefixes_ = table;
            }
            double elapsed = 0;
            // The kernel rewrites its outputs from unchanged inputs, so an interrupted dispatch
            // reruns.
            if (!runtime_.dispatch(pipeline_,
                    {index_, parameters_, bases_, reads_, scratch_, smems_, counts_, prefixes_}, count,
                    group, elapsed))
                throw std::runtime_error(runtime_.failure());
            return elapsed;
        }
    }

    const SeedCount *counts() const override {
        return static_cast<const SeedCount *>(counts_.contents);
    }
    const SeedSmem *smems() const override { return static_cast<const SeedSmem *>(smems_.contents); }
    std::string hang() const override { return wgse::metal::Runtime::hang(); }

private:
    wgse::metal::Runtime runtime_;
    id<MTLComputePipelineState> pipeline_ = nil, prefix_pipeline_ = nil;
    id<MTLBuffer> index_ = nil, parameters_ = nil, bases_ = nil, reads_ = nil, scratch_ = nil,
        smems_ = nil, counts_ = nil, prefixes_ = nil;

    void reserve(__strong id<MTLBuffer> &buffer, size_t bytes) {
        bytes = std::max<size_t>(bytes, 1);
        if (buffer && buffer.length >= bytes) return;
        buffer = nil;
        buffer = runtime_.allocate(bytes + bytes / 4);
        if (!buffer) throw std::runtime_error("Metal allocation failed");
    }
};

} // namespace

std::unique_ptr<SeedingDevice> make_seeding_device(const void *checkpoints, size_t bytes,
    const SeedingLimits &) {
    return std::make_unique<MetalSeedingDevice>(checkpoints, bytes);
}

bool seeding_device_unsettled() { return wgse::metal::Runtime::unsettled() > 0; }
