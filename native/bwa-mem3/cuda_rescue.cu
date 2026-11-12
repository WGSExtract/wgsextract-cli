// The CUDA mate rescue for rescue_host.cpp: wgse-bwa's ksw_align2 kernel (native/bwa/rescue.cuh).
// Each worker thread has its own stream and buffers, so their batches overlap on the GPU.
#include "cuda_common.h"
#include "gpu_runtime.h"
#include "../bwa/rescue.cuh"
#include "rescue_device.h"

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <vector>

using namespace wgse::gpu;
namespace rk = wgse::kernels::rescue;

namespace {

constexpr unsigned block_size = 128;

// Test hook: occupies the stream for about `nanoseconds`, as a hung kernel would.
__global__ void spin(uint64_t nanoseconds) {
    const uint64_t start = clock64();
    // clock64 counts SM cycles; 2 per nanosecond overestimates no current GPU.
    while (uint64_t(clock64() - start) < nanoseconds * 2) __nanosleep(1000);
}

template <int S>
void launch(const uint8_t *bases, const RescueJob *jobs, uint8_t *rows, RescueResult *results,
    const RescueParameters &p, const uint32_t *order, wgse_gpu_stream stream) {
    const size_t threads = size_t(p.count) * rk::lanes;
    rk::rescue_align<S><<<unsigned((threads + block_size - 1) / block_size), block_size, 0, stream>>>(
        bases, jobs, rows, results, p, order);
}

// The launch for jobs of `segments` forward segments (ksw_u8's striping).
template <int S = 1>
void launch_class(int segments, const uint8_t *bases, const RescueJob *jobs, uint8_t *rows,
    RescueResult *results, const RescueParameters &p, const uint32_t *order,
    wgse_gpu_stream stream) {
    if constexpr (S <= rk::segments) {
        if (segments == S) launch<S>(bases, jobs, rows, results, p, order, stream);
        else launch_class<S + 1>(segments, bases, jobs, rows, results, p, order, stream);
    }
}

// A batch's limits. Each context's buffers are allocated at this size before alignment
// starts: an allocation can wait for the GPU's running kernels, so none may happen while
// other stages use the GPU. Larger calls run in several batches; a window beyond them is
// left to the CPU.
constexpr size_t max_jobs = 16384, max_bases = size_t(16) << 20, max_rows = size_t(8) << 20;
// Contexts, each with its own stream; worker threads take a free one.
constexpr int context_count = 4;

class Context {
public:
    explicit Context(int ordinal) : ordinal_(ordinal), watchdog_(wgse_mem3_cuda_watchdog_floor()) {
        check(WGSE_GPU(SetDevice)(ordinal_), "device selection");
        check(WGSE_GPU(StreamCreateWithFlags)(&stream_, WGSE_GPU(StreamNonBlocking)), "stream");
        check(WGSE_GPU(EventCreateWithFlags)(&done_, WGSE_GPU(EventDisableTiming)), "event");
        host_bases_.reserve(max_bases);
        host_jobs_.reserve(max_jobs);
        host_order_.reserve(max_jobs);
        host_results_.reserve(max_jobs);
        bases_.reserve(max_bases);
        rows_.reserve(max_rows);
        jobs_.reserve(max_jobs);
        order_.reserve(max_jobs);
        results_.reserve(max_jobs);
    }

    // Aligns jobs[0, count), in batches that fit the buffers.
    void align(const RescueParameters &parameters, const uint8_t *bases, const RescueJob *jobs,
        size_t count, RescueResult *results) {
        check(WGSE_GPU(SetDevice)(ordinal_), "device selection");
        std::vector<size_t> batch;
        size_t base_count = 0, row_count = 0;
        for (size_t i = 0; i <= count; ++i) {
            const bool last = i == count;
            const size_t need_bases = last ? 0 : size_t(jobs[i].qlen) + jobs[i].tlen;
            const size_t need_rows = last ? 0 : size_t(jobs[i].tlen);
            if (!last && (need_bases > max_bases || need_rows > max_rows)) {
                results[i] = RescueResult{0, -1, -1, -1, -1, -1, -1, rescue_cpu};
                continue;
            }
            if (last || batch.size() == max_jobs || base_count + need_bases > max_bases
                || row_count + need_rows > max_rows) {
                if (!batch.empty()) run(parameters, bases, jobs, batch, base_count, row_count, results);
                batch.clear();
                base_count = row_count = 0;
                if (last) break;
            }
            // The batch's copy of the job's bases (query, then target) and its rows.
            RescueJob job = jobs[i];
            std::copy_n(bases + job.query, job.qlen, host_bases_.data() + base_count);
            std::copy_n(bases + job.target, job.tlen, host_bases_.data() + base_count + job.qlen);
            job.query = uint32_t(base_count);
            job.target = uint32_t(base_count + job.qlen);
            job.rows = uint32_t(row_count);
            host_jobs_.data()[batch.size()] = job;
            batch.push_back(i);
            base_count += need_bases;
            row_count += need_rows;
        }
    }

private:
    int ordinal_;
    Watchdog watchdog_;
    wgse_gpu_stream stream_ = nullptr;
    wgse_gpu_event done_ = nullptr;
    PinnedBuffer<uint8_t> host_bases_;
    PinnedBuffer<RescueJob> host_jobs_;
    PinnedBuffer<uint32_t> host_order_;
    PinnedBuffer<RescueResult> host_results_;
    DeviceBuffer<uint8_t> bases_, rows_;
    DeviceBuffer<RescueJob> jobs_;
    DeviceBuffer<uint32_t> order_;
    DeviceBuffer<RescueResult> results_;

