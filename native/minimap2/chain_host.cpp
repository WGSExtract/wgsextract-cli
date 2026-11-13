// The chaining offload (chain_gpu.h) on a chain_engine.h device. A read's fiber splits
// its anchors into runs that fill independently (see split), fills single anchors and
// overlong runs itself and queues the rest; dispatcher threads batch them for the
// device, and mapping threads with no read to run fill queued reads on the CPU.
#include "chain_engine.h"
#include "chain_gpu.h"
#include "fiber.h"

extern "C" {
#include "mmpriv.h"
}

#include <algorithm>
#include <atomic>
#include <chrono>
#include <climits>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

using namespace wgse::chain;

namespace {

double now() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
uint64_t nanoseconds(double seconds) { return (uint64_t)(std::max(0.0, seconds) * 1e9); }

// A read's chaining job: its anchors, mg_lchain_dp's output arrays, and the runs
// queued for the GPU (packed, with each run's first anchor in a[]).
struct Job {
    const mm128_t *a = nullptr;
    int32_t *f = nullptr, *v = nullptr;
    int64_t *p = nullptr;
    wgse::fiber::Read *read = nullptr;
    Packed packed;
    enum Outcome { queued, gpu, cpu, failed } outcome = queued;
};

// The queued runs on the CPU, as when the GPU fails or a thread helps.
void fill_queued_cpu(const Params &params, Job &job, std::vector<int32_t> &t) {
    const Packed &q = job.packed;
    for (size_t k = 0; k < q.runs.size(); ++k)
        fill_cpu(params, job.a, q.starts[k], q.runs[k].n, job.f, job.p, job.v, t);
}

class Engine {
public:
    bool start(std::string &reason);
    bool enabled() const { return enabled_.load(std::memory_order_relaxed); }
    bool accepts(const Params &params);
    Job *acquire();
    void release(Job *job);
    void submit(Job *job);
    bool help(bool tail);
    void print_metrics() const;
    bool failed(std::string &reason) const;

    uint32_t max_gpu_run = 16384;
    size_t max_job_anchors() const { return max_anchors_; }
    std::atomic<size_t> jobs{0}, alone{0}, gpu_runs{0}, gpu_anchors{0}, cpu_runs{0}, cpu_anchors{0},
        unsupported{0};
    Params params{};

private:
    void dispatcher(int slot);
    bool ready(double now_s) const;
    void run_batch(std::vector<Job *> &batch, int slot);
    void fail(const std::string &reason);

    std::unique_ptr<Device> device_;
    std::atomic<bool> enabled_{false};
    static constexpr int dispatchers = 2;
    size_t max_anchors_ = (size_t)8 << 20, target_anchors_ = (size_t)1 << 20;
    double idle_delay_ = 100e-6, busy_delay_ = 2e-3;

    mutable std::mutex mutex_;
    std::condition_variable work_cv_;
    std::deque<Job *> queue_;
    std::vector<Job *> pool_;
    bool params_set_ = false;
    size_t queued_anchors_ = 0;
    double oldest_ = 0, busy_since_ = 0;
    int in_flight_ = 0;
    std::string failure_;
    std::atomic<size_t> batches_{0}, helped_{0};
    std::atomic<uint64_t> gpu_ns_{0}, busy_ns_{0};
};

Engine *engine = nullptr;
int tune_batch = 0, tune_max_run = 0, tune_fail = 0;
bool fail_batches = false;

bool Engine::start(std::string &reason) {
    if (tune_batch > 0) target_anchors_ = (size_t)tune_batch;
    if (tune_max_run > 0) max_gpu_run = (uint32_t)tune_max_run;
    max_anchors_ = std::max(max_anchors_, target_anchors_);
    fail_batches = tune_fail != 0;
    device_ = make_device();
    if (!device_) {
        reason = "this GPU backend has no chaining kernels";
        return false;
    }
    // Every queued run has at least two anchors.
    if (!device_->start(dispatchers, max_anchors_, max_anchors_ / 2, reason)) return false;
    for (int k = 0; k < dispatchers; ++k) std::thread([this, k] { dispatcher(k); }).detach();
    enabled_ = true;
    wgse::fiber::idle_work = [](bool tail) { return engine->help(tail); };
    return true;
}

// The batches share one setting, mg_lchain_dp's for the run (the GPU fills other ones'
// reads on the CPU).
bool Engine::accepts(const Params &p) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!params_set_) params = p, params_set_ = true;
    return memcmp(&params, &p, sizeof p) == 0;
}

