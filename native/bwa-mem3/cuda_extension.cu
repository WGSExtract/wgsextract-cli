// GPU seed extension (--cuda) for bwa-mem3's 8-bit tier on NVIDIA GPUs: extension.cuh's
// kernels with metal_extension.mm's scheduling. Workers' getScores8 calls are packed
// (extension_host.h) and aggregated into batches, each on its own stream; unlike Metal's
// unified memory, a batch's pairs and bases cross PCIe through pinned buffers.
#include "cuda_common.h"
#include "extension.cuh"
#include "extension_host.h"
#include "gpu_runtime.h"
#include "metal_extension.h"

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <thread>

using namespace wgse::mem3;
using namespace wgse::gpu;
namespace ek = wgse::kernels::mem3_extension;

static_assert(sizeof(ek::Pair) == sizeof(WgseBswJob), "extension.cuh's Pair");
static_assert(sizeof(ek::Parameters) == sizeof(Parameters), "extension.cuh's Parameters");

namespace {

constexpr unsigned block_size = 128;
constexpr int32_t max_pairs = 1 << 16;
// A batch's DP state; a request with more is scored on the CPU.
constexpr size_t max_scratch_cells = size_t(8) << 20;
// A batch's query words (a 256-base query is 32 words) and reference words (three times as
// many); a request with more is scored on the CPU. Every slot's buffers are allocated at this
// size when the engine starts: an allocation can wait for the GPU's running kernels, and the
// submitter must never block while its watchdog is due.
constexpr size_t max_query_words = size_t(max_pairs) * (wgse_mem3_max_gpu_query_length / 8);
constexpr size_t max_reference_words = 3 * max_query_words;
// Pairs worth a second batch while one runs; below it, they wait for the GPU.
constexpr int32_t target_pairs = 8192;
// Batches in flight, each on its own stream.
constexpr int slot_count = 2;
// The watchdog: a batch may take the larger of the floor and `factor` times the slowest
// batch so far, before the GPU is given up on.
constexpr double watchdog_factor = 20;

std::atomic<bool> enabled{false}, failed{false}, inject_failure{false};
std::mutex failure_mutex;
std::string failure_reason;
std::atomic<uint64_t> calls{0}, batches{0}, pairs_total{0}, gpu_ns{0}, wait_ns{0},
    cpu_fallbacks{0}, backlog_calls{0};
std::atomic<int64_t> queued_pairs{0};
int64_t backlog = 0;
double watchdog_floor = 60;

void fail(const std::string &reason) {
    std::lock_guard<std::mutex> lock(failure_mutex);
    if (failure_reason.empty()) failure_reason = reason;
    failed = true;
}

// Test hook: occupies the stream for about `nanoseconds`, as a hung kernel would.
__global__ void spin(uint64_t nanoseconds) {
    const uint64_t start = clock64();
    // clock64 counts SM cycles; 2 per nanosecond overestimates no current GPU.
    while (uint64_t(clock64() - start) < nanoseconds * 2) __nanosleep(1000);
}

// Whether the current device can run this build's kernels: an older GPU than
// CMAKE_CUDA_ARCHITECTURES covers has no machine code or PTX for them.
wgse_gpu_error runs_kernels() {
    WGSE_GPU(FuncAttributes) attributes;
    const wgse_gpu_error status = WGSE_GPU(FuncGetAttributes)(&attributes,
        reinterpret_cast<const void *>(ek::extend_striped));
    (void)WGSE_GPU(GetLastError)();
    return status;
}

// Requests aggregated into one batch: one launch per scoring and kind of pair.
struct Slot {
    enum Status { free, executing, completed } status = free;
    PinnedBuffer<WgseBswJob> host_pairs;
    PinnedBuffer<uint32_t> host_reference, host_query;
    DeviceBuffer<WgseBswJob> pairs;
    DeviceBuffer<uint32_t> reference, query;
    DeviceBuffer<ushort2> scratch;
    wgse_gpu_stream stream = nullptr;
    wgse_gpu_event done = nullptr;
    std::vector<Request *> requests;
    // Each pair's result, in the slot's order.
    std::vector<WgseBswJob *> targets;
    struct Group {
        int32_t first, count;
        bool long_pairs;
        Parameters parameters;
    };
    std::vector<Group> groups;
    std::chrono::steady_clock::time_point submitted;
    size_t count = 0;
    std::string failure;
};

class Engine {
public:
    Engine() {
        std::string reason;
        ordinal_ = wgse_mem3_cuda_device(reason);
        if (ordinal_ < 0) {
            failure_ = reason;
            return;
        }
        size_t best_free = 0, total = 0;
        if (WGSE_GPU(SetDevice)(ordinal_) != WGSE_GPU_SUCCESS
            || WGSE_GPU(MemGetInfo)(&best_free, &total) != WGSE_GPU_SUCCESS) {
            failure_ = "the GPU did not answer";
            ordinal_ = -1;
            return;
        }
        // slot_count batches of max_pairs pairs, each with its query (128 bytes for 256
        // bases, 8 a 32-bit word) and its reference (allowing three times that), and their
        // DP state; and 1 GiB spare, as the GPU may be shared.
        const uint64_t needed = uint64_t(slot_count) * (max_pairs * (sizeof(WgseBswJob)
            + 2 * wgse_mem3_max_gpu_query_length) + max_scratch_cells * sizeof(ushort2));
        if (best_free < needed + (uint64_t(1) << 30)) {
            failure_ = "the GPU has " + std::to_string(best_free >> 20) + " MiB free, below the "
                + std::to_string((needed >> 20) + 1024) + " MiB seed extension needs";
            ordinal_ = -1;
        }
    }
    ~Engine() {
        {
            std::lock_guard<std::mutex> lock(mutex);
            stopping = true;
            changed.notify_all();
        }
        if (submitter.joinable()) submitter.join();
        if (gpu_hung()) return;
        for (Slot &slot : slots) {
            if (slot.done) (void)WGSE_GPU(EventDestroy)(slot.done);
            if (slot.stream) (void)WGSE_GPU(StreamDestroy)(slot.stream);
        }
    }
    const std::string &failure() const { return failure_; }

