// The CUDA seeder: wgse-bwa-mem2's seeding kernel (seeding.cuh) for seeding_host.cpp. Unlike
// Metal on Apple Silicon, the occurrence table is uploaded to the GPU once (about 6 GiB for
// hs38), and each batch's reads and SMEMs cross PCIe through pinned buffers.
#include "cuda_common.h"
#include "gpu_runtime.h"
#include "seeding.cuh"
#include "seeding_device.h"

#include <chrono>
#include <stdexcept>

using namespace wgse::gpu;
namespace sk = wgse::kernels::mem2_seeding;

namespace {

constexpr unsigned block_size = 64;

// Test hook: occupies the stream for about `nanoseconds`, as a hung kernel would.
__global__ void spin(uint64_t nanoseconds) {
    const uint64_t start = clock64();
    // clock64 counts SM cycles; 2 per nanosecond overestimates no current GPU.
    while (uint64_t(clock64() - start) < nanoseconds * 2) __nanosleep(1000);
}

unsigned blocks(size_t threads) { return unsigned((threads + block_size - 1) / block_size); }

class CudaSeedingDevice final : public SeedingDevice {
public:
    CudaSeedingDevice(const void *checkpoints, size_t bytes, const SeedingLimits &limits)
        : watchdog_(wgse_mem3_cuda_watchdog_floor()) {
        std::string reason;
        ordinal_ = wgse_mem3_cuda_device(reason);
        if (ordinal_ < 0) throw std::runtime_error(reason);
        check(WGSE_GPU(SetDevice)(ordinal_), "device selection");
        // The table, the prefix table and a batch's buffers, with 1 GiB spare: the GPU may be
        // shared.
        size_t free = 0, total = 0;
        check(WGSE_GPU(MemGetInfo)(&free, &total), "memory query");
        const size_t batch = limits.reads * (limits.read_length * (1 + sizeof(sk::Interval))
            + sizeof(SeedRead) + limits.capacity * sizeof(SeedSmem) + sizeof(SeedCount));
        const size_t needed = bytes + sk::prefix_entries * sizeof(sk::Prefix) + batch
            + (size_t(1) << 30);
        if (needed > free)
            throw std::runtime_error("the FM index needs " + std::to_string(needed >> 20)
                + " MiB of GPU memory, " + std::to_string(free >> 20) + " MiB free");
        check(WGSE_GPU(StreamCreateWithFlags)(&stream_, WGSE_GPU(StreamNonBlocking)), "stream");
        check(WGSE_GPU(EventCreateWithFlags)(&done_, WGSE_GPU(EventDisableTiming)), "event");
        index_.reserve(bytes / sizeof(sk::Checkpoint));
        check(WGSE_GPU(Memcpy)(index_.data(), checkpoints, bytes, WGSE_GPU(MemcpyHostToDevice)),
            "FM index upload");
        prefixes_.reserve(sk::prefix_entries);
        // Every buffer at its largest now: an allocation can wait for the GPU's running
        // kernels, so none may happen once other stages use the GPU.
        bases_.reserve(limits.reads * limits.read_length);
        host_bases_.reserve(limits.reads * limits.read_length);
        reads_.reserve(limits.reads);
        host_reads_.reserve(limits.reads);
        scratch_.reserve(limits.reads * limits.read_length);
        smems_.reserve(limits.reads * limits.capacity);
        host_smems_.reserve(limits.reads * limits.capacity);
        counts_.reserve(limits.reads);
        host_counts_.reserve(limits.reads);
    }
    ~CudaSeedingDevice() override {
        if (gpu_hung()) return;
        if (done_) (void)WGSE_GPU(EventDestroy)(done_);
        if (stream_) (void)WGSE_GPU(StreamDestroy)(stream_);
    }

    uint8_t *bases(size_t count) override {
        if (count > host_bases_.size()) throw std::runtime_error("a seeding batch outgrew its GPU buffers");
        return host_bases_.data();
    }
    SeedRead *reads(size_t count) override {
        if (count > host_reads_.size()) throw std::runtime_error("a seeding batch outgrew its GPU buffers");
        return host_reads_.data();
    }