Job *Engine::acquire() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (pool_.empty()) return new Job;
    Job *job = pool_.back();
    pool_.pop_back();
    return job;
}

void Engine::release(Job *job) {
    // Spare buffers of a job with many anchors are freed rather than kept.
    if (job->packed.anchors.size() > target_anchors_) *job = Job();
    std::lock_guard<std::mutex> lock(mutex_);
    pool_.push_back(job);
}

void Engine::submit(Job *job) {
    job->outcome = Job::queued;
    std::lock_guard<std::mutex> lock(mutex_);
    const bool was_empty = queue_.empty();
    if (was_empty) oldest_ = now();
    queue_.push_back(job);
    queued_anchors_ += job->packed.n_anchors;
    if (was_empty || queued_anchors_ >= target_anchors_) work_cv_.notify_one();
}

// Fills the newest queued read on this CPU thread while both batches are in flight
// (or every read has started), so neither the GPU nor the CPU waits for the other.
bool Engine::help(bool tail) {
    Job *job;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (queue_.empty() || (!tail && in_flight_ < dispatchers)) return false;
        job = queue_.back();
        queue_.pop_back();
        queued_anchors_ -= job->packed.n_anchors;
    }
    thread_local std::vector<int32_t> t;
    fill_queued_cpu(params, *job, t);
    job->outcome = Job::cpu;
    ++helped_;
    wgse::fiber::complete(job->read, 1);
    return true;
}

bool Engine::ready(double now_s) const {
    if (queue_.empty()) return false;
    if (queued_anchors_ >= target_anchors_) return true;
    return now_s - oldest_ >= (in_flight_ == 0 ? idle_delay_ : busy_delay_);
}

void Engine::dispatcher(int slot) {
    std::vector<Job *> batch;
    for (;;) {
        batch.clear();
        {
            std::unique_lock<std::mutex> lock(mutex_);
            for (;;) {
                const double t = now();
                if (ready(t)) break;
                if (queue_.empty()) work_cv_.wait(lock);
                else {
                    const double delay = in_flight_ == 0 ? idle_delay_ : busy_delay_;
                    work_cv_.wait_for(lock, std::chrono::duration<double>(oldest_ + delay - t));
                }
            }
            size_t anchors = 0;
            while (!queue_.empty() && anchors + queue_.front()->packed.n_anchors <= max_anchors_) {
                anchors += queue_.front()->packed.n_anchors;
                batch.push_back(queue_.front());
                queue_.pop_front();
                if (anchors >= target_anchors_) break;
            }
            queued_anchors_ -= anchors;
            oldest_ = now();
            if (in_flight_++ == 0) busy_since_ = oldest_;
        }
        run_batch(batch, slot);
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (--in_flight_ == 0) busy_ns_ += nanoseconds(now() - busy_since_);
            work_cv_.notify_all();
        }
    }
}

void Engine::run_batch(std::vector<Job *> &batch, int slot) {
    size_t anchors = 0, runs = 0, n_short = 0;
    for (const Job *job : batch) {
        anchors += job->packed.n_anchors, runs += job->packed.runs.size();
        for (const Run &run : job->packed.runs) n_short += run.n < warp_run;
    }
    Batch b;
    std::string reason;
    bool ok = enabled() && device_->stage(slot, anchors, runs, b, reason);
    if (ok) {
        // Short runs first, then long ones, each with its anchors' place in the batch.
        size_t offset = 0, next_short = 0, next_long = n_short;
        for (const Job *job : batch) {
            memcpy(b.anchors + offset, job->packed.anchors.data(), job->packed.n_anchors * sizeof(Anchor));
            for (const Run &run : job->packed.runs)
                b.runs[run.n < warp_run ? next_short++ : next_long++] = {(uint32_t)(offset + run.first), run.n};
            offset += job->packed.n_anchors;
        }
        double gpu_seconds = 0;
        ok = device_->run(slot, params, n_short, runs, anchors, gpu_seconds, reason);
        if (ok && fail_batches) reason = "injected GPU failure", ok = false;
        if (ok) {
            gpu_ns_ += nanoseconds(gpu_seconds);
            ++batches_;
            offset = 0;
            for (Job *job : batch) {
                unpack(job->packed, b.scores + offset, job->f, job->p, job->v);
                offset += job->packed.n_anchors;
            }
        }
    }
    if (!ok && reason.size()) fail(reason);
    for (Job *job : batch) {
        job->outcome = ok ? Job::gpu : Job::failed;
        wgse::fiber::complete(job->read, 1);
    }
}

