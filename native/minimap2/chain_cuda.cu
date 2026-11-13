// The CUDA device of the chaining offload (chain_engine.h): chain_fill.cuh's kernels on
// the GPU the gap fills use. Each slot (dispatcher thread) has its own stream and
// buffers, allocated up front, as in ksw_cuda.cu.
#include "chain_engine.h"
#include "chain_gpu.h"
#include "chain_fill.cuh"
#include "cuda_device.h"
#include "gpu_runtime.h"

#include <atomic>
#include <memory>
#include <string>
#include <vector>

using namespace wgse::gpu;
using namespace wgse::chain;
namespace kc = wgse::kernels::chain;

namespace {

constexpr unsigned block_size = 128;

std::atomic<bool> hang_pending{false};
double hang_floor = 0;

__global__ void spin(uint64_t nanoseconds) {
    const uint64_t start = clock64();
    while (uint64_t(clock64() - start) < nanoseconds * 2) __nanosleep(1000);
}

class CudaDevice final : public Device {
public:
    bool probe(int slots, size_t anchors, size_t runs, std::string &reason) override {
        size_t free = 0;
        ordinal_ = wgse::cuda::choose_device(free, reason);
        if (ordinal_ < 0) return false;
        const size_t needed = size_t(slots) * slot_bytes(anchors, runs) + wgse::cuda::spare_bytes;
        if (free < needed) {
            reason = "the GPU has " + std::to_string(free >> 20) + " MiB free, less than the "
                + std::to_string(needed >> 20) + " MiB needed for chaining";
            return false;
        }
        return true;
    }

    bool start(int slots, size_t anchors, size_t runs, std::string &reason) override {
        if (!probe(slots, anchors, runs, reason)) return false;
        max_anchors_ = anchors, max_runs_ = runs;
        try {
            check(WGSE_GPU(SetDevice)(ordinal_), "device selection");
            watchdog_.set_floor(hang_pending ? hang_floor : 60);
            for (int k = 0; k < slots; ++k) slots_.push_back(std::make_unique<Slot>(anchors, runs));
        } catch (const std::exception &error) {
            reason = error.what();
            return false;
        }
        bytes_ = size_t(slots) * slot_bytes(anchors, runs);
        return true;
    }

    bool stage(int slot, size_t anchors, size_t runs, Batch &batch, std::string &reason) override {
        if (anchors > max_anchors_ || runs > max_runs_) {
            reason = "a chaining batch exceeds the CUDA buffers";
            return false;
        }
        Slot &s = *slots_[(size_t)slot];
        batch.anchors = s.host_anchors.data();
        batch.runs = s.host_runs.data();
        batch.scores = s.host_scores.data();
        return true;
    }

    bool run(int slot, const Params &params, size_t n_short, size_t runs, size_t anchors,
        double &gpu_seconds, std::string &reason) override {
        Slot &s = *slots_[(size_t)slot];
        try {
            check(WGSE_GPU(SetDevice)(ordinal_), "device selection");
            const wgse_gpu_stream stream = s.stream;
            check(WGSE_GPU(MemcpyAsync)(s.anchors.data(), s.host_anchors.data(), anchors * sizeof(Anchor),
                WGSE_GPU(MemcpyHostToDevice), stream), "anchor upload");
            check(WGSE_GPU(MemcpyAsync)(s.runs.data(), s.host_runs.data(), runs * sizeof(Run),
                WGSE_GPU(MemcpyHostToDevice), stream), "run upload");
            check(WGSE_GPU(MemsetAsync)(s.t.data(), 0, anchors * sizeof(int32_t), stream), "clear");
            if (hang_pending.exchange(false)) spin<<<1, 1, 0, stream>>>(uint64_t(watchdog_.limit(Watchdog::chaining) * 2e9));
            check(WGSE_GPU(EventRecord)(s.started, stream), "event");
            // The long runs first: they take longest, and the short ones fill in around them.
            if (runs > n_short) {
                const size_t threads = (runs - n_short) * 32;
                kc::fill_long<<<unsigned((threads + block_size - 1) / block_size), block_size, 0, stream>>>(
                    s.anchors.data(), s.runs.data(), s.scores.data(), s.t.data(), uint32_t(n_short), uint32_t(runs), params);
            }
            if (n_short)
                kc::fill_short<<<unsigned((n_short + block_size - 1) / block_size), block_size, 0, stream>>>(
                    s.anchors.data(), s.runs.data(), s.scores.data(), s.t.data(), uint32_t(n_short), params);
            check(WGSE_GPU(GetLastError)(), "chain_fill launch");
            check(WGSE_GPU(EventRecord)(s.finished, stream), "event");
            check(WGSE_GPU(MemcpyAsync)(s.host_scores.data(), s.scores.data(), anchors * sizeof(Score),
                WGSE_GPU(MemcpyDeviceToHost), stream), "score download");
            check(WGSE_GPU(EventRecord)(s.done, stream), "event");
            watchdog_.wait(s.done, Watchdog::chaining, "chain_fill");
            float milliseconds = 0;
            check(WGSE_GPU(EventElapsedTime)(&milliseconds, s.started, s.finished), "event timing");
            gpu_seconds = milliseconds * 1e-3;
            return true;
        } catch (const std::exception &error) {
            reason = error.what();
            return false;
        }
    }

    size_t buffer_bytes() const override { return bytes_; }

private:
    static size_t slot_bytes(size_t anchors, size_t runs) {
        return anchors * (sizeof(Anchor) + sizeof(Score) + sizeof(int32_t)) + runs * sizeof(Run);
    }

    struct Slot {
        Slot(size_t anchors, size_t runs) {
            check(WGSE_GPU(StreamCreateWithFlags)(&stream, WGSE_GPU(StreamNonBlocking)), "stream");
            check(WGSE_GPU(EventCreate)(&started), "event");
            check(WGSE_GPU(EventCreate)(&finished), "event");
            check(WGSE_GPU(EventCreateWithFlags)(&done, WGSE_GPU(EventDisableTiming)), "event");
            this->anchors.reserve(anchors), host_anchors.reserve(anchors);
            scores.reserve(anchors), host_scores.reserve(anchors);
            t.reserve(anchors);
            this->runs.reserve(runs), host_runs.reserve(runs);
        }
        wgse_gpu_stream stream{};
        wgse_gpu_event started{}, finished{}, done{};
        DeviceBuffer<Anchor> anchors;
        DeviceBuffer<Run> runs;
        DeviceBuffer<Score> scores;
        DeviceBuffer<int32_t> t;
        PinnedBuffer<Anchor> host_anchors;
        PinnedBuffer<Run> host_runs;
        PinnedBuffer<Score> host_scores;
    };

    int ordinal_ = -1;
    size_t max_anchors_ = 0, max_runs_ = 0, bytes_ = 0;
    Watchdog watchdog_;
    std::vector<std::unique_ptr<Slot>> slots_;
};

} // namespace

std::unique_ptr<Device> wgse::chain::make_device() {
    return std::make_unique<CudaDevice>();
}

void wgse_chain_inject_hang(const char *label, double timeout) {
    if (std::string("chain_fill").rfind(label, 0) != 0) return;
    hang_pending = true;
    hang_floor = timeout;
}
