// The CUDA seeder: wgse-bwa-mem2's seeding kernel (seeding.cuh) for seeding_host.cpp. Unlike
// Metal on Apple Silicon, the occurrence table is uploaded to the GPU once (about 6 GiB for
// hs38), and each batch's reads and SMEMs cross PCIe through pinned buffers. With --gpu-sa on,
// the sampled suffix array follows it (about 4 GiB more), and worker threads look up their
// rows' suffix-array values on it, each batch on a stream of its own.
#include "cuda_common.h"
#include "gpu_runtime.h"
#include "seeding.cuh"
#include "seeding_device.h"

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <vector>

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

// FMI_search's sampled suffix array and what its LF walk reads besides the occurrence table.
struct SaTable {
    const int8_t *ms;
    const uint32_t *ls;
    int64_t count[5];
    uint32_t shift, drop_sentinel_offset;
};

// FMI_search::get_sa_entry_compressed (call_one_step's walk, as get_sa_entries_prefetch runs
// it), one row per thread: LF steps from the row to a sampled one, whose value plus the steps
// is the row's. The sentinel row, where no base is set, ends the walk at the steps taken (0
// under --compat=bwa-mem2).
__global__ void resolve_rows(const sk::Checkpoint *__restrict__ index, SaTable t,
    const int64_t *__restrict__ rows, int64_t *__restrict__ values, uint32_t count) {
    const uint32_t i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= count) return;
    const int64_t sampled = (int64_t(1) << t.shift) - 1;
    int64_t row = rows[i], steps = 0;
    while (row & sampled) {
        const sk::Checkpoint &entry = index[row >> 6];
        const uint32_t y = uint32_t(row & 63);
        unsigned planes = 0;
#pragma unroll
        for (int a = 0; a < 4; ++a) planes |= unsigned((entry.one_hot[a] >> (63 - y)) & 1) << a;
        if (planes == 0) {
            values[i] = t.drop_sentinel_offset ? 0 : steps;
            return;
        }
        const int b = __ffs(int(planes)) - 1;
        const uint64_t before = y ? ~0ull << (64 - y) : 0ull;
        row = t.count[b] + entry.count[b] + __popcll(entry.one_hot[b] & before);
        ++steps;
    }
    const int64_t sample = row >> t.shift;
    // ms is the value's signed top byte.
    values[i] = int64_t(uint64_t(int64_t(t.ms[sample])) << 32) + int64_t(t.ls[sample]) + steps;
}

// Rows per lookup batch; a worker's larger call runs in several.
constexpr size_t sa_batch_rows = size_t(1) << 16;

// A worker's lookup buffers and stream.
class SaContext {
public:
    explicit SaContext(int ordinal) : ordinal_(ordinal), watchdog_(wgse_mem3_cuda_watchdog_floor()) {
        check(WGSE_GPU(SetDevice)(ordinal_), "device selection");
        check(WGSE_GPU(StreamCreateWithFlags)(&stream_, WGSE_GPU(StreamNonBlocking)), "stream");
        check(WGSE_GPU(EventCreateWithFlags)(&done_, WGSE_GPU(EventDisableTiming)), "event");
        rows_.reserve(sa_batch_rows);
        values_.reserve(sa_batch_rows);
        host_rows_.reserve(sa_batch_rows);
        host_values_.reserve(sa_batch_rows);
    }
    ~SaContext() {
        if (gpu_hung()) return;
        if (done_) (void)WGSE_GPU(EventDestroy)(done_);
        if (stream_) (void)WGSE_GPU(StreamDestroy)(stream_);
    }
    SaContext(const SaContext &) = delete;
    SaContext &operator=(const SaContext &) = delete;