void Engine::fail(const std::string &reason) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (failure_.empty()) failure_ = reason;
    enabled_ = false;
}

bool Engine::failed(std::string &reason) const {
    std::lock_guard<std::mutex> lock(mutex_);
    reason = failure_;
    return !failure_.empty();
}

void Engine::print_metrics() const {
    const double gpu = gpu_ns_.load() * 1e-9;
    const size_t batches = batches_.load();
    fprintf(stderr, "[wgse-minimap2] GPU chaining: %zu reads (%zu on the CPU: unsupported anchors), "
        "%zu by idle CPU threads; anchors: %zu alone, %zu in %zu runs queued for the GPU, %zu in %zu runs "
        "over %u on the CPU; %zu batches of %.0f anchors, GPU %.2f s, batches in flight %.2f s; "
        "%.0f MB of GPU buffers\n",
        jobs.load(), unsupported.load(), helped_.load(), alone.load(), gpu_anchors.load(), gpu_runs.load(),
        cpu_anchors.load(), cpu_runs.load(), max_gpu_run, batches,
        batches ? (double)gpu_anchors.load() / batches : 0.0, gpu, busy_ns_.load() * 1e-9,
        device_->buffer_bytes() / 1048576.0);
}

} // namespace

static int32_t span(const mm128_t &a) { return (int32_t)(a.y >> 32 & 0xff); }

// Upstream's loop resets at anchor i when no earlier anchor is in range: when a[i]
// differs from a[i - 1] in reference sequence or strand, or lies more than max_dist_x
// past it, the loop's start st moves to i, and a[i] is also too far from max_ii, so
// max_ii restarts at i too. Nothing before i then affects f, p or v from i on, so the
// anchors split into runs, each filled as if it were the whole input (with p offset by
// its start). A lone anchor scores its own span with no predecessor. Returns false if
// the GPU can't take the job: anchors with query positions of 2^24 or more, several
// read segments, or a run that crosses reference sequences (only within max_dist_x of
// a sequence's 2^32 end).
bool wgse::chain::split(const Params &params, int64_t n, const mm128_t *a, uint32_t max_gpu_run, Packed &out,
    int32_t *f, int64_t *p, int32_t *v) {
    out.runs.clear(), out.starts.clear(), out.cpu_runs.clear();
    if (out.anchors.size() < (size_t)n) out.anchors.resize((size_t)n);
    Anchor *packed = out.anchors.data();
    size_t n_packed = 0;
    const uint64_t d = (uint64_t)(int64_t)params.max_dist_x;
    const uint64_t segment = a[0].y & MM_SEED_SEG_MASK;
    bool ok = true;
    for (int64_t s = 0; s < n;) {
        int64_t e = s + 1;
        uint64_t prev = a[s].x;
        for (; e < n; ++e) {
            const uint64_t x = a[e].x;
            if ((x >> 32 != prev >> 32 || x > prev + d) && x - prev > d) break;
            ok &= x >> 32 == prev >> 32;
            prev = x;
        }
        const int64_t len = e - s;
        if (len == 1) {
            f[s] = v[s] = span(a[s]);
            p[s] = -1;
        } else if (len > (int64_t)max_gpu_run) {
            out.cpu_runs.push_back(s), out.cpu_runs.push_back(len);
        } else {
            out.runs.push_back({(uint32_t)n_packed, (uint32_t)len});
            out.starts.push_back(s);
            for (int64_t i = s; i < e; ++i) {
                const uint64_t y = a[i].y;
                ok &= (uint32_t)y < 1u << 24 && (y & MM_SEED_SEG_MASK) == segment;
                packed[n_packed++] = {(uint32_t)a[i].x, (uint32_t)y | (uint32_t)(y >> 32 & 0xff) << 24};
            }
        }
        s = e;
    }
    out.n_anchors = n_packed;
    return ok;
}