    double collect(const SeedParameters &p, size_t base_count) override {
        if (gpu_hung()) throw std::runtime_error(wgse_mem3_cuda_hang());
        const auto start = std::chrono::steady_clock::now();
        check(WGSE_GPU(SetDevice)(ordinal_), "device selection");
        const size_t count = p.reads;
        if (base_count > bases_.size() || count > reads_.size()
            || count * p.scratch > scratch_.size() || count * p.capacity > smems_.size())
            throw std::runtime_error("a seeding batch outgrew its GPU buffers");
        const auto *index = index_.data();
        if (!have_prefixes_) {
            // The table depends only on the index (p.count, p.sentinel).
            sk::seed_prefixes<<<blocks(sk::prefix_entries), block_size, 0, stream_>>>(index, p,
                prefixes_.data());
            have_prefixes_ = true;
        }
        check(WGSE_GPU(MemcpyAsync)(bases_.data(), host_bases_.data(), base_count,
            WGSE_GPU(MemcpyHostToDevice), stream_), "seeding upload");
        check(WGSE_GPU(MemcpyAsync)(reads_.data(), host_reads_.data(), count * sizeof(SeedRead),
            WGSE_GPU(MemcpyHostToDevice), stream_), "seeding upload");
        if (wgse_mem3_cuda_take_hang("collect_smems")) {
            spin<<<1, 1, 0, stream_>>>(uint64_t(watchdog_.limit(Watchdog::seeding) * 2e9));
        }
        sk::collect_smems<<<blocks(count), block_size, 0, stream_>>>(index, p, bases_.data(),
            reads_.data(), scratch_.data(), smems_.data(), counts_.data(), prefixes_.data());
        check(WGSE_GPU(GetLastError)(), "seeding launch");
        check(WGSE_GPU(MemcpyAsync)(host_counts_.data(), counts_.data(), count * sizeof(SeedCount),
            WGSE_GPU(MemcpyDeviceToHost), stream_), "seeding download");
        check(WGSE_GPU(MemcpyAsync)(host_smems_.data(), smems_.data(),
            count * p.capacity * sizeof(SeedSmem), WGSE_GPU(MemcpyDeviceToHost), stream_),
            "seeding download");
        check(WGSE_GPU(EventRecord)(done_, stream_), "seeding");
        const double allowed = watchdog_.limit(Watchdog::seeding);
        try {
            watchdog_.wait(done_, Watchdog::seeding, "seeding");
        } catch (const std::exception &) {
            if (!gpu_hung()) throw;
            const std::string reason = "GPU watchdog: a CUDA command (collect_smems) did not finish "
                "within " + std::to_string(int(allowed)) + " s";
            wgse_mem3_cuda_set_hang(reason);
            throw std::runtime_error(reason);
        }
        return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    }

    const SeedCount *counts() const override { return host_counts_.data(); }
    const SeedSmem *smems() const override { return host_smems_.data(); }
    std::string hang() const override { return wgse_mem3_cuda_hang(); }

private:
    int ordinal_ = -1;
    Watchdog watchdog_;
    wgse_gpu_stream stream_ = nullptr;
    wgse_gpu_event done_ = nullptr;
    DeviceBuffer<sk::Checkpoint> index_;
    DeviceBuffer<sk::Prefix> prefixes_;
    bool have_prefixes_ = false;
    DeviceBuffer<uint8_t> bases_;
    DeviceBuffer<SeedRead> reads_;
    DeviceBuffer<sk::Interval> scratch_;
    DeviceBuffer<SeedSmem> smems_;
    DeviceBuffer<SeedCount> counts_;
    PinnedBuffer<uint8_t> host_bases_;
    PinnedBuffer<SeedRead> host_reads_;
    PinnedBuffer<SeedSmem> host_smems_;
    PinnedBuffer<SeedCount> host_counts_;
};

} // namespace

std::unique_ptr<SeedingDevice> make_seeding_device(const void *checkpoints, size_t bytes,
    const SeedingLimits &limits) {
    return std::make_unique<CudaSeedingDevice>(checkpoints, bytes, limits);
}

// The GPU reads its own copy of the index.
bool seeding_device_unsettled() { return false; }