    void resolve(const sk::Checkpoint *index, const SaTable &table, const int64_t *rows,
        const int64_t *slots, size_t count, int64_t *coordinates) {
        check(WGSE_GPU(SetDevice)(ordinal_), "device selection");
        for (size_t first = 0; first < count; first += sa_batch_rows) {
            const size_t n = std::min(sa_batch_rows, count - first);
            std::copy_n(rows + first, n, host_rows_.data());
            check(WGSE_GPU(MemcpyAsync)(rows_.data(), host_rows_.data(), n * sizeof(int64_t),
                WGSE_GPU(MemcpyHostToDevice), stream_), "suffix-array upload");
            if (wgse_mem3_cuda_take_hang("sa"))
                spin<<<1, 1, 0, stream_>>>(uint64_t(watchdog_.limit(Watchdog::locate) * 2e9));
            resolve_rows<<<blocks(n), block_size, 0, stream_>>>(index, table, rows_.data(),
                values_.data(), uint32_t(n));
            check(WGSE_GPU(GetLastError)(), "suffix-array launch");
            check(WGSE_GPU(MemcpyAsync)(host_values_.data(), values_.data(), n * sizeof(int64_t),
                WGSE_GPU(MemcpyDeviceToHost), stream_), "suffix-array download");
            check(WGSE_GPU(EventRecord)(done_, stream_), "suffix-array lookup");
            const double allowed = watchdog_.limit(Watchdog::locate);
            try {
                watchdog_.wait(done_, Watchdog::locate, "suffix-array lookup");
            } catch (const std::exception &) {
                if (!gpu_hung()) throw;
                const std::string reason = "GPU watchdog: a CUDA command (sa) did not finish "
                    "within " + std::to_string(int(allowed)) + " s";
                wgse_mem3_cuda_set_hang(reason);
                throw std::runtime_error(reason);
            }
            const int64_t *values = host_values_.data();
            if (slots)
                for (size_t k = 0; k < n; ++k) coordinates[slots[first + k]] = values[k];
            else
                std::copy_n(values, n, coordinates + first);
        }
    }

private:
    int ordinal_;
    Watchdog watchdog_;
    wgse_gpu_stream stream_ = nullptr;
    wgse_gpu_event done_ = nullptr;
    DeviceBuffer<int64_t> rows_, values_;
    PinnedBuffer<int64_t> host_rows_, host_values_;
};

// Copies `bytes` from pageable host memory to the GPU through two pinned 64 MiB buffers, the
// CPU filling one while the other crosses PCIe: several times faster than a pageable copy.
void upload(void *device, const void *host, size_t bytes) {
    constexpr size_t chunk = size_t(64) << 20;
    PinnedBuffer<uint8_t> staging(2 * chunk);
    wgse_gpu_stream stream = nullptr;
    wgse_gpu_event sent[2] = {};
    check(WGSE_GPU(StreamCreateWithFlags)(&stream, WGSE_GPU(StreamNonBlocking)), "stream");
    try {
        for (auto &event : sent)
            check(WGSE_GPU(EventCreateWithFlags)(&event, WGSE_GPU(EventDisableTiming)), "event");
        for (size_t offset = 0, k = 0; offset < bytes; offset += chunk, k ^= 1) {
            const size_t n = std::min(chunk, bytes - offset);
            uint8_t *buffer = staging.data() + k * chunk;
            if (offset >= 2 * chunk) check(WGSE_GPU(EventSynchronize)(sent[k]), "suffix-array upload");
            std::copy_n(static_cast<const uint8_t *>(host) + offset, n, buffer);
            check(WGSE_GPU(MemcpyAsync)(static_cast<uint8_t *>(device) + offset, buffer, n,
                WGSE_GPU(MemcpyHostToDevice), stream), "suffix-array upload");
            check(WGSE_GPU(EventRecord)(sent[k], stream), "suffix-array upload");
        }
        check(WGSE_GPU(StreamSynchronize)(stream), "suffix-array upload");
    } catch (...) {
        for (auto event : sent) if (event) (void)WGSE_GPU(EventDestroy)(event);
        (void)WGSE_GPU(StreamDestroy)(stream);
        throw;
    }
    for (auto event : sent) (void)WGSE_GPU(EventDestroy)(event);
    (void)WGSE_GPU(StreamDestroy)(stream);
}

// As many contexts as mate rescue keeps: a worker waiting for one costs more than its 2 MiB.
size_t sa_context_count() { return std::clamp<unsigned>(std::thread::hardware_concurrency(), 4, 16); }

