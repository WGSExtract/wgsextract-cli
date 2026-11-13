// The CUDA device of the gap-fill offload (ksw_engine.h): ksw_fill.cuh's kernels on the
// GPU with the most free memory. Each slot (dispatcher thread) has its own stream and
// buffers, allocated up front for the engine's largest batch: an allocation can wait for
// the GPU's running kernels, so none happens once a batch could be waiting on a hung one.
#include "cuda_device.h"
#include "gpu_runtime.h"
#include "ksw_engine.h"
#include "ksw_fill.cuh"
#include "ksw_gpu.h"

#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

using namespace wgse::gpu;
using namespace wgse::ksw;
namespace kk = wgse::kernels::ksw;

namespace {

constexpr unsigned block_size = 128; // four fills per block

// Test hook (--gpu-inject-hang): the first batch hangs, once, and the watchdog gives up
// after `hang_floor` seconds.
std::atomic<bool> hang_pending{false};
double hang_floor = 0;

// Occupies the stream for about `nanoseconds`, as a hung kernel would.
__global__ void spin(uint64_t nanoseconds) {
    const uint64_t start = clock64();
    // clock64 counts SM cycles; 2 per nanosecond overestimates no current GPU.
    while (uint64_t(clock64() - start) < nanoseconds * 2) __nanosleep(1000);
}

// Whether this build has kernels for the current device.
bool has_kernels() {
    WGSE_GPU(FuncAttributes) attributes;
    const bool ok = WGSE_GPU(FuncGetAttributes)(&attributes, kk::ksw_fill<4, int8_t>) == WGSE_GPU_SUCCESS;
    (void)WGSE_GPU(GetLastError)();
    return ok;
}

template <int C, typename T>
void launch(const Fill *fills, const Scoring &scoring, const uint8_t *seq, uint8_t *matrix, int *score,
    uint32_t *cigar, int *n_cigar, uint32_t count, wgse_gpu_stream stream) {
    const size_t threads = size_t(count) * kk::lanes;
    kk::ksw_fill<C, T><<<unsigned((threads + block_size - 1) / block_size), block_size, 0, stream>>>(
        fills, scoring, seq, matrix, score, cigar, n_cigar, count);
}

template <typename T>
void launch_width(int k, const Fill *fills, const Scoring &scoring, const uint8_t *seq, uint8_t *matrix,
    int *score, uint32_t *cigar, int *n_cigar, uint32_t count, wgse_gpu_stream stream) {
    switch (widths[k]) {
    case 4: launch<4, T>(fills, scoring, seq, matrix, score, cigar, n_cigar, count, stream); break;
    case 7: launch<7, T>(fills, scoring, seq, matrix, score, cigar, n_cigar, count, stream); break;
    case 8: launch<8, T>(fills, scoring, seq, matrix, score, cigar, n_cigar, count, stream); break;
    case 12: launch<12, T>(fills, scoring, seq, matrix, score, cigar, n_cigar, count, stream); break;
    default: launch<16, T>(fills, scoring, seq, matrix, score, cigar, n_cigar, count, stream); break;
    }
}

class CudaDevice final : public Device {
public:
    const char *name() const override { return "CUDA"; }

    bool probe(int slots, const Limits &limits, std::string &reason) override {
        size_t free = 0;
        ordinal_ = wgse::cuda::choose_device(free, reason);
        if (ordinal_ < 0) return false;
        const size_t needed = size_t(slots) * slot_bytes(limits) + wgse::cuda::spare_bytes;
        if (free < needed) {
            reason = "the GPU has " + std::to_string(free >> 20) + " MiB free, less than the "
                + std::to_string(needed >> 20) + " MiB needed";
            return false;
        }
        return true;
    }

    bool start(int slots, const Limits &limits, std::string &reason) override {
        if (!probe(slots, limits, reason)) return false;
        limits_ = limits;
        try {
            check(WGSE_GPU(SetDevice)(ordinal_), "device selection");
            watchdog_.set_floor(hang_pending ? hang_floor : 60);
            for (int k = 0; k < slots; ++k) slots_.push_back(std::make_unique<Slot>(limits));
        } catch (const std::exception &error) {
            reason = error.what();
            return false;
        }
        bytes_ = size_t(slots) * slot_bytes(limits);
        return true;
    }

    bool stage(int slot, size_t n, size_t seq_bytes, size_t matrix_bytes, size_t cigar_words,
        Batch &batch, std::string &reason) override {
        if (n > limits_.fills || seq_bytes > limits_.seq_bytes || matrix_bytes > limits_.matrix_bytes
            || cigar_words > limits_.cigar_words) {
            reason = "a gap-fill batch exceeds the CUDA buffers";
            return false;
        }
        Slot &s = *slots_[(size_t)slot];
        s.seq_bytes = seq_bytes;
        batch.fills = s.host_fills.data();
        batch.seq = s.host_seq.data();
        batch.score = s.host_score.data();
        batch.n_cigar = s.host_n_cigar.data();
        batch.cigar = s.host_cigar.data();
        return true;
    }

