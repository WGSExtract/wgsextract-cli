#pragma once

// The GPU operations wgse-bwa's scheduling (backend.cpp) asks of a device. Each
// device (Metal, CUDA) runs the same exact kernels; the scheduling, the CPU's
// share of the work and the matching of GPU results to upstream's calls are
// shared. Every operation throws std::runtime_error when the GPU fails; the
// caller then finishes the work on the CPU.

#include "extend_types.h"
#include "rescue_parameters.h"
#include "rescue_types.h"
#include "seeding_types.h"

#include "bwamem.h"

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace wgse::gpu {
using Clock = std::chrono::steady_clock;
using Intervals = std::vector<bwtintv_t>;
static_assert(sizeof(SeedInterval) == sizeof(bwtintv_t));
static_assert(offsetof(SeedInterval, info) == offsetof(bwtintv_t, info));
static_assert(sizeof(SeedParameters) == 80 && sizeof(SeedRead) == 8);
static_assert(sizeof(LocateParameters) == 88);
static_assert(sizeof(RescueParameters) == 40 && sizeof(RescueJob) == 24);
static_assert(sizeof(RescueResult) == 32);
static_assert(sizeof(ExtendParameters) == 52 && sizeof(ExtendJob) == 36);
static_assert(sizeof(ExtendResult) == 112);

inline double seconds(Clock::time_point start) {
    return std::chrono::duration<double>(Clock::now() - start).count();
}

// Reads packed as mem_align1_core encodes them (2-bit codes, 4 for N).
struct Reads {
    std::vector<uint8_t> bases;
    std::vector<SeedRead> reads;
};

// The suffix-array rows mem_chain() passes to bwt_sa() for these intervals, in
// its order: at most max_occ evenly spaced rows per interval. The casts spell
// out C's implicit conversions so any wrap-around behaves identically.
// At most the number of rows append_rows() adds for these intervals (equal
// unless an interval's step wraps around in upstream's int).
inline size_t row_count(const bwtintv_t *begin, const bwtintv_t *end, int max_occ) {
    size_t total = 0;
    for (const bwtintv_t *p = begin; p != end; ++p)
        total += p->x[2] > static_cast<uint64_t>(max_occ) ? static_cast<size_t>(max_occ)
            : static_cast<size_t>(p->x[2]);
    return total;
}

inline size_t row_count(const Intervals &intervals, int max_occ) {
    return row_count(intervals.data(), intervals.data() + intervals.size(), max_occ);
}

inline void append_rows(const bwtintv_t *begin, const bwtintv_t *end, int max_occ,
    std::vector<uint64_t> &rows) {
    for (const bwtintv_t *p = begin; p != end; ++p) {
        int step, count;
        int64_t k;
        const uint64_t limit = static_cast<uint64_t>(max_occ);
        step = p->x[2] > limit ? static_cast<int>(p->x[2] / limit) : 1;
        for (k = count = 0; static_cast<uint64_t>(k) < p->x[2] && count < max_occ;
            k += step, ++count)
            rows.push_back(p->x[0] + static_cast<uint64_t>(k));
    }
}

inline void append_rows(const Intervals &intervals, int max_occ, std::vector<uint64_t> &rows) {
    append_rows(intervals.data(), intervals.data() + intervals.size(), max_occ, rows);
}


// The longest query of each seed-extension kernel class; kernels are
// instantiated per class so registers fit the class's longest query.
constexpr uint32_t extend_class_caps[] = {16, 32, 48, 64, 80, 96, 112, 128, extend_max_query};
constexpr size_t extend_classes = sizeof(extend_class_caps) / sizeof(extend_class_caps[0]);
static_assert(extend_class_caps[extend_classes - 1] == extend_max_query);

// Job indices with a side to extend, longest query first (a counting sort), so
// that the jobs sharing a SIMD group take similar times. Returns the count.
inline size_t order_side(const ExtendJob *jobs, size_t count, bool right, uint32_t *order) {
    size_t start[extend_max_query + 2] = {};
    auto length = [&](size_t i) { return right ? jobs[i].right_qlen : jobs[i].left_qlen; };
    for (size_t i = 0; i < count; ++i)
        if (length(i)) ++start[extend_max_query + 1 - length(i)];
    size_t total = 0;
    for (size_t k = 0; k < extend_max_query + 2; ++k) {
        const size_t n = start[k];
        start[k] = total;
        total += n;
    }
    for (size_t i = 0; i < count; ++i)
        if (length(i)) order[start[extend_max_query + 1 - length(i)]++] = uint32_t(i);
    return total;
}

// Calls fn(class index, first, last) for each run of one side's ordered jobs
// (descending query length) that falls into one kernel class.
template <class F>
void for_each_class(const ExtendJob *jobs, const uint32_t *order, size_t count, bool right, F fn) {
    auto length = [&](size_t slot) {
        const ExtendJob &j = jobs[order[slot]];
        return right ? j.right_qlen : j.left_qlen;
    };
    size_t low = 0;
    for (size_t k = extend_classes; k-- > 0 && low < count;) {
        const uint32_t below = k ? extend_class_caps[k - 1] : 0;
        size_t high = low;
        while (high < count && length(high) > below) ++high;
        if (high == low) continue;
        fn(k, low, high);
        low = high;
    }
}

// The extension kernel's parameters for opt's scoring, or false if ksw_extend2
// would score differently than it models: a bwa_fill_scmat-style matrix with a
// positive maximum, positive gap extensions and sizes that keep its arithmetic
// in range.
inline bool extend_parameters(const mem_opt_t &opt, ExtendParameters &p) {
    const int8_t *mat = opt.mat;
    p = {};
    p.match = mat[0];
    p.mismatch = mat[1];
    p.ambiguous = mat[4];
    int highest = 0;
    for (int i = 0; i < 5; ++i)
        for (int j = 0; j < 5; ++j) {
            const int value = mat[i * 5 + j];
            const int expected = i == 4 || j == 4 ? p.ambiguous : i == j ? p.match : p.mismatch;
            if (value != expected) return false;
            highest = value > highest ? value : highest;
        }
    p.max = highest;
    p.o_del = opt.o_del;
    p.e_del = opt.e_del;
    p.o_ins = opt.o_ins;
    p.e_ins = opt.e_ins;
    p.zdrop = opt.zdrop;
    p.w = opt.w;
    p.left_bonus = opt.pen_clip5;
    p.right_bonus = opt.pen_clip3;
    constexpr int limit = 1 << 16;
    auto small = [](int value) { return value > -limit && value < limit; };
    const bool gaps = opt.o_del >= 0 && opt.o_ins >= 0 && opt.e_del > 0 && opt.e_ins > 0
        && opt.o_del < limit && opt.o_ins < limit && opt.e_del < limit && opt.e_ins < limit;
    const bool bounded = opt.w > 0 && opt.w < limit && small(opt.zdrop) && small(opt.pen_clip5)
        && small(opt.pen_clip3);
    // Gap penalties must fit the kernel's 16-bit cells.
    const bool narrow = opt.o_del + opt.e_del <= INT16_MAX
        && opt.o_ins + int(extend_max_query) * opt.e_ins <= INT16_MAX;
    return highest > 0 && gaps && bounded && narrow;
}

// Host-visible buffers of one seed-extension batch. The scheduler fills bases
// and jobs; results are valid after the batch completes.
struct ExtendBuffers {
    uint8_t *bases = nullptr;
    ExtendJob *jobs = nullptr;
    ExtendResult *results = nullptr;
    size_t base_capacity = 0, job_capacity = 0;
    virtual ~ExtendBuffers() = default;
};

enum class Outcome {
    done,
    // The OS stopped the work to keep the display responsive (Metal); running
    // it again is safe, as the kernels rewrite their outputs from unchanged inputs.
    interrupted,
    failed
};

class Device {
public:
    virtual ~Device() = default;
    // "Metal", "CUDA" or "Vulkan", for messages.
    virtual const char *name() const = 0;
    // Whether the device seeds and locates (attach, seed, attach_sa, locate,
    // densify) and rescues mates. A device that does neither only extends
    // seeds; the CPU then seeds, locates and rescues as its backend does.
    virtual bool seeds() const { return true; }
    virtual bool rescues() const { return true; }

    // Seeding: reads per seed() batch.
    virtual size_t seed_capacity() const = 0;
    // Makes the BWT (allocated by wgse_metal_calloc) and opt's seeding
    // parameters available to seed(). Repeated calls with the same index only
    // update the parameters.
    virtual void attach(const bwt_t &bwt, const mem_opt_t &opt) = 0;
    // mem_collect_intv's intervals for every read; reads the kernel cannot seed
    // exactly are listed in `failed` with empty intervals.
    virtual void seed(const Reads &input, std::vector<Intervals> &out,
        std::vector<long> &failed) = 0;

    // Suffix-array locate (bwt_sa). False if the device cannot locate with this
    // index's samples (the CPU then locates).
    virtual bool attach_sa(const bwt_t &bwt) = 0;
    virtual void locate(const std::vector<uint64_t> &rows, std::vector<uint64_t> &positions) = 0;
    // Builds samples of every (1 << shift)th row the index lacks, so walks are
    // shorter; *low and *high then point at host copies the CPU's bwt_sa uses too
    // (see sampled_position in backend.cpp). False if unsupported or out of memory.
    virtual bool densify(unsigned shift, size_t slice, const uint32_t **low,
        const uint32_t **high) = 0;

    // Mate rescue: results[i] is ksw_align2's result for jobs[i] unless its
    // status is rescue_cpu. Synchronous.
    virtual void rescue(const RescueParameters &p, const std::vector<uint8_t> &bases,
        const std::vector<RescueJob> &jobs, size_t row_bytes,
        std::vector<RescueResult> &results) = 0;
    virtual double rescue_kernel_seconds() const = 0;

    // Seed extension: buffers for up to `jobs` jobs and `bases` bases.
    virtual std::unique_ptr<ExtendBuffers> extend_buffers(size_t bases, size_t jobs) = 0;
    // Runs the left then the right extensions of buffers.jobs[0, count) without
    // waiting. `done` runs once, on another thread, with the outcome, the
    // kernel seconds and an error message for failures. `attempt` counts this
    // batch's submissions, from 1.
    using ExtendDone = std::function<void(Outcome, double, const std::string &)>;
    virtual void extend(const ExtendParameters &p, ExtendBuffers &buffers, size_t count,
        int attempt, ExtendDone done) = 0;

    // Work the OS interrupted and that was run again (Metal).
    virtual size_t interruptions() const { return 0; }
    // Submissions of one piece of work, including those after interruptions.
    static constexpr int attempts = 4;
};

// Each returns null and a reason when its GPU is unusable.
struct DeviceOptions {
    // Reads per seeding batch and rows per locate dispatch.
    size_t seed_batch = 16384, locate_rows = size_t(1) << 21;
    // Test hook (Metal): report the first submission of every nth piece of work
    // as interrupted even if it completed, so its kernels run twice.
    size_t interrupt_every = 0;
    // The watchdog's floor: a wait for the GPU fails after the larger of this
    // and 20 times the slowest batch of its stage (CUDA and Vulkan).
    double watchdog_seconds = 60;
    // Test hook (CUDA): occupy the GPU before the first rescue batch for twice
    // the watchdog's limit, as a hung kernel would.
    bool inject_hang = false;
};
std::unique_ptr<Device> make_metal_device(const DeviceOptions &options, std::string &reason);
std::unique_ptr<Device> make_cuda_device(const DeviceOptions &options, std::string &reason);
std::unique_ptr<Device> make_vulkan_device(const DeviceOptions &options, std::string &reason);
// Whether the machine has the device at all (capabilities' *_hardware_supported).
bool metal_hardware_supported();
bool cuda_hardware_supported();
bool vulkan_hardware_supported();
} // namespace wgse::gpu
