// The gap-fill offload engine (ksw_gpu.h) on any ksw_engine.h device: reads run as
// fibers on the mapping threads and queue their fills; dispatcher threads batch them for
// the device, and mapping threads with nothing to run compute queued fills themselves.
#include "fiber.h"
#include "ksw_engine.h"
#include "ksw_gpu.h"

extern "C" {
#include "kalloc.h"
#include "kthread.h"
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
#include <sys/mman.h>
#include <unistd.h>
#include <string>
#include <thread>
#include <vector>

namespace {

using namespace wgse::ksw;

double now() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

// Fibers: each mapping thread runs several reads, switching to another read in
// user space while one waits for the GPU. A fiber stays on the thread that made it.
// wgse_fiber_switch saves the callee-saved registers on the current stack, stores
// sp to *from and resumes the stack at to; wgse_fiber_boot starts a new fiber,
// calling its entry with its argument (fiber_frame lays out the first switch's frame).
extern "C" void wgse_fiber_switch(void **from, void *to);
extern "C" void wgse_fiber_boot(void);
#if defined(__aarch64__) && defined(__APPLE__)
// Callee-saved x19-x30 and d8-d15; boot calls x20(x19).
asm(R"(
    .text
    .p2align 2
    .private_extern _wgse_fiber_switch
    .globl _wgse_fiber_switch
_wgse_fiber_switch:
    sub sp, sp, #160
    stp x19, x20, [sp, #0]
    stp x21, x22, [sp, #16]
    stp x23, x24, [sp, #32]
    stp x25, x26, [sp, #48]
    stp x27, x28, [sp, #64]
    stp x29, x30, [sp, #80]
    stp d8, d9, [sp, #96]
    stp d10, d11, [sp, #112]
    stp d12, d13, [sp, #128]
    stp d14, d15, [sp, #144]
    mov x9, sp
    str x9, [x0]
    mov sp, x1
    ldp x19, x20, [sp, #0]
    ldp x21, x22, [sp, #16]
    ldp x23, x24, [sp, #32]
    ldp x25, x26, [sp, #48]
    ldp x27, x28, [sp, #64]
    ldp x29, x30, [sp, #80]
    ldp d8, d9, [sp, #96]
    ldp d10, d11, [sp, #112]
    ldp d12, d13, [sp, #128]
    ldp d14, d15, [sp, #144]
    add sp, sp, #160
    ret
    .p2align 2
    .private_extern _wgse_fiber_boot
    .globl _wgse_fiber_boot
_wgse_fiber_boot:
    mov x0, x19
    blr x20
    brk #1
)");
constexpr size_t frame_size = 160;
// x19 = argument, x20 = entry, x30 = boot.
void fiber_frame(void **frame, void *argument, void (*entry)(void *)) {
    frame[0] = argument;
    frame[1] = (void *)entry;
    frame[11] = (void *)&wgse_fiber_boot;
}
#elif defined(__x86_64__) && defined(__ELF__)
// System V: callee-saved rbx, rbp and r12-r15, and the control bits of MXCSR and the
// x87 FPU; boot calls r13(r12) with the stack 16-byte aligned.
asm(R"(
    .text
    .p2align 4
    .globl wgse_fiber_switch
    .hidden wgse_fiber_switch
    .type wgse_fiber_switch, @function
wgse_fiber_switch:
    pushq %rbp
    pushq %rbx
    pushq %r12
    pushq %r13
    pushq %r14
    pushq %r15
    subq $8, %rsp
    stmxcsr (%rsp)
    fnstcw 4(%rsp)
    movq %rsp, (%rdi)
    movq %rsi, %rsp
    ldmxcsr (%rsp)
    fldcw 4(%rsp)
    addq $8, %rsp
    popq %r15
    popq %r14
    popq %r13
    popq %r12
    popq %rbx
    popq %rbp
    ret
    .size wgse_fiber_switch, .-wgse_fiber_switch
    .p2align 4
    .globl wgse_fiber_boot
    .hidden wgse_fiber_boot
    .type wgse_fiber_boot, @function
wgse_fiber_boot:
    movq %r12, %rdi
    callq *%r13
    ud2
    .size wgse_fiber_boot, .-wgse_fiber_boot
    .section .note.GNU-stack,"",@progbits
    .text
)");
// MXCSR and the x87 control word, r15, r14, r13, r12, rbx, rbp and the return address;
// the stack top above it stays 16-byte aligned.
constexpr size_t frame_size = 64;
void fiber_frame(void **frame, void *argument, void (*entry)(void *)) {
    uint32_t mxcsr;
    uint16_t fpu_control;
    asm volatile("stmxcsr %0" : "=m"(mxcsr));
    asm volatile("fnstcw %0" : "=m"(fpu_control));
    memcpy(&frame[0], &mxcsr, sizeof mxcsr);
    memcpy((char *)&frame[0] + 4, &fpu_control, sizeof fpu_control);
    frame[3] = (void *)entry;
    frame[4] = argument;
    frame[7] = (void *)&wgse_fiber_boot;
}
#else
#error "wgse-minimap2's GPU offload has fibers for arm64 macOS and x86-64 ELF only"
#endif

// A fill queued by a read; score INT_MIN means the CPU computes it.
struct Entry {
    uint32_t qoff, toff, coff;
    int32_t qlen, tlen, w, score, n_cigar;
};

struct Thread;

// A fiber's fills for the read it is aligning. While the fiber waits, the GPU
// dispatchers and helping threads write the results.
struct Read {
    std::vector<uint8_t> seq;
    std::vector<Entry> entries;
    std::vector<uint32_t> cigar;
    size_t cursor = 0;
    std::atomic<size_t> remaining{0};
    Thread *owner = nullptr;
};

struct Pending {
    Read *read;
    size_t first, last;
};

// A mapping thread, woken when one of its fibers' fills are done. Its fibers'
// reads share one kalloc arena, so reads in flight cost their live memory only.
struct Thread {
    void *km = nullptr;
    int reading = 0; // fibers in the middle of a read
    // The arena needs minimap2's post-read reset while other reads still hold
    // memory in it: start no reads until they finish, then replace it.
    bool draining = false;
    std::mutex mutex;
    std::condition_variable cv;
    bool signaled = false;
    void signal() {
        std::lock_guard<std::mutex> lock(mutex);
        signaled = true;
        cv.notify_one();
    }
    void wait(double seconds) {
        std::unique_lock<std::mutex> lock(mutex);
        cv.wait_for(lock, std::chrono::duration<double>(seconds), [&] { return signaled; });
        signaled = false;
    }
};

struct Fiber {
    enum State { Idle, Running, Waiting };
    void *sp = nullptr, *scheduler_sp = nullptr;
    void *stack = nullptr;
    size_t stack_size = 0;
    State state = Idle;
    bool started = false;
    long index = 0;
    int tid = 0;
    struct For *job = nullptr;
    Read read;
};

// One wgse_ksw_for call.
struct For {
    void (*func)(void *, long, int);
    void *data;
    long n;
    std::atomic<long> next{0};
};

thread_local Fiber *current_fiber = nullptr;

void fiber_main(void *argument) {
    Fiber *f = (Fiber *)argument;
    for (;;) {
        f->job->func(f->job->data, f->index, f->tid);
        --f->read.owner->reading;
        f->state = Fiber::Idle;
        wgse_fiber_switch(&f->sp, f->scheduler_sp);
    }
}

void fiber_resume(Fiber *f) {
    current_fiber = f;
    f->state = Fiber::Running;
    wgse_fiber_switch(&f->scheduler_sp, f->sp);
    current_fiber = nullptr;
}

void fiber_yield(Fiber *f) {
    wgse_fiber_switch(&f->sp, f->scheduler_sp);
}

class Engine {
public:
    bool start(std::string &reason);
    bool probe(std::string &reason);
    bool enabled() const { return enabled_.load(std::memory_order_relaxed); }

    int workers(int n_threads) const { return n_threads * fibers_per_thread_; }
    void run_for(int n_threads, For &job);

    void set_scoring(int q, int e, int q2, int e2, const int8_t mat[25]);
    bool eligible(int qlen, int tlen, int w) const;
    void submit(Fiber &fiber);
    void print_metrics() const;
    bool failed(std::string &reason) const;

    std::atomic<size_t> taken{0}, drains{0};
    void finish(Read &read, size_t fills);

private:
    void thread_main(Thread &thread, Fiber *fibers, int n_fibers, For &job);
    bool help(void *km, ksw_extz_t &ez, bool tail);
    Limits limits();
    void dispatcher(int slot);
    bool ready(double now_s) const;
    void run_batch(std::vector<Pending> &batch, int slot);
    void fail(const std::string &reason);

    std::unique_ptr<Device> device_;
    std::atomic<bool> enabled_{false};
    int fibers_per_thread_ = 16;
    bool fail_batches_ = false;
    size_t target_fills_ = 4096;
    static constexpr int dispatchers = 2;
    static constexpr size_t help_fills = 8;
    double idle_delay_ = 100e-6, busy_delay_ = 2e-3;
    size_t matrix_cap_ = (size_t)256 << 20;
    // A batch takes as long as its slowest fill, so the few fills that cost several
    // times the typical one (lane steps, columns x anti-diagonals) stay on the CPU.
    int max_cost_ = 8192;

    std::once_flag scoring_once_;
    Scoring scoring_{};
    int8_t mat_[25] = {};
    bool scoring_ok_ = false, narrow_ = false;

    mutable std::mutex mutex_;
    std::condition_variable work_cv_;
    std::deque<Pending> queue_;
    size_t queued_fills_ = 0;
    double oldest_ = 0;
    int in_flight_ = 0, starved_ = 0;
    double busy_since_ = 0;

    std::string failure_;
    std::atomic<size_t> gpu_fills_{0}, wrapped_{0}, batches_{0}, queued_total_{0}, helped_{0};
    std::atomic<uint64_t> gpu_ns_{0}, cells_{0}, busy_ns_{0}, idle_ns_{0};
    std::atomic<int> finishing_{0}; // finish() calls that may still touch a read's owner
};

// Created by wgse_ksw_gpu_start (--metal) only.
Engine *engine = nullptr;
int tune_fibers = 0, tune_batch = 0, tune_max_cost = -1, tune_fail = 0;

uint64_t nanoseconds(double seconds) { return (uint64_t)(std::max(0.0, seconds) * 1e9); }

Limits Engine::limits() {
    if (tune_fibers > 0) fibers_per_thread_ = tune_fibers;
    if (tune_batch > 0) target_fills_ = (size_t)tune_batch;
    if (tune_max_cost >= 0) max_cost_ = tune_max_cost;
    fail_batches_ = tune_fail != 0;
    // A batch has at most target_fills_ fills, and its matrices at most matrix_cap_
    // bytes (each padded to 4 bytes): one fill's never exceeds it.
    const size_t fill_bases = (size_t)(max_qlen + max_tlen);
    return {target_fills_, target_fills_ * fill_bases, matrix_cap_ + 4 * target_fills_,
        target_fills_ * fill_bases};
}

bool Engine::probe(std::string &reason) {
    const Limits l = limits();
    return make_device()->probe(dispatchers, l, reason);
}

bool Engine::start(std::string &reason) {
    const Limits l = limits();
    device_ = make_device();
    if (!device_->start(dispatchers, l, reason)) return false;
    // One dispatcher packs a batch while the other's runs.
    for (int k = 0; k < dispatchers; ++k) std::thread([this, k] { dispatcher(k); }).detach();
    enabled_ = true;
    return true;
}

void Engine::run_for(int n_threads, For &job) {
    const int n_fibers = fibers_per_thread_;
    const size_t stack_size = (size_t)1 << 20, guard = (size_t)getpagesize();
    std::vector<Thread> threads(n_threads);
    std::vector<Fiber> fibers((size_t)n_threads * n_fibers);
    for (size_t k = 0; k < fibers.size(); ++k) {
        Fiber &f = fibers[k];
        void *m = mmap(nullptr, stack_size + guard, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
        if (m == MAP_FAILED) {
            fprintf(stderr, "[wgse-minimap2] out of memory for fiber stacks\n");
            abort();
        }
        mprotect(m, guard, PROT_NONE);
        f.stack = m, f.stack_size = stack_size + guard;
        f.tid = (int)k, f.job = &job;
        f.read.owner = &threads[k / n_fibers];
        // The first switch to the fiber pops this frame and boots fiber_main(&f).
        void **frame = (void **)((char *)m + stack_size + guard - frame_size);
        memset(frame, 0, frame_size);
        fiber_frame(frame, &f, &fiber_main);
        f.sp = frame;
    }
    std::vector<std::thread> pool;
    for (int t = 0; t < n_threads; ++t)
        pool.emplace_back([&, t] { thread_main(threads[t], &fibers[(size_t)t * n_fibers], n_fibers, job); });
    for (std::thread &t : pool) t.join();
    while (finishing_.load() != 0) std::this_thread::yield();
    for (Fiber &f : fibers) munmap(f.stack, f.stack_size);
}

// Runs this thread's fibers: resumes reads whose fills are done, starts new reads,
// and when every fiber waits, computes queued fills on the CPU or sleeps.
void Engine::thread_main(Thread &thread, Fiber *fibers, int n_fibers, For &job) {
    thread.km = km_init();
    void *km = km_init(); // help()'s, which runs between the fibers' allocations
    ksw_extz_t ez;
    memset(&ez, 0, sizeof ez);
    bool exhausted = false;
    for (;;) {
        if (thread.draining && thread.reading == 0) {
            km_destroy(thread.km);
            thread.km = km_init();
            thread.draining = false;
        }
        bool progressed = false;
        int waiting = 0;
        for (int k = 0; k < n_fibers; ++k) {
            Fiber &f = fibers[k];
            if (f.state == Fiber::Waiting && f.read.remaining.load(std::memory_order_acquire) == 0) {
                fiber_resume(&f);
                progressed = true;
            }
            if (f.state == Fiber::Idle && !exhausted && !thread.draining) {
                const long i = job.next.fetch_add(1, std::memory_order_relaxed);
                if (i >= job.n) exhausted = true;
                else {
                    f.index = i;
                    ++thread.reading;
                    fiber_resume(&f);
                    progressed = true;
                }
            }
            waiting += f.state == Fiber::Waiting;
        }
        if (progressed || (waiting == 0 && thread.draining)) continue;
        if (waiting == 0) break;
        if (help(km, ez, exhausted)) continue;
        if (wgse::fiber::idle_work && wgse::fiber::idle_work(exhausted)) continue;
        const double t0 = now();
        {
            std::lock_guard<std::mutex> lock(mutex_);
            ++starved_;
            work_cv_.notify_one();
        }
        thread.wait(200e-6);
        {
            std::lock_guard<std::mutex> lock(mutex_);
            --starved_;
        }
        idle_ns_ += nanoseconds(now() - t0);
    }
    kfree(km, ez.cigar);
    km_destroy(km);
    km_destroy(thread.km);
}

// Computes a few queued fills on this CPU thread, newest first, while the GPU
// is busy (or the reads are all started), so neither waits for the other.
bool Engine::help(void *km, ksw_extz_t &ez, bool tail) {
    Pending p;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (queue_.empty() || (!tail && in_flight_ < dispatchers)) return false;
        Pending &back = queue_.back();
        const size_t n = std::min(help_fills, back.last - back.first);
        p = {back.read, back.last - n, back.last};
        back.last -= n;
        if (back.last == back.first) queue_.pop_back();
        queued_fills_ -= n;
    }
    Read &r = *p.read;
    for (size_t k = p.first; k < p.last; ++k) {
        Entry &e = r.entries[k];
        // mm_align_pair's call; Z-drop and end bonus do not apply to KSW_EZ_APPROX_MAX alone.
        ksw_extd2_sse(km, e.qlen, r.seq.data() + e.qoff, e.tlen, r.seq.data() + e.toff, 5, mat_,
            (int8_t)scoring_.q, (int8_t)scoring_.e, (int8_t)scoring_.q2, (int8_t)scoring_.e2, e.w, -1, 0,
            KSW_EZ_APPROX_MAX, &ez);
        e.score = ez.score;
        e.n_cigar = ez.n_cigar;
        memcpy(r.cigar.data() + e.coff, ez.cigar, (size_t)ez.n_cigar * 4);
    }
    helped_ += p.last - p.first;
    finish(r, p.last - p.first);
    return true;
}

// The owner may see remaining reach 0 and return from run_for before signal() returns,
// so run_for waits for finishing_ to drain before it frees the threads and reads.
void Engine::finish(Read &read, size_t fills) {
    finishing_.fetch_add(1);
    Thread *owner = read.owner;
    if (read.remaining.fetch_sub(fills, std::memory_order_acq_rel) == fills) owner->signal();
    finishing_.fetch_sub(1);
}

void Engine::set_scoring(int q, int e, int q2, int e2, const int8_t mat[25]) {
    std::call_once(scoring_once_, [&] {
        scoring_ok_ = make_scoring(q, e, q2, e2, mat, scoring_, narrow_);
        memcpy(mat_, mat, sizeof mat_);
    });
}

bool Engine::eligible(int qlen, int tlen, int w) const {
    if (!enabled() || !scoring_ok_ || qlen <= 0 || tlen <= 0 || qlen > max_qlen || tlen > max_tlen)
        return false;
    // ksw_extd2_sse's band, which must not bind (only the backtrack's off[] = 0 layout is ported).
    // Its per-anti-diagonal clamps st = max(st, (r-w+1)>>1) and en = min(en, (r+w)>>1) are
    // no-ops for every r exactly when both lengths fit in w + 1.
    if (w >= 0 && (qlen - 1 > w || tlen - 1 > w)) return false;
    return max_cost_ == 0 || lane_columns(qlen) * (qlen + tlen) <= max_cost_;
}

void Engine::submit(Fiber &fiber) {
    Read &read = fiber.read;
    const size_t n = read.entries.size();
    read.remaining.store(n, std::memory_order_relaxed);
    {
        std::lock_guard<std::mutex> lock(mutex_);
        const bool was_empty = queue_.empty();
        if (was_empty) oldest_ = now();
        queue_.push_back({&read, 0, n});
        queued_fills_ += n;
        if (was_empty || queued_fills_ >= target_fills_) work_cv_.notify_one();
    }
    queued_total_ += n;
    fiber.state = Fiber::Waiting;
    fiber_yield(&fiber);
}

bool Engine::ready(double now_s) const {
    if (queued_fills_ == 0) return false;
    if (queued_fills_ >= target_fills_ || (starved_ > 0 && in_flight_ == 0)) return true;
    return now_s - oldest_ >= (in_flight_ == 0 ? idle_delay_ : busy_delay_);
}

void Engine::dispatcher(int slot) {
    std::vector<Pending> batch;
    for (;;) {
        batch.clear();
        {
            std::unique_lock<std::mutex> lock(mutex_);
            for (;;) {
                const double t = now();
                if (ready(t)) break;
                if (queued_fills_ == 0) work_cv_.wait(lock);
                else {
                    const double delay = in_flight_ == 0 ? idle_delay_ : busy_delay_;
                    work_cv_.wait_for(lock, std::chrono::duration<double>(oldest_ + delay - t));
                }
            }
            size_t fills = 0, matrix = 0;
            while (!queue_.empty() && fills < target_fills_) {
                Pending &front = queue_.front();
                size_t last = front.first;
                while (last < front.last && fills < target_fills_) {
                    const Entry &e = front.read->entries[last];
                    const size_t bytes = matrix_bytes(e.qlen, e.tlen);
                    if (fills > 0 && matrix + bytes > matrix_cap_) break;
                    matrix += bytes, ++fills, ++last;
                }
                if (last == front.first) break;
                batch.push_back({front.read, front.first, last});
                if (last < front.last) {
                    front.first = last;
                    break;
                }
                queue_.pop_front();
            }
            queued_fills_ -= fills;
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
void Engine::run_batch(std::vector<Pending> &batch, int slot) {
    // Entries across the batch, grouped by width, longest target first.
    struct Ref { Read *read; size_t index; int c; int32_t tlen; };
    std::vector<Ref> refs;
    for (const Pending &p : batch)
        for (size_t k = p.first; k < p.last; ++k) {
            const Entry &e = p.read->entries[k];
            refs.push_back({p.read, k, lane_columns(e.qlen), e.tlen});
        }
    std::stable_sort(refs.begin(), refs.end(), [](const Ref &x, const Ref &y) {
        return x.c != y.c ? x.c < y.c : x.tlen > y.tlen;
    });
    const size_t n = refs.size();
    size_t first[n_widths + 1] = {};
    for (int k = 0, i = 0; k < n_widths; ++k) {
        first[k] = (size_t)i;
        while ((size_t)i < n && refs[i].c == widths[k]) ++i;
        first[k + 1] = (size_t)i;
    }
    size_t seq = 0, matrix = 0, cigar = 0;
    for (const Ref &ref : refs) {
        const Entry &e = ref.read->entries[ref.index];
        seq += e.qlen + e.tlen;
        matrix += (matrix_bytes(e.qlen, e.tlen) + 3) & ~(size_t)3;
        cigar += e.qlen + e.tlen;
    }
    Batch b;
    std::string reason;
    bool ok = enabled();
    if (ok && !device_->stage(slot, n, seq, matrix, cigar, b, reason)) {
        fail(reason);
        ok = false;
    }
    if (ok) {
        uint64_t cells = 0;
        seq = matrix = cigar = 0;
        for (size_t k = 0; k < n; ++k) {
            const Read &r = *refs[k].read;
            const Entry &e = r.entries[refs[k].index];
            Fill &f = b.fills[k];
            f.qlen = e.qlen, f.tlen = e.tlen;
            f.qoff = (uint32_t)seq, seq += e.qlen;
            f.toff = (uint32_t)seq, seq += e.tlen;
            f.poff = (uint32_t)matrix, matrix += (matrix_bytes(e.qlen, e.tlen) + 3) & ~(size_t)3;
            f.coff = (uint32_t)cigar, cigar += e.qlen + e.tlen;
            memcpy(b.seq + f.qoff, r.seq.data() + e.qoff, e.qlen);
            memcpy(b.seq + f.toff, r.seq.data() + e.toff, e.tlen);
            cells += (uint64_t)e.qlen * e.tlen;
        }
        double gpu_seconds = 0;
        ok = device_->run(slot, scoring_, narrow_, n, first, cigar, b, gpu_seconds, reason);
        if (ok && fail_batches_) reason = "injected GPU failure", ok = false;
        if (ok) {
            gpu_ns_ += nanoseconds(gpu_seconds);
            cells_ += cells;
            ++batches_;
        } else {
            fail(reason);
        }
    }
    size_t gpu = 0, wrapped = 0;
    for (size_t k = 0; k < n; ++k) {
        Read &r = *refs[k].read;
        Entry &e = r.entries[refs[k].index];
        if (!ok || b.score[k] == INT_MIN) {
            e.score = INT_MIN;
            wrapped += ok;
            continue;
        }
        e.score = b.score[k];
        e.n_cigar = b.n_cigar[k];
        const uint32_t *src = b.cigar + b.fills[k].coff;
        uint32_t *dst = r.cigar.data() + e.coff;
        for (int c = 0; c < e.n_cigar; ++c) dst[c] = src[e.n_cigar - 1 - c];
        ++gpu;
    }
    gpu_fills_ += gpu;
    wrapped_ += wrapped;
    for (const Pending &p : batch) finish(*p.read, p.last - p.first);
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
    const char *name = device_->name();
    fprintf(stderr, "[wgse-minimap2] %s gap fills: %zu queued, %zu on the GPU (%zu used), "
        "%zu by idle CPU threads, %zu redone on the CPU (int8 overflow); %s kernels; %zu batches of %.0f fills, "
        "GPU %.2f s (%.2f G cells/s), batches in flight %.2f s, threads idle %.2f s; "
        "%.0f MB of GPU buffers; %zu %s interruptions; %zu arena drains\n",
        name, queued_total_.load(), gpu_fills_.load(), taken.load(), helped_.load(), wrapped_.load(),
        narrow_ ? "16-bit" : "8-bit", batches, batches ? (double)gpu_fills_.load() / batches : 0.0, gpu,
        gpu > 0 ? cells_.load() / gpu / 1e9 : 0.0, busy_ns_.load() * 1e-9, idle_ns_.load() * 1e-9,
        device_->buffer_bytes() / 1048576.0, device_->interruptions(), name, drains.load());
}

} // namespace

namespace wgse::fiber {

bool (*idle_work)(bool tail) = nullptr;

Read *current() {
    return engine && engine->enabled() && current_fiber ? reinterpret_cast<Read *>(current_fiber) : nullptr;
}
void expect(Read *read, size_t n) {
    reinterpret_cast<Fiber *>(read)->read.remaining.store(n, std::memory_order_relaxed);
}
void complete(Read *read, size_t n) { engine->finish(reinterpret_cast<Fiber *>(read)->read, n); }
void wait(Read *read) {
    Fiber &fiber = *reinterpret_cast<Fiber *>(read);
    if (fiber.read.remaining.load(std::memory_order_acquire) == 0) return;
    fiber.state = Fiber::Waiting;
    fiber_yield(&fiber);
}

} // namespace wgse::fiber

extern "C" {

void wgse_ksw_tune(int fibers_per_thread, int batch_fills, int max_cost, int fail_batches) {
    tune_fibers = fibers_per_thread, tune_batch = batch_fills, tune_max_cost = max_cost;
    tune_fail = fail_batches;
}

int wgse_ksw_gpu_probe(char *reason, int reason_size) {
    std::string why;
    Engine probe;
    if (probe.probe(why)) return 1;
    snprintf(reason, reason_size, "%s", why.c_str());
    return 0;
}

int wgse_ksw_gpu_start(char *reason, int reason_size) {
    std::string why;
    if (!engine) engine = new Engine;
    if (engine->start(why)) return 1;
    delete engine;
    engine = nullptr;
    snprintf(reason, reason_size, "%s", why.c_str());
    return 0;
}

int wgse_ksw_gpu_enabled(void) { return engine && engine->enabled() && current_fiber; }

int wgse_ksw_workers(int n_threads) {
    return engine ? engine->workers(n_threads) : n_threads;
}

void *wgse_ksw_share_km(void **km) {
    if (!current_fiber || !*km) return nullptr;
    void *own = *km;
    *km = current_fiber->read.owner->km;
    return own;
}

void wgse_ksw_unshare_km(void **km, void *own) {
    if (!own) return;
    current_fiber->read.owner->km = *km;
    *km = own;
}

int wgse_ksw_km_check(const void *km, int64_t cap_kalloc) {
    if (!current_fiber) return 1;
    Thread &thread = *current_fiber->read.owner;
    if (thread.reading <= 1) {
        thread.draining = false;
        return 1;
    }
    km_stat_t st;
    km_stat(km, &st);
    if (!thread.draining && (st.largest > 1U << 28 || (cap_kalloc > 0 && st.capacity > (size_t)cap_kalloc))) {
        thread.draining = true;
        ++engine->drains;
    }
    return 0;
}

void wgse_ksw_km_reset(void *km) {
    if (current_fiber) current_fiber->read.owner->km = km;
}

void wgse_ksw_for(int n_threads, void (*func)(void *, long, int), void *data, long n) {
    if (!engine) {
        kt_for(n_threads, func, data, n);
        return;
    }
    For job;
    job.func = func, job.data = data, job.n = n;
    engine->run_for(n_threads, job);
}

void wgse_ksw_set_scoring(int q, int e, int q2, int e2, const int8_t mat[25]) {
    engine->set_scoring(q, e, q2, e2, mat);
}

void wgse_ksw_begin_read(void) {
    Read &r = current_fiber->read;
    r.seq.clear();
    r.entries.clear();
    r.cigar.clear();
    r.cursor = 0;
}

int wgse_ksw_eligible(int qlen, int tlen, int w) {
    return engine->eligible(qlen, tlen, w);
}

uint8_t *wgse_ksw_add(int qlen, const uint8_t *qseq, int tlen, int w) {
    Read &r = current_fiber->read;
    Entry e{};
    e.qoff = (uint32_t)r.seq.size();
    e.toff = e.qoff + qlen;
    e.coff = (uint32_t)r.cigar.size();
    e.qlen = qlen, e.tlen = tlen, e.w = w, e.score = INT_MIN;
    r.seq.insert(r.seq.end(), qseq, qseq + qlen);
    r.seq.resize(r.seq.size() + tlen);
    r.cigar.resize(r.cigar.size() + qlen + tlen);
    r.entries.push_back(e);
    return r.seq.data() + e.toff;
}

void wgse_ksw_run_read(void) {
    if (!current_fiber->read.entries.empty()) engine->submit(*current_fiber);
}

int wgse_ksw_take(void *km, int qlen, const uint8_t *qseq, int tlen, const uint8_t *tseq,
    int w, int flag, ksw_extz_t *ez) {
    if (!engine || !current_fiber || flag != KSW_EZ_APPROX_MAX) return 0;
    Read &r = current_fiber->read;
    for (size_t k = r.cursor; k < r.entries.size(); ++k) {
        const Entry &e = r.entries[k];
        if (e.qlen != qlen || e.tlen != tlen || e.w != w
            || memcmp(r.seq.data() + e.qoff, qseq, qlen) || memcmp(r.seq.data() + e.toff, tseq, tlen))
            continue;
        // Fills are queued in the order mm_align1 runs them; a Z-drop skips the rest of a region's.
        r.cursor = k + 1;
        if (e.score == INT_MIN) return 0;
        ksw_reset_extz(ez);
        if (e.n_cigar > ez->m_cigar) {
            int m = e.n_cigar;
            m |= m >> 1, m |= m >> 2, m |= m >> 4, m |= m >> 8, m |= m >> 16, ++m;
            ez->cigar = (uint32_t *)krealloc(km, ez->cigar, (size_t)m * 4);
            ez->m_cigar = m;
        }
        memcpy(ez->cigar, r.cigar.data() + e.coff, (size_t)e.n_cigar * 4);
        ez->n_cigar = e.n_cigar;
        ez->score = e.score;
        engine->taken.fetch_add(1, std::memory_order_relaxed);
        return 1;
    }
    return 0;
}

void wgse_ksw_print_metrics(void) {
    if (engine) engine->print_metrics();
}

int wgse_ksw_gpu_failed(char *reason, int reason_size) {
    std::string why;
    if (!engine || !engine->failed(why)) return 0;
    snprintf(reason, reason_size, "%s", why.c_str());
    return 1;
}

} // extern "C"

bool wgse::ksw::make_scoring(int q, int e, int q2, int e2, const int8_t mat[25], Scoring &scoring,
    bool &narrow) {
    int min_sc = mat[1];
    for (int t = 0; t < 25; ++t) min_sc = std::min(min_sc, (int)mat[t]);
    // The kernel scores only matches, mismatches and N, like ksw_extd2_sse
    // without KSW_EZ_GENERIC_SC, and assumes ksw_extd2_sse's q/e swap is a no-op.
    bool simple = true;
    for (int a = 0; a < 4; ++a)
        for (int b = 0; b < 4; ++b) simple &= mat[a * 5 + b] == (a == b ? mat[0] : mat[1]);
    const bool ok = simple && q + e <= q2 + e2 && -min_sc <= 2 * (q + e) && (q != q2 || e != e2);
    int lt = e != e2 ? (q2 - q) / (e - e2) - 1 : 0;
    if (q2 + e2 + lt * e2 > q + e + lt * e) ++lt;
    scoring = {q, e, q2, e2, mat[0], mat[1], mat[24] == 0 ? -e2 : mat[24], 4, lt,
        lt * (e - e2) - (q2 - q) - e2};
    // The 16-bit kernels equal int8 while z <= M before the cap and u, v >= -A
    // (they check both). Then, with A = q + e, A2 = q2 + e2, M the match score and
    // m the lowest score (z lies in [m, M]), whatever the order of q and q2:
    //   x, y in [-A, -e], x2, y2 in [-A2, -e2], u, v in [-A, M + A];
    //   a = x + v, b = y + u in [-2A, M + A - e]; a2, b2 in [-A - A2, M + A - e2];
    //   z - q in [m - q, M - q], z - q2 in [m - q2, M - q2];
    //   a - (z - q) in [-2A - M + q, q], a2 - (z - q2) in [-A - A2 - M + q2, q2];
    //   the boundary u, v: -A, -e, long_diff, -e2.
    // narrow requires all of these to lie in int8, where both kernels agree.
    const int A = q + e, A2 = q2 + e2, M = scoring.mch, m = std::min(scoring.mis, scoring.scN);
    const int ld = scoring.long_diff;
    const auto in8 = [](int lo, int hi) { return lo >= -128 && hi <= 127; };
    narrow = ok && in8(-A, M + A) && in8(-A2, -e2) && in8(-2 * A, M + A - e)
        && in8(-A - A2, M + A - e2) && in8(m - q, M - q) && in8(m - q2, M - q2)
        && in8(-2 * A - M + q, q) && in8(-A - A2 - M + q2, q2) && in8(ld, ld) && ld >= -A && ld <= M + A
        && A + A2 <= 127 && e2 <= A;
    return ok;
}