    bool start(std::string &reason) {
        if (!failure_.empty()) {
            reason = failure_;
            return false;
        }
        try {
            check(WGSE_GPU(SetDevice)(ordinal_), "device selection");
            for (Slot &slot : slots) {
                check(WGSE_GPU(StreamCreateWithFlags)(&slot.stream, WGSE_GPU(StreamNonBlocking)),
                    "stream");
                check(WGSE_GPU(EventCreateWithFlags)(&slot.done, WGSE_GPU(EventDisableTiming)),
                    "event");
            }
        } catch (const std::exception &error) {
            reason = error.what();
            return false;
        }
        std::lock_guard<std::mutex> lock(mutex);
        if (!submitter.joinable()) submitter = std::thread([this] { run(); });
        return true;
    }

    // Every slot's buffers at their largest (wgse_mem3_cuda_prepare); false if they don't fit.
    bool prepare() {
        try {
            check(WGSE_GPU(SetDevice)(ordinal_), "device selection");
            for (Slot &slot : slots) {
                slot.host_pairs.reserve(max_pairs);
                slot.host_reference.reserve(max_reference_words);
                slot.host_query.reserve(max_query_words);
                slot.pairs.reserve(max_pairs);
                slot.reference.reserve(max_reference_words);
                slot.query.reserve(max_query_words);
                slot.scratch.reserve(max_scratch_cells);
            }
            prepared_ = true;
        } catch (const std::exception &error) {
            fail(std::string("GPU seed extension: ") + error.what());
        }
        return prepared_;
    }

    bool score(Request &request) {
        wgse_mem3_cuda_prepare();
        if (!prepared_) return false;
        std::unique_lock<std::mutex> lock(mutex);
        request.enqueued = now_ns();
        pending.push_back(&request);
        pending_pairs += request.count;
        queued_pairs = pending_pairs;
        changed.notify_one();
        done.wait(lock, [&request] { return request.done; });
        wait_ns += now_ns() - request.enqueued;
        return request.ok;
    }

private:
    std::string failure_;
    int ordinal_ = -1;
    std::atomic<bool> prepared_{false};
    std::mutex mutex;
    std::condition_variable changed, done;
    std::vector<Request *> pending;
    int64_t pending_pairs = 0;
    Slot slots[slot_count];
    bool stopping = false;
    std::thread submitter;
    double slowest_batch = 0;

