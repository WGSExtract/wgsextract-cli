#include "metal_extension.h"

#include "extension_source.h"
#include "runtime.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <mutex>
#include <pthread.h>
#include <sys/sysctl.h>
#include <thread>

namespace {

// extension.metal's Parameters.
struct Parameters {
    int32_t count, bandwidth, open_deletion, extend_deletion, open_insertion, extend_insertion;
    int32_t zdrop, end_bonus, max_substitution;
    int8_t matrix[25];
    bool simple_matrix;
    uint8_t precision;
};
static_assert(sizeof(Parameters) == 64, "extension.metal's Parameters");
static_assert(sizeof(WgseBswJob) == 15 * sizeof(int32_t), "extension.metal's Pair");

bool same_scoring(const Parameters &a, const Parameters &b) {
    return std::memcmp(&a.open_deletion, &b.open_deletion,
        sizeof(Parameters) - offsetof(Parameters, open_deletion)) == 0;
}

bool scoring_less(const Parameters &a, const Parameters &b) {
    return std::memcmp(&a.open_deletion, &b.open_deletion,
        sizeof(Parameters) - offsetof(Parameters, open_deletion)) < 0;
}

uint64_t now_ns() {
    return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
}

// One worker's getScores8 call, waiting for its results.
struct Request {
    WgseBswJob *jobs;
    int32_t count;
    const uint8_t *ref, *qer;
    Parameters parameters;
    size_t ref_bytes, qer_bytes;
    uint64_t enqueued;
    bool done = false, ok = false;
};

// Requests aggregated into one command buffer: one dispatch per scoring.
struct Slot {
    enum Status { free, executing, completed } status = free;
    id<MTLBuffer> pairs = nil, reference = nil, query = nil;
    std::vector<Request *> requests;
    std::vector<WgseBswJob> inputs; // to commit again after an interruption
    std::vector<std::pair<int32_t, Parameters>> groups; // first pair, scoring
    id<MTLCommandBuffer> command = nil;
    int attempt = 0;
    std::string failure;
};

constexpr int32_t max_pairs = 1 << 18;
// Pairs worth a second command buffer while one runs; below it, they wait for the GPU.
constexpr int32_t target_pairs = 8192;

std::atomic<bool> enabled{false}, failed{false}, inject_failure{false};
std::mutex failure_mutex;
std::string failure_reason;
std::atomic<uint64_t> calls{0}, batches{0}, pairs_total{0}, gpu_ns{0}, wait_ns{0},
    cpu_fallbacks{0}, interruptions{0};

void fail(const std::string &reason) {
    std::lock_guard<std::mutex> lock(failure_mutex);
    if (failure_reason.empty()) failure_reason = reason;
    failed = true;
}

class Engine {
public:
    wgse::metal::Runtime runtime;
    id<MTLComputePipelineState> pipeline = nil;

    Engine() {
        if (runtime.device()) pipeline = runtime.compile(wgse_extension_source, "extend_batch");
    }
    ~Engine() {
        {
            std::lock_guard<std::mutex> lock(mutex);
            stopping = true;
            changed.notify_all();
        }
        if (submitter.joinable()) submitter.join();
    }
    std::string failure() const {
        return pipeline ? std::string()
            : runtime.failure().empty() ? "extension kernel unavailable" : runtime.failure();
    }
    void start() {
        std::lock_guard<std::mutex> lock(mutex);
        if (!submitter.joinable()) submitter = std::thread([this] { run(); });
    }

    bool score(Request &request) {
        std::unique_lock<std::mutex> lock(mutex);
        request.enqueued = now_ns();
        pending.push_back(&request);
        pending_pairs += request.count;
        changed.notify_one();
        done.wait(lock, [&request] { return request.done; });
        wait_ns += now_ns() - request.enqueued;
        return request.ok;
    }

private:
    std::mutex mutex;
    std::condition_variable changed, done;
    std::vector<Request *> pending;
    int64_t pending_pairs = 0;
    Slot slots[2];
    bool stopping = false;
    std::thread submitter;

    static bool reserve(id<MTLDevice> device, id<MTLBuffer> __strong &buffer, size_t bytes) {
        if (buffer && buffer.length >= bytes) return true;
        size_t capacity = 64 * 1024;
        while (capacity < bytes) capacity *= 2;
        buffer = [device newBufferWithLength:capacity options:MTLResourceStorageModeShared];
        return buffer != nil;
    }

