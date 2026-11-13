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
#include <vector>

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

struct DeviceMemory {
    static constexpr const char *what = "allocation";
    static wgse_gpu_error allocate(void **pointer, size_t bytes) {
        return WGSE_GPU(Malloc)(pointer, bytes);
    }
    static void free(void *pointer) { (void)WGSE_GPU(Free)(pointer); }
};

// Page-locked host memory, which the GPU copies to and from at full speed.
struct PinnedMemory {
    static constexpr const char *what = "pinned allocation";
    static wgse_gpu_error allocate(void **pointer, size_t bytes) {
        return wgse_gpu_host_alloc(pointer, bytes);
    }
    static void free(void *pointer) { (void)wgse_gpu_host_free(pointer); }
};

// `count` T of Memory, uninitialised. Freeing waits for all of the device's
// work, including another stage's on its own stream, which may be hung; so
// growing only allocates, and the outgrown storage (at most about twice the
// final size, as each growth adds half) is freed by release() or the destructor,
// once the stages are done.
template <class T, class Memory>
class GpuBuffer {
public:
    GpuBuffer() = default;
    explicit GpuBuffer(size_t count) { reserve(count); }
    GpuBuffer(const GpuBuffer &) = delete;
    GpuBuffer &operator=(const GpuBuffer &) = delete;
    ~GpuBuffer() { release(); }

    // Grows to at least count elements (half again when it grows, to limit
    // reallocations); the contents are not kept.
    void reserve(size_t count) {
        if (count <= count_ && data_) return;
        count = std::max<size_t>(count, 1);
        if (data_) count += count / 2;
        T *grown = nullptr;
        check(Memory::allocate(reinterpret_cast<void **>(&grown), count * sizeof(T)), Memory::what);
        if (data_) outgrown_.push_back(data_);
        data_ = grown;
        count_ = count;
    }
    void release() {
        if (data_) outgrown_.push_back(data_);
        if (!gpu_hung())
            for (T *pointer : outgrown_) Memory::free(pointer);
        outgrown_.clear();
        data_ = nullptr;
        count_ = 0;
    }
    T *data() const { return data_; }
    size_t size() const { return count_; }

private:
    T *data_ = nullptr;
    size_t count_ = 0;
    std::vector<T *> outgrown_;
};

template <class T>
using DeviceBuffer = GpuBuffer<T, DeviceMemory>;
template <class T>
using PinnedBuffer = GpuBuffer<T, PinnedMemory>;

// Bounds each wait for the GPU. A stage's limit is generous, so slow GPUs never
// trip it: the larger of a floor and `factor` times the slowest batch of that
// stage so far. When a wait times out, the GPU is marked hung (gpu_hung), the
// wait throws, and the caller finishes the work on the CPU.
class Watchdog {
public:
    enum Stage { seeding, locate, rescue, extend, gap_fill, stages };

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