    // Takes pending requests, up to the slot's buffers, ordered by scoring. Holds the lock.
    void take(Slot &slot) {
        slot.requests.clear();
        int32_t count = 0;
        size_t cells = 0, ref_words = 0, qer_words = 0;
        size_t kept = 0;
        for (Request *request : pending) {
            if (count > 0 && (count + request->count > max_pairs
                || cells + request->scratch_cells > max_scratch_cells
                || ref_words + request->ref_word_count > max_reference_words
                || qer_words + request->qer_word_count > max_query_words)) {
                pending[kept++] = request;
                continue;
            }
            slot.requests.push_back(request);
            count += request->count;
            cells += request->scratch_cells;
            ref_words += request->ref_word_count;
            qer_words += request->qer_word_count;
        }
        pending.resize(kept);
        pending_pairs -= count;
        queued_pairs = pending_pairs;
        std::stable_sort(slot.requests.begin(), slot.requests.end(),
            [](const Request *a, const Request *b) {
                return scoring_less(a->parameters, b->parameters);
            });
    }

    // Concatenates the slot's packed requests, rebasing their offsets, and queues the
    // batch: its upload, its kernels and its results' download.
    void dispatch(Slot &slot) {
        if (gpu_hung()) {
            slot.failure = wgse_mem3_cuda_hang();
            return;
        }
        size_t count = 0, ref_words = 0, qer_words = 0, cells = 0;
        for (const Request *request : slot.requests) {
            count += size_t(request->count);
            ref_words += request->ref_word_count;
            qer_words += request->qer_word_count;
            cells += request->scratch_cells;
        }
        // take() keeps a batch within the buffers start() allocated.
        if (count > slot.pairs.size() || ref_words > slot.reference.size()
            || qer_words > slot.query.size() || cells > slot.scratch.size()) {
            slot.failure = "a seed-extension batch outgrew its GPU buffers";
            return;
        }
        WgseBswJob *jobs = slot.host_pairs.data();
        uint32_t *reference = slot.host_reference.data(), *query = slot.host_query.data();
        slot.groups.clear();
        slot.targets.resize(count);
        size_t index = 0;
        // The one-thread pairs, then the long ones, each in runs of one scoring.
        for (const bool long_pairs : {false, true}) {
            size_t rbase = 0, qbase = 0, sbase = 0;
            for (const Request *request : slot.requests) {
                const int32_t first = long_pairs ? request->count - request->long_count : 0;
                const int32_t last = long_pairs ? request->count : request->count - request->long_count;
                if (first < last && (slot.groups.empty() || slot.groups.back().long_pairs != long_pairs
                    || !same_scoring(slot.groups.back().parameters, request->parameters)))
                    slot.groups.push_back({int32_t(index), 0, long_pairs, request->parameters});
                if (!long_pairs) {
                    std::memcpy(reference + rbase, request->ref_words, request->ref_word_count * sizeof(uint32_t));
                    std::memcpy(query + qbase, request->qer_words, request->qer_word_count * sizeof(uint32_t));
                }
                for (int32_t i = first; i < last; ++i) {
                    WgseBswJob job = request->packed[i];
                    slot.targets[index] = request->jobs + job.seqid;
                    job.idr += int32_t(rbase);
                    job.idq += int32_t(qbase);
                    job.id += int32_t(sbase);
                    jobs[index++] = job;
                    ++slot.groups.back().count;
                }
                rbase += request->ref_word_count;
                qbase += request->qer_word_count;
                sbase += request->scratch_cells;
            }
        }
        slot.count = count;
        const auto stream = slot.stream;
        try {
            check(WGSE_GPU(MemcpyAsync)(slot.pairs.data(), jobs, count * sizeof(WgseBswJob),
                WGSE_GPU(MemcpyHostToDevice), stream), "extension upload");
            check(WGSE_GPU(MemcpyAsync)(slot.reference.data(), reference, ref_words * sizeof(uint32_t),
                WGSE_GPU(MemcpyHostToDevice), stream), "extension upload");
            check(WGSE_GPU(MemcpyAsync)(slot.query.data(), query, qer_words * sizeof(uint32_t),
                WGSE_GPU(MemcpyHostToDevice), stream), "extension upload");
            if (wgse_mem3_cuda_take_hang("extend")) {
                spin<<<1, 1, 0, stream>>>(uint64_t(limit() * 2e9));
            }
            auto *pairs = reinterpret_cast<ek::Pair *>(slot.pairs.data());
            for (const Slot::Group &group : slot.groups) {
                Parameters parameters = group.parameters;
                parameters.count = group.count;
                ek::Parameters p;
                std::memcpy(&p, &parameters, sizeof(p));
                // A pair per thread, or a segment of ek::lanes per long pair.
                if (group.long_pairs) {
                    const unsigned per_block = ek::long_segments * ek::lanes;
                    const size_t threads = size_t(group.count) * ek::lanes;
                    ek::extend_long<<<unsigned((threads + per_block - 1) / per_block), per_block, 0,
                        stream>>>(pairs + group.first, slot.reference.data(), slot.query.data(), p);
                } else {
                    ek::extend_striped<<<unsigned((size_t(group.count) + block_size - 1) / block_size),
                        block_size, 0, stream>>>(pairs + group.first, slot.reference.data(),
                        slot.query.data(), p, slot.scratch.data());
                }
            }
            check(WGSE_GPU(GetLastError)(), "extension launch");
            check(WGSE_GPU(MemcpyAsync)(jobs, slot.pairs.data(), count * sizeof(WgseBswJob),
                WGSE_GPU(MemcpyDeviceToHost), stream), "extension download");
            check(WGSE_GPU(EventRecord)(slot.done, stream), "extension");
        } catch (const std::exception &error) {
            slot.failure = error.what();
            return;
        }
        slot.submitted = std::chrono::steady_clock::now();
    }