    // Takes pending requests, up to max_pairs, ordered by scoring. Holds the lock.
    void take(Slot &slot) {
        slot.requests.clear();
        int32_t count = 0;
        size_t kept = 0;
        for (Request *request : pending) {
            if (count + request->count > max_pairs && count > 0) {
                pending[kept++] = request;
                continue;
            }
            slot.requests.push_back(request);
            count += request->count;
        }
        pending.resize(kept);
        pending_pairs -= count;
        std::stable_sort(slot.requests.begin(), slot.requests.end(),
            [](const Request *a, const Request *b) {
                return scoring_less(a->parameters, b->parameters);
            });
    }

    bool pack(Slot &slot) {
        size_t count = 0, ref_bytes = 0, qer_bytes = 0;
        for (const Request *request : slot.requests) {
            count += size_t(request->count);
            ref_bytes += request->ref_bytes;
            qer_bytes += request->qer_bytes;
        }
        id<MTLDevice> device = runtime.device();
        if (!reserve(device, slot.pairs, count * sizeof(WgseBswJob))
            || !reserve(device, slot.reference, ref_bytes)
            || !reserve(device, slot.query, qer_bytes)) {
            slot.failure = "Metal buffer allocation failed";
            return false;
        }
        auto *jobs = static_cast<WgseBswJob *>(slot.pairs.contents);
        auto *reference = static_cast<uint8_t *>(slot.reference.contents);
        auto *query = static_cast<uint8_t *>(slot.query.contents);
        slot.groups.clear();
        size_t index = 0, rbase = 0, qbase = 0;
        for (const Request *request : slot.requests) {
            if (slot.groups.empty() || !same_scoring(slot.groups.back().second, request->parameters))
                slot.groups.emplace_back(int32_t(index), request->parameters);
            for (int32_t i = 0; i < request->count; ++i) {
                WgseBswJob job = request->jobs[i];
                std::memcpy(reference + rbase, request->ref + job.idr, size_t(job.len1));
                std::memcpy(query + qbase, request->qer + job.idq, size_t(job.len2));
                job.idr = int32_t(rbase);
                job.idq = int32_t(qbase);
                rbase += size_t(job.len1);
                qbase += size_t(job.len2);
                jobs[index++] = job;
            }
        }
        slot.inputs.assign(jobs, jobs + count);
        return true;
    }

    void dispatch(Slot &slot) {
        ++slot.attempt;
        if (slot.attempt > 1)
            std::copy(slot.inputs.begin(), slot.inputs.end(),
                static_cast<WgseBswJob *>(slot.pairs.contents));
        slot.command = [runtime.queue() commandBufferWithUnretainedReferences];
        slot.command.label = @"extend";
        id<MTLComputeCommandEncoder> encoder = [slot.command computeCommandEncoder];
        if (!slot.command || !encoder) {
            slot.failure = "Metal command allocation failed";
            std::lock_guard<std::mutex> lock(mutex);
            slot.status = Slot::completed;
            return;
        }
        [encoder setComputePipelineState:pipeline];
        [encoder setBuffer:slot.reference offset:0 atIndex:1];
        [encoder setBuffer:slot.query offset:0 atIndex:2];
        const int32_t total = int32_t(slot.inputs.size());
        for (size_t g = 0; g < slot.groups.size(); ++g) {
            const int32_t first = slot.groups[g].first;
            const int32_t last = g + 1 < slot.groups.size() ? slot.groups[g + 1].first : total;
            Parameters parameters = slot.groups[g].second;
            parameters.count = last - first;
            [encoder setBuffer:slot.pairs offset:size_t(first) * sizeof(WgseBswJob) atIndex:0];
            [encoder setBytes:&parameters length:sizeof(parameters) atIndex:3];
            [encoder dispatchThreads:MTLSizeMake(NSUInteger(parameters.count) * 32, 1, 1)
                threadsPerThreadgroup:MTLSizeMake(128, 1, 1)];
        }
        [encoder endEncoding];
        Slot *finished = &slot;
        wgse::metal::Runtime::commit(slot.command, ^(id<MTLCommandBuffer>) {
            std::lock_guard<std::mutex> lock(mutex);
            finished->status = Slot::completed;
            changed.notify_one();
        }, @[slot.pairs, slot.reference, slot.query]);
    }