// Upstream's loop over runs of a[], from start for len anchors.
void wgse::chain::fill_cpu(const Params &params, const mm128_t *a, int64_t start, int64_t len, int32_t *f, int64_t *p,
    int32_t *v, std::vector<int32_t> &t) {
    t.assign((size_t)len, 0);
    wgse_lchain_dp_fill(params.max_dist_x, params.max_dist_y, params.bw, params.max_skip, params.max_iter,
        params.pen_gap, params.pen_skip, params.is_cdna, 1, len, a + start, f + start, p + start, v + start, t.data());
    for (int64_t i = start; i < start + len; ++i)
        if (p[i] >= 0) p[i] += start;
}

// The GPU's f and p, and v as upstream derives it: the peak score up to each anchor.
void wgse::chain::unpack(const Packed &packed, const Score *scores, int32_t *f, int64_t *p, int32_t *v) {
    for (size_t k = 0; k < packed.runs.size(); ++k) {
        const Run &run = packed.runs[k];
        const int64_t s = packed.starts[k];
        const Score *score = scores + run.first;
        for (uint32_t i = 0; i < run.n; ++i) {
            const int32_t fi = score[i].f;
            const int64_t j = score[i].p < 0 ? -1 : s + score[i].p;
            f[s + i] = fi, p[s + i] = j;
            v[s + i] = j >= 0 && v[j] > fi ? v[j] : fi;
        }
    }
}

#ifdef __APPLE__
std::unique_ptr<Device> wgse::chain::make_device() { return nullptr; }
extern "C" void wgse_chain_inject_hang(const char *, double) {}
#endif

extern "C" {

void wgse_chain_tune(int batch_anchors, int max_gpu_run, int fail_batches) {
    tune_batch = batch_anchors, tune_max_run = max_gpu_run, tune_fail = fail_batches;
}

int wgse_chain_gpu_start(char *reason, int reason_size) {
    std::string why;
    if (!engine) engine = new Engine;
    if (engine->start(why)) return 1;
    delete engine;
    engine = nullptr;
    snprintf(reason, reason_size, "%s", why.c_str());
    return 0;
}

int wgse_chain_dp_gpu(int max_dist_x, int max_dist_y, int bw, int max_skip, int max_iter, float chn_pen_gap,
    float chn_pen_skip, int is_cdna, int n_seg, int64_t n, const mm128_t *a, int32_t *f, int64_t *p,
    int32_t *v) {
    if (!engine || !engine->enabled() || n_seg != 1 || n < 2 || max_dist_x < 0 || max_iter < 0) return 0;
    wgse::fiber::Read *read = wgse::fiber::current();
    if (!read) return 0;
    const Params params{max_dist_x, max_dist_y, bw, max_skip, max_iter, is_cdna, chn_pen_gap, chn_pen_skip};
    if (!engine->accepts(params)) return 0;
    ++engine->jobs;
    Job *job = engine->acquire();
    job->a = a, job->f = f, job->p = p, job->v = v, job->read = read;
    if (!split(params, n, a, engine->max_gpu_run, job->packed, f, p, v) || job->packed.n_anchors > engine->max_job_anchors()) {
        ++engine->unsupported;
        engine->release(job);
        return 0;
    }
    size_t cpu = 0;
    for (size_t k = 1; k < job->packed.cpu_runs.size(); k += 2) cpu += (size_t)job->packed.cpu_runs[k];
    engine->alone += (size_t)n - job->packed.n_anchors - cpu;
    engine->gpu_anchors += job->packed.n_anchors, engine->gpu_runs += job->packed.runs.size();
    engine->cpu_anchors += cpu, engine->cpu_runs += job->packed.cpu_runs.size() / 2;
    const bool queued = !job->packed.runs.empty();
    if (queued) {
        wgse::fiber::expect(read, 1);
        engine->submit(job);
    }
    // Long runs on this thread, while the GPU fills the rest.
    thread_local std::vector<int32_t> t;
    for (size_t k = 0; k < job->packed.cpu_runs.size(); k += 2)
        fill_cpu(engine->params, a, job->packed.cpu_runs[k], job->packed.cpu_runs[k + 1], f, p, v, t);
    if (queued) {
        wgse::fiber::wait(read);
        if (job->outcome == Job::failed) fill_queued_cpu(engine->params, *job, t);
    }
    engine->release(job);
    return 1;
}

void wgse_chain_print_metrics(void) {
    if (engine) engine->print_metrics();
}

int wgse_chain_gpu_failed(char *reason, int reason_size) {
    std::string why;
    if (!engine || !engine->failed(why)) return 0;
    snprintf(reason, reason_size, "%s", why.c_str());
    return 1;
}

} // extern "C"