    double limit() const { return std::max(watchdog_floor, watchdog_factor * slowest_batch); }

    // Whether the slot's batch has finished, failed or overrun the watchdog.
    bool settled(Slot &slot) {
        const wgse_gpu_error status = WGSE_GPU(EventQuery)(slot.done);
        if (status == WGSE_GPU_SUCCESS) return true;
        if (status != WGSE_GPU_NOT_READY) {
            slot.failure = std::string("GPU seed extension: ") + WGSE_GPU(GetErrorString)(status);
            return true;
        }
        const double waited = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - slot.submitted).count();
        if (waited > limit()) {
            slot.failure = "GPU watchdog: a CUDA command (extend) did not finish within "
                + std::to_string(int(limit())) + " s";
            wgse_mem3_cuda_set_hang(slot.failure);
            return true;
        }
        return false;
    }

    // Stores the results, or fails the slot's requests to the CPU.
    void finish(Slot &slot) {
        if (slot.failure.empty() && inject_failure)
            slot.failure = "injected GPU extension failure (test)";
        if (slot.failure.empty()) {
            const double seconds = std::chrono::duration<double>(
                std::chrono::steady_clock::now() - slot.submitted).count();
            slowest_batch = std::max(slowest_batch, seconds);
            gpu_ns += uint64_t(seconds * 1e9);
            const WgseBswJob *jobs = slot.host_pairs.data();
            for (size_t i = 0; i < slot.count; ++i) {
                const WgseBswJob &in = jobs[i];
                WgseBswJob &out = *slot.targets[i];
                out.score = in.score;
                out.tle = in.tle;
                out.gtle = in.gtle;
                out.qle = in.qle;
                out.gscore = in.gscore;
                out.max_off = in.max_off;
            }
            ++batches;
            pairs_total += slot.count;
        } else {
            fail(slot.failure);
        }
        std::lock_guard<std::mutex> lock(mutex);
        for (Request *request : slot.requests) {
            request->ok = slot.failure.empty();
            request->done = true;
        }
        done.notify_all();
        slot.requests.clear();
        slot.failure.clear();
        slot.status = Slot::free;
    }