    // One batch: the staged jobs host_jobs_[0, batch.size()), for jobs batch[k] of the call.
    void run(const RescueParameters &parameters, const uint8_t *, const RescueJob *,
        const std::vector<size_t> &batch, size_t base_count, size_t, RescueResult *results) {
        const size_t count = batch.size();
        const RescueJob *jobs = host_jobs_.data();
        // Jobs grouped by forward segment count, as the kernel is instantiated per count.
        size_t start[rk::segments + 2] = {};
        auto segments = [](const RescueJob &job) {
            return std::clamp<size_t>((job.qlen + rk::lanes - 1) / rk::lanes, 1, rk::segments);
        };
        for (size_t i = 0; i < count; ++i) ++start[segments(jobs[i]) + 1];
        for (size_t s = 1; s <= rk::segments + 1; ++s) start[s] += start[s - 1];
        size_t next[rk::segments + 1];
        std::copy_n(start, rk::segments + 1, next);
        for (size_t i = 0; i < count; ++i) host_order_.data()[next[segments(jobs[i])]++] = uint32_t(i);
        check(WGSE_GPU(MemcpyAsync)(bases_.data(), host_bases_.data(), base_count,
            WGSE_GPU(MemcpyHostToDevice), stream_), "rescue upload");
        check(WGSE_GPU(MemcpyAsync)(jobs_.data(), jobs, count * sizeof(RescueJob),
            WGSE_GPU(MemcpyHostToDevice), stream_), "rescue upload");
        check(WGSE_GPU(MemcpyAsync)(order_.data(), host_order_.data(), count * sizeof(uint32_t),
            WGSE_GPU(MemcpyHostToDevice), stream_), "rescue upload");
        if (wgse_mem3_cuda_take_hang("rescue"))
            spin<<<1, 1, 0, stream_>>>(uint64_t(watchdog_.limit(Watchdog::rescue) * 2e9));
        for (size_t s = 1; s <= rk::segments; ++s) {
            const size_t n = start[s + 1] - start[s];
            if (n == 0) continue;
            RescueParameters p = parameters;
            p.count = uint32_t(n);
            launch_class(int(s), bases_.data(), jobs_.data(), rows_.data(), results_.data(), p,
                order_.data() + start[s], stream_);
        }
        check(WGSE_GPU(GetLastError)(), "rescue launch");
        check(WGSE_GPU(MemcpyAsync)(host_results_.data(), results_.data(), count * sizeof(RescueResult),
            WGSE_GPU(MemcpyDeviceToHost), stream_), "rescue download");
        check(WGSE_GPU(EventRecord)(done_, stream_), "rescue");
        const double allowed = watchdog_.limit(Watchdog::rescue);
        try {
            watchdog_.wait(done_, Watchdog::rescue, "mate rescue");
        } catch (const std::exception &) {
            if (!gpu_hung()) throw;
            const std::string reason = "GPU watchdog: a CUDA command (rescue) did not finish "
                "within " + std::to_string(int(allowed)) + " s";
            wgse_mem3_cuda_set_hang(reason);
            throw std::runtime_error(reason);
        }
        for (size_t k = 0; k < count; ++k) results[batch[k]] = host_results_.data()[k];
    }
};

// The contexts, created before alignment starts and kept until the process exits: freeing GPU
// memory could wait on a hung kernel.
class Pool {
public:
    explicit Pool(int ordinal) {
        for (int i = 0; i < context_count; ++i) free_.push_back(new Context(ordinal));
    }
    Context *take() {
        std::unique_lock<std::mutex> lock(mutex_);
        changed_.wait(lock, [this] { return !free_.empty(); });
        Context *context = free_.back();
        free_.pop_back();
        return context;
    }
    void give(Context *context) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            free_.push_back(context);
        }
        changed_.notify_one();
    }

private:
    std::mutex mutex_;
    std::condition_variable changed_;
    std::vector<Context *> free_;
};

Pool *pool = nullptr;
std::string pool_failure;

Pool *make_pool(std::string &reason) {
    static std::once_flag once;
    std::call_once(once, [] {
        std::string why;
        const int ordinal = wgse_mem3_cuda_device(why);
        if (ordinal < 0) {
            pool_failure = why;
            return;
        }
        try {
            pool = new Pool(ordinal);
        } catch (const std::exception &error) {
            pool_failure = error.what();
        }
    });
    reason = pool_failure;
    return pool;
}

} // namespace

bool wgse_mem3_rescue_device_available(std::string &reason) { return make_pool(reason) != nullptr; }

bool wgse_mem3_rescue_device_align(const RescueParameters &p, const uint8_t *bases, size_t,
    const RescueJob *jobs, size_t count, size_t, RescueResult *results, double &seconds,
    std::string &reason) {
    const auto start = std::chrono::steady_clock::now();
    if (gpu_hung()) {
        reason = wgse_mem3_cuda_hang();
        return false;
    }
    Pool *contexts = make_pool(reason);
    if (!contexts) return false;
    Context *context = contexts->take();
    bool ok = true;
    try {
        context->align(p, bases, jobs, count, results);
    } catch (const std::exception &error) {
        reason = error.what();
        ok = false;
    }
    contexts->give(context);
    seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    return ok;
}
