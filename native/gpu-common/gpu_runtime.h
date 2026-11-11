// Host side of the shared GPU kernel layer, for CUDA and HIP: error checks,
// device and pinned buffers, and the watchdog that bounds every wait for the
// GPU, so a hung kernel sends its work to the CPU instead of stalling the run.
#pragma once

#if defined(__HIPCC__)
#include <hip/hip_runtime.h>
#define WGSE_GPU(name) hip##name
using wgse_gpu_error = hipError_t;
using wgse_gpu_event = hipEvent_t;
using wgse_gpu_stream = hipStream_t;
#define WGSE_GPU_SUCCESS hipSuccess
#define WGSE_GPU_NOT_READY hipErrorNotReady
#define wgse_gpu_host_alloc(pointer, bytes) hipHostMalloc((pointer), (bytes), hipHostMallocDefault)
#define wgse_gpu_host_free hipHostFree
#else
#include <cuda_runtime.h>
#define WGSE_GPU(name) cuda##name
using wgse_gpu_error = cudaError_t;
using wgse_gpu_event = cudaEvent_t;
using wgse_gpu_stream = cudaStream_t;
#define WGSE_GPU_SUCCESS cudaSuccess
#define WGSE_GPU_NOT_READY cudaErrorNotReady
#define wgse_gpu_host_alloc(pointer, bytes) cudaMallocHost((pointer), (bytes))
#define wgse_gpu_host_free cudaFreeHost
#endif

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>

namespace wgse::gpu {

// Set once the watchdog gives up on the GPU: buffers are then leaked rather than
// freed, since freeing waits for the hung work.
inline std::atomic<bool> &gpu_hung() {
    static std::atomic<bool> value{false};
    return value;
}

inline void check(wgse_gpu_error error, const char *what) {
    if (error != WGSE_GPU_SUCCESS)
        throw std::runtime_error(std::string("GPU ") + what + ": " + WGSE_GPU(GetErrorString)(error));
}

// Device memory of `count` T, uninitialised.
template <class T>
class DeviceBuffer {
public:
    DeviceBuffer() = default;
    explicit DeviceBuffer(size_t count) { reserve(count); }
    DeviceBuffer(const DeviceBuffer &) = delete;
    DeviceBuffer &operator=(const DeviceBuffer &) = delete;
    ~DeviceBuffer() { release(); }

    // Grows to at least count elements (half again, to limit reallocations);
    // the contents are not kept.
    void reserve(size_t count) {
        if (count <= count_ && data_) return;
        release();
        count = std::max<size_t>(count, 1);
        if (count_) count += count / 2;
        check(WGSE_GPU(Malloc)(reinterpret_cast<void **>(&data_), count * sizeof(T)),
            "allocation");
        count_ = count;
    }
    void release() {
        if (data_ && !gpu_hung()) (void)WGSE_GPU(Free)(data_);
        data_ = nullptr;
        count_ = 0;
    }
    T *data() const { return data_; }
    size_t size() const { return count_; }

private:
    T *data_ = nullptr;
    size_t count_ = 0;
};

// Page-locked host memory, which the GPU copies to and from at full speed.
template <class T>
class PinnedBuffer {
public:
    PinnedBuffer() = default;
    explicit PinnedBuffer(size_t count) { reserve(count); }
    PinnedBuffer(const PinnedBuffer &) = delete;
    PinnedBuffer &operator=(const PinnedBuffer &) = delete;
    ~PinnedBuffer() { release(); }

    void reserve(size_t count) {
        if (count <= count_ && data_) return;
        release();
        count = std::max<size_t>(count, 1);
        if (count_) count += count / 2;
        check(wgse_gpu_host_alloc(reinterpret_cast<void **>(&data_), count * sizeof(T)),
            "pinned allocation");
        count_ = count;
    }
    void release() {
        if (data_ && !gpu_hung()) (void)wgse_gpu_host_free(data_);
        data_ = nullptr;
        count_ = 0;
    }
    T *data() const { return data_; }
    size_t size() const { return count_; }

private:
    T *data_ = nullptr;
    size_t count_ = 0;
};

// Bounds each wait for the GPU. A stage's limit is generous, so slow GPUs never
// trip it: the larger of a floor and `factor` times the slowest batch of that
// stage so far. When a wait times out, the GPU is marked hung (gpu_hung), the
// wait throws, and the caller finishes the work on the CPU.
class Watchdog {
public:
    enum Stage { seeding, locate, rescue, extend, stages };

    explicit Watchdog(double floor_seconds = 60, double factor = 20)
        : floor_(floor_seconds), factor_(factor) {}

    void set_floor(double seconds) { floor_ = seconds; }

    double limit(Stage stage) const {
        std::lock_guard<std::mutex> lock(mutex_);
        return std::max(floor_, factor_ * slowest_[stage]);
    }

    // Waits for `event` (recorded after the stage's work); throws on a GPU
    // error or when the stage's limit passes.
    void wait(wgse_gpu_event event, Stage stage, const char *what) {
        if (gpu_hung()) throw std::runtime_error(std::string("GPU ") + what + ": GPU hung earlier");
        const auto start = std::chrono::steady_clock::now();
        const double allowed = limit(stage);
        auto pause = std::chrono::microseconds(20);
        for (;;) {
            const wgse_gpu_error status = WGSE_GPU(EventQuery)(event);
            if (status == WGSE_GPU_SUCCESS) break;
            if (status != WGSE_GPU_NOT_READY) check(status, what);
            const double waited = seconds_since(start);
            if (waited > allowed) {
                gpu_hung() = true;
                throw std::runtime_error(std::string("GPU watchdog: ") + what
                    + " did not finish within " + std::to_string(int(allowed)) + " s");
            }
            std::this_thread::sleep_for(pause);
            pause = std::min(pause * 2, std::chrono::microseconds(1000));
        }
        record(stage, seconds_since(start));
    }

private:
    mutable std::mutex mutex_;
    double floor_, factor_;
    double slowest_[stages] = {};

    static double seconds_since(std::chrono::steady_clock::time_point start) {
        return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    }
    void record(Stage stage, double seconds) {
        std::lock_guard<std::mutex> lock(mutex_);
        slowest_[stage] = std::max(slowest_[stage], seconds);
    }
};
} // namespace wgse::gpu