    bool run(int slot, const Scoring &scoring, bool narrow, size_t n, const size_t (&first)[n_widths + 1],
        size_t cigar_words, Batch &, double &gpu_seconds, std::string &reason) override {
        Slot &s = *slots_[(size_t)slot];
        try {
            check(WGSE_GPU(SetDevice)(ordinal_), "device selection");
            const wgse_gpu_stream stream = s.stream;
            check(WGSE_GPU(MemcpyAsync)(s.fills.data(), s.host_fills.data(), n * sizeof(Fill),
                WGSE_GPU(MemcpyHostToDevice), stream), "fill upload");
            check(WGSE_GPU(MemcpyAsync)(s.seq.data(), s.host_seq.data(), s.seq_bytes,
                WGSE_GPU(MemcpyHostToDevice), stream), "sequence upload");
            if (hang_pending.exchange(false)) spin<<<1, 1, 0, stream>>>(uint64_t(watchdog_.limit(Watchdog::gap_fill) * 2e9));
            check(WGSE_GPU(EventRecord)(s.started, stream), "event");
            for (int k = 0; k < n_widths; ++k) {
                const uint32_t count = uint32_t(first[k + 1] - first[k]);
                if (!count) continue;
                const size_t f = first[k];
                if (narrow)
                    launch_width<int16_t>(k, s.fills.data() + f, scoring, s.seq.data(), s.matrix.data(),
                        s.score.data() + f, s.cigar.data(), s.n_cigar.data() + f, count, stream);
                else
                    launch_width<int8_t>(k, s.fills.data() + f, scoring, s.seq.data(), s.matrix.data(),
                        s.score.data() + f, s.cigar.data(), s.n_cigar.data() + f, count, stream);
            }
            check(WGSE_GPU(GetLastError)(), "ksw_fill launch");
            check(WGSE_GPU(EventRecord)(s.finished, stream), "event");
            check(WGSE_GPU(MemcpyAsync)(s.host_score.data(), s.score.data(), n * 4,
                WGSE_GPU(MemcpyDeviceToHost), stream), "score download");
            check(WGSE_GPU(MemcpyAsync)(s.host_n_cigar.data(), s.n_cigar.data(), n * 4,
                WGSE_GPU(MemcpyDeviceToHost), stream), "CIGAR length download");
            check(WGSE_GPU(MemcpyAsync)(s.host_cigar.data(), s.cigar.data(), cigar_words * 4,
                WGSE_GPU(MemcpyDeviceToHost), stream), "CIGAR download");
            check(WGSE_GPU(EventRecord)(s.done, stream), "event");
            watchdog_.wait(s.done, Watchdog::gap_fill, "ksw_fill");
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
    // Device memory per slot (the host has as much pinned, without the matrices).
    static size_t slot_bytes(const Limits &l) {
        return l.fills * (sizeof(Fill) + 8) + l.seq_bytes + l.matrix_bytes + l.cigar_words * 4;
    }

    struct Slot {
        explicit Slot(const Limits &l) {
            check(WGSE_GPU(StreamCreateWithFlags)(&stream, WGSE_GPU(StreamNonBlocking)), "stream");
            check(WGSE_GPU(EventCreate)(&started), "event");
            check(WGSE_GPU(EventCreate)(&finished), "event");
            check(WGSE_GPU(EventCreateWithFlags)(&done, WGSE_GPU(EventDisableTiming)), "event");
            fills.reserve(l.fills), host_fills.reserve(l.fills);
            seq.reserve(l.seq_bytes), host_seq.reserve(l.seq_bytes);
            matrix.reserve(l.matrix_bytes);
            score.reserve(l.fills), host_score.reserve(l.fills);
            n_cigar.reserve(l.fills), host_n_cigar.reserve(l.fills);
            cigar.reserve(l.cigar_words), host_cigar.reserve(l.cigar_words);
        }
        wgse_gpu_stream stream{};
        wgse_gpu_event started{}, finished{}, done{};
        size_t seq_bytes = 0;
        DeviceBuffer<Fill> fills;
        DeviceBuffer<uint8_t> seq, matrix;
        DeviceBuffer<int32_t> score, n_cigar;
        DeviceBuffer<uint32_t> cigar;
        PinnedBuffer<Fill> host_fills;
        PinnedBuffer<uint8_t> host_seq;
        PinnedBuffer<int32_t> host_score, host_n_cigar;
        PinnedBuffer<uint32_t> host_cigar;
    };

    int ordinal_ = -1;
    Limits limits_{};
    size_t bytes_ = 0;
    Watchdog watchdog_;
    std::vector<std::unique_ptr<Slot>> slots_;
};

} // namespace

int wgse::cuda::choose_device(size_t &free_bytes, std::string &reason) {
    int count = 0;
    const wgse_gpu_error error = WGSE_GPU(GetDeviceCount)(&count);
    if (error != WGSE_GPU_SUCCESS || count == 0) {
        (void)WGSE_GPU(GetLastError)();
        reason = error != WGSE_GPU_SUCCESS ? WGSE_GPU(GetErrorString)(error) : "no CUDA device";
        return -1;
    }
    int best = -1;
    free_bytes = 0;
    for (int d = 0; d < count; ++d) {
        size_t free = 0, total = 0;
        if (WGSE_GPU(SetDevice)(d) != WGSE_GPU_SUCCESS || !has_kernels()
            || WGSE_GPU(MemGetInfo)(&free, &total) != WGSE_GPU_SUCCESS) {
            (void)WGSE_GPU(GetLastError)();
            continue;
        }
        if (best < 0 || free > free_bytes) best = d, free_bytes = free;
    }
    if (best < 0) reason = "no CUDA device this build has kernels for";
    return best;
}

std::unique_ptr<Device> wgse::ksw::make_device() {
    return std::make_unique<CudaDevice>();
}

extern "C" {

int wgse_ksw_gpu_hardware_supported(void) {
    size_t free = 0;
    std::string reason;
    return wgse::cuda::choose_device(free, reason) >= 0;
}

void wgse_ksw_inject_interruptions(int) {}

void wgse_ksw_inject_hang(const char *label, double timeout) {
    if (std::string("ksw_fill").rfind(label, 0) != 0) return;
    hang_pending = true;
    hang_floor = timeout;
}

} // extern "C"