class CudaSeedingDevice final : public SeedingDevice {
public:
    CudaSeedingDevice(const void *checkpoints, size_t bytes, const SeedingLimits &limits)
        : watchdog_(wgse_mem3_cuda_watchdog_floor()) {
        std::string reason;
        ordinal_ = wgse_mem3_cuda_device(reason);
        if (ordinal_ < 0) throw std::runtime_error(reason);
        wgse_mem3_cuda_prepare();
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

    bool enable_sa(const int8_t *ms, const uint32_t *ls, size_t entries, int shift,
        const int64_t *count, std::string &reason) override {
        try {
            check(WGSE_GPU(SetDevice)(ordinal_), "device selection");
            // The table and every context's buffers, with 1 GiB spare, as for the occurrence
            // table: the GPU may be shared.
            size_t free = 0, total = 0;
            check(WGSE_GPU(MemGetInfo)(&free, &total), "memory query");
            const size_t contexts = sa_context_count();
            const size_t needed = entries * (sizeof(int8_t) + sizeof(uint32_t))
                + contexts * sa_batch_rows * 2 * sizeof(int64_t) + (size_t(1) << 30);
            if (needed > free) {
                reason = "the sampled suffix array needs " + std::to_string(needed >> 20)
                    + " MiB of GPU memory, " + std::to_string(free >> 20) + " MiB free";
                return false;
            }
            sa_ms_.reserve(entries);
            sa_ls_.reserve(entries);
            upload(sa_ms_.data(), ms, entries * sizeof(int8_t));
            upload(sa_ls_.data(), ls, entries * sizeof(uint32_t));
            for (size_t i = 0; i < contexts; ++i)
                sa_contexts_.push_back(std::make_unique<SaContext>(ordinal_));
            sa_free_.reserve(contexts);
            for (const auto &context : sa_contexts_) sa_free_.push_back(context.get());
        } catch (const std::exception &error) {
            reason = error.what();
            sa_free_.clear();
            sa_contexts_.clear();
            sa_ms_.release();
            sa_ls_.release();
            return false;
        }
        sa_table_.ms = sa_ms_.data();
        sa_table_.ls = sa_ls_.data();
        sa_table_.shift = uint32_t(shift);
        std::copy_n(count, 5, sa_table_.count);
        return true;
    }

    double resolve_sa(const int64_t *rows, const int64_t *slots, size_t count,
        int64_t *coordinates, bool drop_sentinel_offset) override {
        if (gpu_hung()) throw std::runtime_error(wgse_mem3_cuda_hang());
        const auto start = std::chrono::steady_clock::now();
        SaContext *context;
        {
            std::unique_lock<std::mutex> lock(sa_mutex_);
            sa_changed_.wait(lock, [this] { return !sa_free_.empty(); });
            context = sa_free_.back();
            sa_free_.pop_back();
        }
        SaTable table = sa_table_;
        table.drop_sentinel_offset = drop_sentinel_offset;
        try {
            context->resolve(index_.data(), table, rows, slots, count, coordinates);
        } catch (...) {
            give(context);
            throw;
        }
        give(context);
        return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    }

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
    DeviceBuffer<int8_t> sa_ms_;
    DeviceBuffer<uint32_t> sa_ls_;
    SaTable sa_table_{};
    std::vector<std::unique_ptr<SaContext>> sa_contexts_;
    std::vector<SaContext *> sa_free_;
    std::mutex sa_mutex_;
    std::condition_variable sa_changed_;

    void give(SaContext *context) {
        {
            std::lock_guard<std::mutex> lock(sa_mutex_);
            sa_free_.push_back(context);
        }
        sa_changed_.notify_one();
    }
};

} // namespace

std::unique_ptr<SeedingDevice> make_seeding_device(const void *checkpoints, size_t bytes,
    const SeedingLimits &limits) {
    return std::make_unique<CudaSeedingDevice>(checkpoints, bytes, limits);
}

// The GPU reads its own copy of the index.
bool seeding_device_unsettled() { return false; }
