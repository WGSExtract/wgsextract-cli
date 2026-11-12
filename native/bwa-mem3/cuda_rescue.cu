// The CUDA mate rescue for rescue_host.cpp: wgse-bwa's ksw_align2 kernel (native/bwa/rescue.cuh).
// Each worker thread has its own stream and buffers, so their batches overlap on the GPU.
#include "cuda_common.h"
#include "gpu_runtime.h"
#include "../bwa/rescue.cuh"
#include "rescue_device.h"

#include <algorithm>
#include <chrono>
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

// One worker thread's stream and buffers.
class Context {
public:
    explicit Context(int ordinal) : ordinal_(ordinal), watchdog_(wgse_mem3_cuda_watchdog_floor()) {
        check(WGSE_GPU(SetDevice)(ordinal_), "device selection");
        check(WGSE_GPU(StreamCreateWithFlags)(&stream_, WGSE_GPU(StreamNonBlocking)), "stream");
        check(WGSE_GPU(EventCreateWithFlags)(&done_, WGSE_GPU(EventDisableTiming)), "event");
    }

    void align(const RescueParameters &parameters, const uint8_t *bases, size_t base_count,
        const RescueJob *jobs, size_t count, size_t rows, RescueResult *results) {
        check(WGSE_GPU(SetDevice)(ordinal_), "device selection");
        // Jobs grouped by forward segment count, as the kernel is instantiated per count.
        size_t start[rk::segments + 2] = {};
        auto segments = [](const RescueJob &job) {
            return std::clamp<size_t>((job.qlen + rk::lanes - 1) / rk::lanes, 1, rk::segments);
        };
        for (size_t i = 0; i < count; ++i) ++start[segments(jobs[i]) + 1];
        for (size_t s = 1; s <= rk::segments + 1; ++s) start[s] += start[s - 1];
        host_order_.reserve(count);
        size_t next[rk::segments + 1];
        std::copy_n(start, rk::segments + 1, next);
        for (size_t i = 0; i < count; ++i) host_order_.data()[next[segments(jobs[i])]++] = uint32_t(i);
        host_bases_.reserve(base_count);
        host_jobs_.reserve(count);
        host_results_.reserve(count);
        bases_.reserve(base_count);
        jobs_.reserve(count);
        order_.reserve(count);
        results_.reserve(count);
        rows_.reserve(rows);
        std::copy_n(bases, base_count, host_bases_.data());
        std::copy_n(jobs, count, host_jobs_.data());
        check(WGSE_GPU(MemcpyAsync)(bases_.data(), host_bases_.data(), base_count,
            WGSE_GPU(MemcpyHostToDevice), stream_), "rescue upload");
        check(WGSE_GPU(MemcpyAsync)(jobs_.data(), host_jobs_.data(), count * sizeof(RescueJob),
            WGSE_GPU(MemcpyHostToDevice), stream_), "rescue upload");
        check(WGSE_GPU(MemcpyAsync)(order_.data(), host_order_.data(), count * sizeof(uint32_t),
            WGSE_GPU(MemcpyHostToDevice), stream_), "rescue upload");
        std::string &hang_label = wgse_mem3_cuda_hang_label();
        {
            static std::mutex hang_mutex;
            std::lock_guard<std::mutex> lock(hang_mutex);
            if (!hang_label.empty() && std::string("rescue").rfind(hang_label, 0) == 0) {
                hang_label.clear();
                spin<<<1, 1, 0, stream_>>>(uint64_t(watchdog_.limit(Watchdog::rescue) * 2e9));
            }
        }
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
        std::copy_n(host_results_.data(), count, results);
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
};

// Contexts live until the process exits: freeing GPU memory then could wait on a hung kernel,
// and the CUDA runtime may already be gone when thread-local destructors run.
Context &context(int ordinal) {
    thread_local Context *value = nullptr;
    if (!value) value = new Context(ordinal);
    return *value;
}

} // namespace

bool wgse_mem3_rescue_device_available(std::string &reason) {
    return wgse_mem3_cuda_device(reason) >= 0;
}

bool wgse_mem3_rescue_device_align(const RescueParameters &p, const uint8_t *bases,
    size_t base_count, const RescueJob *jobs, size_t count, size_t rows, RescueResult *results,
    double &seconds, std::string &reason) {
    const auto start = std::chrono::steady_clock::now();
    if (gpu_hung()) {
        reason = wgse_mem3_cuda_hang();
        return false;
    }
    const int ordinal = wgse_mem3_cuda_device(reason);
    if (ordinal < 0) return false;
    try {
        context(ordinal).align(p, bases, base_count, jobs, count, rows, results);
    } catch (const std::exception &error) {
        reason = error.what();
        return false;
    }
    seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    return true;
}