    void run() {
        // The submitter's launches go to the engine's GPU.
        if (WGSE_GPU(SetDevice)(ordinal_) != WGSE_GPU_SUCCESS) fail("GPU seed extension: device selection");
        std::unique_lock<std::mutex> lock(mutex);
        auto pause = std::chrono::microseconds(20);
        for (;;) {
            Slot *free = nullptr, *completed = nullptr;
            bool executing = false;
            for (Slot &slot : slots) {
                if (slot.status == Slot::executing && settled(slot)) slot.status = Slot::completed;
                if (slot.status == Slot::free) free = &slot;
                if (slot.status == Slot::completed) completed = &slot;
                executing |= slot.status == Slot::executing;
            }
            if (completed) {
                lock.unlock();
                finish(*completed);
                lock.lock();
                pause = std::chrono::microseconds(20);
            } else if (free && !pending.empty() && (!executing || pending_pairs >= target_pairs)) {
                take(*free);
                free->status = Slot::executing;
                lock.unlock();
                dispatch(*free);
                lock.lock();
                // A batch that could not be queued fails at once.
                if (!free->failure.empty()) free->status = Slot::completed;
                pause = std::chrono::microseconds(20);
            } else if (stopping && pending.empty() && !executing) {
                return;
            } else if (executing) {
                // Polls the running batches, as the CUDA runtime has no completion callback
                // that may call it back.
                changed.wait_for(lock, pause);
                pause = std::min(pause * 2, std::chrono::microseconds(500));
            } else {
                changed.wait(lock);
            }
        }
    }
};

Engine &engine() {
    static Engine value;
    return value;
}

int thread_percent = wgse_mem3_default_thread_percent;

} // namespace

const char *wgse_mem3_gpu_name() { return "CUDA"; }

bool wgse_mem3_gpu_hardware_supported() {
    int count = 0;
    if (WGSE_GPU(GetDeviceCount)(&count) != WGSE_GPU_SUCCESS) return false;
    for (int ordinal = 0; ordinal < count; ++ordinal)
        if (WGSE_GPU(SetDevice)(ordinal) == WGSE_GPU_SUCCESS && runs_kernels() == WGSE_GPU_SUCCESS)
            return true;
    return false;
}

// CUDA reports no interrupted commands to commit again.
void wgse_mem3_gpu_inject_interruptions(size_t) {}

namespace {
std::mutex hang_mutex;
std::string hang_label, hang_reason;
} // namespace

void wgse_mem3_gpu_inject_hang(const char *label) {
    std::lock_guard<std::mutex> lock(hang_mutex);
    hang_label = label;
    watchdog_floor = 2;
}

bool wgse_mem3_cuda_take_hang(const char *stage) {
    std::lock_guard<std::mutex> lock(hang_mutex);
    if (hang_label.empty() || std::string(stage).rfind(hang_label, 0) != 0) return false;
    hang_label.clear();
    return true;
}

double wgse_mem3_cuda_watchdog_floor() { return watchdog_floor; }

void wgse_mem3_cuda_prepare() {
    static std::once_flag once;
    std::call_once(once, [] {
        if (enabled) engine().prepare();
        wgse_mem3_rescue_prepare();
    });
}

std::string wgse_mem3_cuda_hang() {
    std::lock_guard<std::mutex> lock(hang_mutex);
    // Watchdog::wait marks the GPU hung before its caller records why.
    if (hang_reason.empty() && gpu_hung()) return "GPU watchdog: the GPU hung";
    return hang_reason;
}

void wgse_mem3_cuda_set_hang(const std::string &reason) {
    std::lock_guard<std::mutex> lock(hang_mutex);
    if (hang_reason.empty()) hang_reason = reason;
    gpu_hung() = true;
}

