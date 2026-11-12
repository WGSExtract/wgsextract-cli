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

// Threads of a group of pairs, whose bases extension.metal reads as interleaved words.
constexpr int32_t lanes = 32;

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

// One worker's getScores8 call, packed for extension.metal, waiting for its results.
struct Request {
    WgseBswJob *jobs;
    int32_t count;
    Parameters parameters;
    // The pairs sorted by length (seqid: the index in jobs), with offsets relative to the
    // request's words and scratch.
    const WgseBswJob *packed;
    const uint32_t *ref_words, *qer_words;
    size_t ref_word_count, qer_word_count, scratch_cells;
    // packed's last long_count pairs go to extend_long.
    int32_t long_count;
    uint64_t enqueued;
    bool done = false, ok = false;
};

// Requests aggregated into one command buffer: one dispatch per scoring.
struct Slot {
    enum Status { free, executing, completed } status = free;
    id<MTLBuffer> pairs = nil, reference = nil, query = nil, scratch = nil;
    std::vector<Request *> requests;
    std::vector<WgseBswJob> inputs; // to commit again after an interruption
    // Each pair's result, in the slot's order.
    std::vector<WgseBswJob *> targets;
    struct Group {
        int32_t first, count;
        bool long_pairs;
        Parameters parameters;
    };
    std::vector<Group> groups;
    id<MTLCommandBuffer> command = nil;
    int attempt = 0;
    std::string failure;
};

constexpr int32_t max_pairs = 1 << 18;
// A command buffer's DP state; a request with more is scored on the CPU.
constexpr size_t max_scratch_cells = size_t(16) << 20;
// Pairs worth a second command buffer while one runs; below it, they wait for the GPU.
constexpr int32_t target_pairs = 8192;
// Command buffers in flight: each holds too few pairs to fill the GPU, so several run at once.
constexpr int slot_count = 2;
// Band cells above which a pair is scored by a SIMD group (extend_long), not one thread.
constexpr int64_t long_cells = 30000;

std::atomic<bool> enabled{false}, failed{false}, inject_failure{false};
std::mutex failure_mutex;
std::string failure_reason;
// backlog_calls: getScores8 calls scored on the CPU because the GPU's queue was over --gpu-backlog.
std::atomic<uint64_t> calls{0}, batches{0}, pairs_total{0}, gpu_ns{0}, wait_ns{0},
    cpu_fallbacks{0}, interruptions{0}, backlog_calls{0}, gpu_busy_ns{0};
double busy_end = 0;
// Pairs queued for the GPU but not yet in a command buffer; above backlog, workers score
// their own pairs on the CPU (0: never).
std::atomic<int64_t> queued_pairs{0};
int64_t backlog = 0;

void fail(const std::string &reason) {
    std::lock_guard<std::mutex> lock(failure_mutex);
    if (failure_reason.empty()) failure_reason = reason;
    failed = true;
}

class Engine {
public:
    wgse::metal::Runtime runtime;
    id<MTLComputePipelineState> pipeline = nil, long_pipeline = nil;