    // Stores the results, or commits the slot again after an interruption.
    void finish(Slot &slot) {
        if (slot.failure.empty() && slot.attempt < wgse::metal::Runtime::attempts
            && wgse::metal::Runtime::interrupted(slot.command, slot.attempt)) {
            ++interruptions;
            {
                std::lock_guard<std::mutex> lock(mutex);
                slot.status = Slot::executing;
            }
            dispatch(slot);
            return;
        }
        if (slot.failure.empty() && slot.command.status != MTLCommandBufferStatusCompleted)
            slot.failure = wgse::metal::Runtime::describe(slot.command);
        if (slot.failure.empty() && inject_failure)
            slot.failure = "injected GPU extension failure (test)";
        if (slot.failure.empty()) {
            gpu_ns += uint64_t((slot.command.GPUEndTime - slot.command.GPUStartTime) * 1e9);
            const auto *jobs = static_cast<const WgseBswJob *>(slot.pairs.contents);
            size_t index = 0;
            for (Request *request : slot.requests)
                for (int32_t i = 0; i < request->count; ++i, ++index) {
                    WgseBswJob &out = request->jobs[i];
                    const WgseBswJob &in = jobs[index];
                    out.score = in.score;
                    out.tle = in.tle;
                    out.gtle = in.gtle;
                    out.qle = in.qle;
                    out.gscore = in.gscore;
                    out.max_off = in.max_off;
                }
            ++batches;
            pairs_total += index;
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
        slot.command = nil;
        slot.attempt = 0;
        slot.failure.clear();
        slot.status = Slot::free;
    }

    void run() {
        pthread_set_qos_class_self_np(QOS_CLASS_USER_INITIATED, 0);
        std::unique_lock<std::mutex> lock(mutex);
        for (;;) {
            Slot *free = nullptr, *completed = nullptr;
            bool executing = false;
            for (Slot &slot : slots) {
                if (slot.status == Slot::free) free = &slot;
                if (slot.status == Slot::completed) completed = &slot;
                executing |= slot.status == Slot::executing;
            }
            if (completed) {
                lock.unlock();
                @autoreleasepool { finish(*completed); }
                lock.lock();
            } else if (free && !pending.empty() && (!executing || pending_pairs >= target_pairs)) {
                take(*free);
                free->status = Slot::executing;
                lock.unlock();
                @autoreleasepool {
                    if (pack(*free)) dispatch(*free);
                    else {
                        std::lock_guard<std::mutex> guard(mutex);
                        free->status = Slot::completed;
                    }
                }
                lock.lock();
            } else if (stopping && pending.empty() && !executing) {
                return;
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

} // namespace

bool wgse_mem3_bsw_available(std::string &reason) {
    Engine &e = engine();
    reason = e.failure();
    if (!reason.empty()) return false;
    // Two slots of max_pairs pairs, each with its query and a reference of a few times that.
    const uint64_t needed = 2ull * max_pairs * (sizeof(WgseBswJob) + 4 * wgse_mem3_max_gpu_query_length);
    id<MTLDevice> device = e.runtime.device();
    if (device.maxBufferLength < needed / 2 || device.recommendedMaxWorkingSetSize < needed) {
        reason = "the GPU's buffer limits are below the " + std::to_string(needed >> 20)
            + " MiB seed extension needs";
        return false;
    }
    return true;
}

WgseMem3MetalLimits wgse_mem3_metal_limits() {
    WgseMem3MetalLimits limits;
    id<MTLDevice> device = engine().runtime.device();
    if (!device) return limits;
    limits.device = device.name.UTF8String;
    limits.max_buffer_bytes = device.maxBufferLength;
    limits.recommended_working_set_bytes = device.recommendedMaxWorkingSetSize;
    return limits;
}

namespace {
std::atomic<uint64_t> physical_memory_override{0};
}

void wgse_mem3_set_physical_memory(uint64_t bytes) { physical_memory_override = bytes; }

uint64_t wgse_mem3_physical_memory() {
    if (physical_memory_override) return physical_memory_override;
    uint64_t bytes = 0;
    size_t size = sizeof(bytes);
    return sysctlbyname("hw.memsize", &bytes, &size, nullptr, 0) == 0 ? bytes : 0;
}

void wgse_mem3_memory_preflight(const char *prefix, int64_t chunk_bases) {
    // The index files bwa-mem3 loads, 1 GiB, and 24 bytes per base of a chunk, as for
    // wgse-bwa-mem2's CPU backend: 11.3 GiB for hs38 with -K 10000000, which peaked at
    // 10.7 GiB (10.9 GiB with --metal).
    uint64_t need = (uint64_t(1) << 30) + uint64_t(std::max<int64_t>(0, chunk_bases)) * 24;
    for (const char *suffix : {".bwt.2bit.64", ".pac"}) {
        std::error_code error;
        const uintmax_t size = std::filesystem::file_size(std::string(prefix) + suffix, error);
        if (!error) need += size;
    }
    const uint64_t have = wgse_mem3_physical_memory();
    if (have && have < need)
        fprintf(stderr, "[wgse-bwa-mem3] warning: this run needs about %.1f GiB of memory but "
            "this Mac has %.1f GiB; expect heavy paging and a much slower run\n",
            double(need) / double(1ull << 30), double(have) / double(1ull << 30));
}

bool wgse_mem3_bsw_start(std::string &reason) {
    if (!wgse_mem3_bsw_available(reason)) return false;
    Engine &e = engine();
    e.start();
    enabled = true;
    return true;
}

void wgse_mem3_bsw_decline() {
    ++calls;
    ++cpu_fallbacks;
}

void wgse_mem3_bsw_inject_failure() {
    inject_failure = true;
}

int wgse_mem3_bsw_finish() {
    if (!enabled) return 0;
    fprintf(stderr, "[wgse-bwa-mem3] extension_calls=%llu extension_cpu_calls=%llu "
        "extension_batches=%llu extension_pairs=%llu extension_gpu_s=%.2f extension_wait_s=%.2f "
        "extension_interruptions=%llu\n",
        (unsigned long long)calls.load(), (unsigned long long)cpu_fallbacks.load(),
        (unsigned long long)batches.load(), (unsigned long long)pairs_total.load(),
        gpu_ns.load() * 1e-9, wait_ns.load() * 1e-9, (unsigned long long)interruptions.load());
    if (!failed) return 0;
    std::lock_guard<std::mutex> lock(failure_mutex);
    fprintf(stderr, "[wgse-bwa-mem3] CPU fallback: GPU seed extension failed (%s); "
        "the CPU scored the rest\n", failure_reason.c_str());
    return 1;
}

namespace {
int thread_percent = wgse_mem3_default_thread_percent;
}

void wgse_mem3_bsw_set_threads(int percent) {
    thread_percent = percent;
}

int wgse_mem3_compute_threads(int threads) {
    if (!enabled) return threads;
    const int result = std::min(256, std::max(threads, (threads * thread_percent + 50) / 100));
    fprintf(stderr, "[wgse-bwa-mem3] --metal: %d compute threads for -t %d\n", result, threads);
    return result;
}

bool wgse_mem3_bsw_enabled() {
    return enabled && !failed;
}

bool wgse_mem3_bsw_score(WgseBswJob *jobs, int32_t count, const uint8_t *ref,
    const uint8_t *qer, const WgseBswScoring &scoring) {
    ++calls;
    if (count > max_pairs || scoring.extend_deletion <= 0 || scoring.extend_insertion <= 0) {
        ++cpu_fallbacks;
        return false;
    }
    Request request{jobs, count, ref, qer, {}, 0, 0, 0};
    Parameters &p = request.parameters;
    p.open_deletion = scoring.open_deletion;
    p.extend_deletion = scoring.extend_deletion;
    p.open_insertion = scoring.open_insertion;
    p.extend_insertion = scoring.extend_insertion;
    p.zdrop = scoring.zdrop;
    p.end_bonus = scoring.end_bonus;
    p.simple_matrix = true;
    p.precision = 8;
    const int8_t *m = scoring.matrix;
    for (int i = 0; i < 25; ++i) {
        p.matrix[i] = m[i];
        p.max_substitution = std::max<int32_t>(p.max_substitution, m[i]);
        const int r = i / 5, c = i % 5;
        p.simple_matrix &= m[i] == (r == 4 || c == 4 ? m[24] : r == c ? m[0] : m[1]);
    }
    for (int32_t i = 0; i < count; ++i) {
        request.ref_bytes += size_t(jobs[i].len1);
        request.qer_bytes += size_t(jobs[i].len2);
    }
    if (!engine().score(request)) {
        ++cpu_fallbacks;
        return false;
    }
    return true;
}