int wgse_mem3_cuda_device(std::string &reason) {
    static std::once_flag once;
    static int chosen = -1;
    static std::string why;
    std::call_once(once, [] {
        int count = 0;
        const wgse_gpu_error status = WGSE_GPU(GetDeviceCount)(&count);
        if (status != WGSE_GPU_SUCCESS || count == 0) {
            why = status != WGSE_GPU_SUCCESS
                ? std::string("no usable NVIDIA GPU: ") + WGSE_GPU(GetErrorString)(status)
                : "no NVIDIA GPU";
            return;
        }
        size_t best_free = 0;
        std::string unsupported;
        for (int ordinal = 0; ordinal < count; ++ordinal) {
            size_t free = 0, total = 0;
            if (WGSE_GPU(SetDevice)(ordinal) != WGSE_GPU_SUCCESS) continue;
            const wgse_gpu_error kernels = runs_kernels();
            if (kernels != WGSE_GPU_SUCCESS) {
                unsupported = WGSE_GPU(GetErrorString)(kernels);
                continue;
            }
            if (WGSE_GPU(MemGetInfo)(&free, &total) != WGSE_GPU_SUCCESS) continue;
            if (chosen < 0 || free > best_free) {
                chosen = ordinal;
                best_free = free;
            }
        }
        if (chosen < 0)
            why = unsupported.empty() ? "no NVIDIA GPU answered"
                : "no NVIDIA GPU this build has kernels for: " + unsupported;
    });
    reason = why;
    return chosen;
}

bool wgse_mem3_bsw_available(std::string &reason) {
    reason = engine().failure();
    return reason.empty();
}

WgseMem3MetalLimits wgse_mem3_metal_limits() { return {}; }

bool wgse_mem3_bsw_start(std::string &reason) {
    if (!engine().start(reason)) return false;
    enabled = true;
    return true;
}

void wgse_mem3_bsw_decline() {
    ++calls;
    ++cpu_fallbacks;
}

void wgse_mem3_bsw_inject_failure() { inject_failure = true; }

int wgse_mem3_bsw_finish() {
    if (!enabled) return 0;
    fprintf(stderr, "[wgse-bwa-mem3] extension_calls=%llu extension_cpu_calls=%llu "
        "extension_batches=%llu extension_pairs=%llu extension_gpu_s=%.2f extension_wait_s=%.2f "
        "extension_backlog_calls=%llu\n",
        (unsigned long long)calls.load(), (unsigned long long)cpu_fallbacks.load(),
        (unsigned long long)batches.load(), (unsigned long long)pairs_total.load(),
        gpu_ns.load() * 1e-9, wait_ns.load() * 1e-9, (unsigned long long)backlog_calls.load());
    if (!failed) return 0;
    std::lock_guard<std::mutex> lock(failure_mutex);
    fprintf(stderr, "[wgse-bwa-mem3] CPU fallback: GPU seed extension failed (%s); "
        "the CPU scored the rest\n", failure_reason.c_str());
    return 1;
}

void wgse_mem3_bsw_set_threads(int percent) { thread_percent = percent; }

int wgse_mem3_compute_threads(int threads) {
    if (!enabled) return threads;
    const int result = std::min(256, std::max(threads, (threads * thread_percent + 50) / 100));
    fprintf(stderr, "[wgse-bwa-mem3] --cuda: %d compute threads for -t %d\n", result, threads);
    return result;
}

bool wgse_mem3_bsw_enabled() { return enabled && !failed; }

bool wgse_mem3_bsw_busy() {
    if (backlog <= 0 || queued_pairs.load(std::memory_order_relaxed) < backlog) return false;
    ++backlog_calls;
    return true;
}

void wgse_mem3_bsw_set_backlog(int64_t pairs) { backlog = pairs; }

bool wgse_mem3_bsw_score(WgseBswJob *jobs, int32_t count, const uint8_t *ref,
    const uint8_t *qer, const WgseBswScoring &scoring) {
    ++calls;
    if (count > max_pairs || scoring.extend_deletion <= 0 || scoring.extend_insertion <= 0) {
        ++cpu_fallbacks;
        return false;
    }
    Request request{jobs, count, parameters(scoring), nullptr, nullptr, nullptr, 0, 0, 0, 0, 0};
    pack(request, jobs, count, ref, qer);
    if (request.scratch_cells > max_scratch_cells || request.ref_word_count > max_reference_words
        || request.qer_word_count > max_query_words || !engine().score(request)) {
        ++cpu_fallbacks;
        return false;
    }
    return true;
}