    Engine() {
        id<MTLDevice> device = runtime.device();
        id<MTLLibrary> library = device ? runtime.library(wgse_extension_source) : nil;
        id<MTLFunction> function = [library newFunctionWithName:@"extend_striped"];
        if (!function) return;
        // One SIMD group per threadgroup leaves the compiler the most registers per thread.
        MTLComputePipelineDescriptor *descriptor = [[MTLComputePipelineDescriptor alloc] init];
        descriptor.computeFunction = function;
        descriptor.maxTotalThreadsPerThreadgroup = lanes;
        descriptor.threadGroupSizeIsMultipleOfThreadExecutionWidth = YES;
        NSError *error = nil;
        pipeline = [device newComputePipelineStateWithDescriptor:descriptor
            options:MTLPipelineOptionNone reflection:nil error:&error];
        if (pipeline && pipeline.threadExecutionWidth != NSUInteger(lanes)) pipeline = nil;
        id<MTLFunction> long_function = [library newFunctionWithName:@"extend_long"];
        if (pipeline && long_function) {
            long_pipeline = [device newComputePipelineStateWithFunction:long_function error:&error];
            if (long_pipeline && (long_pipeline.threadExecutionWidth != NSUInteger(lanes)
                || long_pipeline.maxTotalThreadsPerThreadgroup < 4 * NSUInteger(lanes)))
                long_pipeline = nil;
        }
        if (!long_pipeline) pipeline = nil;
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
        queued_pairs = pending_pairs;
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
    Slot slots[slot_count];
    bool stopping = false;
    std::thread submitter;

    static bool reserve(id<MTLDevice> device, id<MTLBuffer> __strong &buffer, size_t bytes,
        MTLResourceOptions options = MTLResourceStorageModeShared) {
        if (buffer && buffer.length >= bytes) return true;
        size_t capacity = 64 * 1024;
        while (capacity < bytes) capacity *= 2;
        buffer = [device newBufferWithLength:capacity options:options];
        return buffer != nil;
    }

    // Takes pending requests, up to max_pairs and max_scratch_cells, ordered by scoring.
    // Holds the lock.
    void take(Slot &slot) {
        slot.requests.clear();
        int32_t count = 0;
        size_t cells = 0;
        size_t kept = 0;
        for (Request *request : pending) {
            if (count > 0 && (count + request->count > max_pairs
                || cells + request->scratch_cells > max_scratch_cells)) {
                pending[kept++] = request;
                continue;
            }
            slot.requests.push_back(request);
            count += request->count;
            cells += request->scratch_cells;
        }
        pending.resize(kept);
        pending_pairs -= count;
        queued_pairs = pending_pairs;
        std::stable_sort(slot.requests.begin(), slot.requests.end(),
            [](const Request *a, const Request *b) {
                return scoring_less(a->parameters, b->parameters);
            });
    }

    // Concatenates the slot's packed requests, rebasing their offsets.
    bool pack(Slot &slot) {
        // Once the watchdog gives up, an abandoned command may still use this
        // slot's buffers: fail the requests to the CPU without packing.
        slot.failure = wgse::metal::Runtime::hang();
        if (!slot.failure.empty()) return false;
        size_t count = 0, ref_words = 0, qer_words = 0, cells = 0;
        for (const Request *request : slot.requests) {
            count += size_t(request->count);
            ref_words += request->ref_word_count;
            qer_words += request->qer_word_count;
            cells += request->scratch_cells;
        }
        id<MTLDevice> device = runtime.device();
        if (!reserve(device, slot.pairs, count * sizeof(WgseBswJob))
            || !reserve(device, slot.reference, ref_words * sizeof(uint32_t))
            || !reserve(device, slot.query, qer_words * sizeof(uint32_t))
            || !reserve(device, slot.scratch, cells * sizeof(uint32_t), MTLResourceStorageModePrivate)) {
            slot.failure = "Metal buffer allocation failed";
            return false;
        }
        auto *jobs = static_cast<WgseBswJob *>(slot.pairs.contents);
        auto *reference = static_cast<uint32_t *>(slot.reference.contents);
        auto *query = static_cast<uint32_t *>(slot.query.contents);
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
        // The dispatches write disjoint pairs and DP state, so they may run at once.
        id<MTLComputeCommandEncoder> encoder = [slot.command
            computeCommandEncoderWithDispatchType:MTLDispatchTypeConcurrent];
        if (!slot.command || !encoder) {
            slot.failure = "Metal command allocation failed";
            std::lock_guard<std::mutex> lock(mutex);
            slot.status = Slot::completed;
            return;
        }
        [encoder setBuffer:slot.reference offset:0 atIndex:1];
        [encoder setBuffer:slot.query offset:0 atIndex:2];
        [encoder setBuffer:slot.scratch offset:0 atIndex:4];
        for (const Slot::Group &group : slot.groups) {
            Parameters parameters = group.parameters;
            parameters.count = group.count;
            [encoder setComputePipelineState:group.long_pairs ? long_pipeline : pipeline];
            [encoder setBuffer:slot.pairs offset:size_t(group.first) * sizeof(WgseBswJob) atIndex:0];
            [encoder setBytes:&parameters length:sizeof(parameters) atIndex:3];
            // Whole SIMD groups: a pair per thread, or per SIMD group for long pairs; the
            // kernels skip the padding threads.
            const NSUInteger threads = group.long_pairs ? NSUInteger(group.count) * lanes
                : NSUInteger(group.count + lanes - 1) / lanes * lanes;
            const NSUInteger per_group = group.long_pairs ? 4 * lanes : lanes;
            [encoder dispatchThreads:MTLSizeMake((threads + per_group - 1) / per_group * per_group, 1, 1)
                threadsPerThreadgroup:MTLSizeMake(per_group, 1, 1)];
        }
        [encoder endEncoding];
        Slot *finished = &slot;
        wgse::metal::Runtime::commit(slot.command, ^(id<MTLCommandBuffer>) {
            std::lock_guard<std::mutex> lock(mutex);
            finished->status = Slot::completed;
            changed.notify_one();
        }, @[slot.pairs, slot.reference, slot.query, slot.scratch]);
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
            {
                // Busy time: the union of the command buffers' GPU intervals (finish runs on
                // the submitter thread only, in completion order).
                const double start = std::max(slot.command.GPUStartTime, busy_end);
                if (slot.command.GPUEndTime > start) gpu_busy_ns += uint64_t((slot.command.GPUEndTime - start) * 1e9);
                busy_end = std::max(busy_end, slot.command.GPUEndTime);
            }
            const auto *jobs = static_cast<const WgseBswJob *>(slot.pairs.contents);
            const size_t index = slot.targets.size();
            for (size_t i = 0; i < index; ++i) {
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
    // slot_count slots of max_pairs pairs, each with its query (128 bytes for 256 bases, 8 a
    // 32-bit word) and its reference (allowing three times that), and their DP state.
    const uint64_t needed = uint64_t(slot_count) * (max_pairs * (sizeof(WgseBswJob) + 2 * wgse_mem3_max_gpu_query_length)
        + max_scratch_cells * sizeof(uint32_t));
    id<MTLDevice> device = e.runtime.device();
    if (device.maxBufferLength < needed / slot_count || device.recommendedMaxWorkingSetSize < needed) {
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
        "extension_batches=%llu extension_pairs=%llu extension_gpu_s=%.2f extension_gpu_busy_s=%.2f extension_wait_s=%.2f "
        "extension_interruptions=%llu extension_backlog_calls=%llu\n",
        (unsigned long long)calls.load(), (unsigned long long)cpu_fallbacks.load(),
        (unsigned long long)batches.load(), (unsigned long long)pairs_total.load(),
        gpu_ns.load() * 1e-9, gpu_busy_ns.load() * 1e-9, wait_ns.load() * 1e-9, (unsigned long long)interruptions.load(),
        (unsigned long long)backlog_calls.load());
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

bool wgse_mem3_bsw_busy() {
    if (backlog <= 0 || queued_pairs.load(std::memory_order_relaxed) < backlog) return false;
    ++backlog_calls;
    return true;
}

void wgse_mem3_bsw_set_backlog(int64_t pairs) {
    backlog = pairs;
}

namespace {

// Eight bases (each at most 4) as the 4-bit fields of a word, the first base lowest.
uint32_t pack_bases(const uint8_t *bases, int32_t length) {
    if (length >= 8) {
        uint64_t x;
        std::memcpy(&x, bases, sizeof(x));
        x = (x | x >> 4) & 0x00ff00ff00ff00ffull;
        x = (x | x >> 8) & 0x0000ffff0000ffffull;
        return uint32_t(x | x >> 16);
    }
    uint32_t word = 0;
    for (int32_t k = 0; k < length; ++k) word |= uint32_t(bases[k]) << (k * 4);
    return word;
}

// Sorts the pairs by length into groups of `lanes`, and packs each group's bases as
// interleaved words, one column per lane, with its DP state columns after the last group's.
void pack(Request &request, const WgseBswJob *jobs, int32_t count, const uint8_t *ref,
    const uint8_t *qer) {
    thread_local std::vector<int32_t> order;
    thread_local std::vector<WgseBswJob> packed;
    thread_local std::vector<uint32_t> ref_words, qer_words;
    order.resize(size_t(count));
    for (int32_t i = 0; i < count; ++i) order[size_t(i)] = i;
    const auto is_long = [jobs](int32_t i) {
        return int64_t(jobs[i].len1) * std::min(jobs[i].len2, 2 * jobs[i].bandwidth + 1) > long_cells;
    };
    const auto split = std::stable_partition(order.begin(), order.end(),
        [&is_long](int32_t i) { return !is_long(i); });
    const int32_t short_count = int32_t(split - order.begin());
    const auto shorter = [jobs](int32_t a, int32_t b) {
        const int32_t qa = (jobs[a].len2 + 7) / 8, qb = (jobs[b].len2 + 7) / 8;
        return qa != qb ? qa < qb : jobs[a].len1 < jobs[b].len1;
    };
    std::stable_sort(order.begin(), split, shorter);
    packed.resize(size_t(count));
    ref_words.clear();
    qer_words.clear();
    size_t cells = 0;
    // Groups of one-thread pairs, then of long pairs.
    for (int32_t first = 0, last; first < count; first = last) {
        const bool long_group = first >= short_count;
        last = std::min(long_group ? count : short_count, first + lanes);
        int32_t rmax = 0, qmax = 0;
        for (int32_t i = first; i < last; ++i) {
            rmax = std::max(rmax, (jobs[order[size_t(i)]].len1 + 7) / 8);
            qmax = std::max(qmax, (jobs[order[size_t(i)]].len2 + 7) / 8);
        }
        const size_t rbase = ref_words.size(), qbase = qer_words.size();
        ref_words.resize(rbase + size_t(rmax) * lanes);
        qer_words.resize(qbase + size_t(qmax) * lanes);
        for (int32_t i = first; i < last; ++i) {
            const int32_t lane = i - first, original = order[size_t(i)];
            WgseBswJob job = jobs[original];
            const uint8_t *r = ref + job.idr, *q = qer + job.idq;
            for (int32_t w = 0; w < (job.len1 + 7) / 8; ++w)
                ref_words[rbase + size_t(lane) + size_t(w) * lanes] = pack_bases(r + 8 * w, job.len1 - 8 * w);
            for (int32_t w = 0; w < (job.len2 + 7) / 8; ++w)
                qer_words[qbase + size_t(lane) + size_t(w) * lanes] = pack_bases(q + 8 * w, job.len2 - 8 * w);
            job.idr = int32_t(rbase) + lane;
            job.idq = int32_t(qbase) + lane;
            job.id = int32_t(cells) + lane;
            job.seqid = original;
            packed[size_t(i)] = job;
        }
        // A lane's DP state: one cell per query base, and one past the last word's. Long
        // pairs keep theirs in threadgroup memory.
        if (!long_group) cells += size_t(qmax * 8 + 1) * lanes;
    }
    request.long_count = count - short_count;
    request.packed = packed.data();
    request.ref_words = ref_words.data();
    request.qer_words = qer_words.data();
    request.ref_word_count = ref_words.size();
    request.qer_word_count = qer_words.size();
    request.scratch_cells = cells;
}

} // namespace

bool wgse_mem3_bsw_score(WgseBswJob *jobs, int32_t count, const uint8_t *ref,
    const uint8_t *qer, const WgseBswScoring &scoring) {
    ++calls;
    if (count > max_pairs || scoring.extend_deletion <= 0 || scoring.extend_insertion <= 0) {
        ++cpu_fallbacks;
        return false;
    }
    Request request{jobs, count, {}, nullptr, nullptr, nullptr, 0, 0, 0, 0, 0};
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
    pack(request, jobs, count, ref, qer);
    if (request.scratch_cells > max_scratch_cells || !engine().score(request)) {
        ++cpu_fallbacks;
        return false;
    }
    return true;
}
