#include "metal_ksw.h"
#include "runtime.h"

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

// Must match the Metal structs below.
struct Scoring {
    int32_t q, e, q2, e2, mch, mis, scN, m1, long_thres, long_diff;
};
struct Fill {
    uint32_t qoff, toff, poff, coff;
    int32_t qlen, tlen;
};

const char *kernel_source = R"METAL(
#include <metal_stdlib>
using namespace metal;
struct Scoring { int q, e, q2, e2, mch, mis, scN, m1, long_thres, long_diff; };
struct Fill { uint qoff, toff, poff, coff; int qlen, tlen; };

inline void push_cigar(device uint *cigar, thread int &n, uint op, int len)
{
    if (n == 0 || op != (cigar[n - 1] & 0xf)) cigar[n++] = (uint)len << 4 | op;
    else cigar[n - 1] += (uint)len << 4;
}

// ksw_extd2_sse with flag KSW_EZ_APPROX_MAX and a band that never binds; one
// simdgroup per fill. Lane l owns query columns [l*C, l*C+C) and at step s
// computes target row i = s - l, so each cell sees the same (i-1, j) and
// (i, j-1) int8 values as the CPU's anti-diagonal loop. The CPU's approximate
// score walk telescopes to v(0,0) - qe + sum u(i,0) + sum v(T-1,j), which is
// exact unless u or v wrap; a wrap returns INT_MIN and the CPU redoes the fill.
// The backtrack is ksw_backtrack's (is_rot=0, no offsets), emitted in reverse.
//
// T = char computes in int8 like the CPU. T = short (when Scoring::narrow) is
// faster and equals it while no int8 value would wrap: for such scoring that
// holds while every diagonal step z stays at most the match score before the cap
// (so a, b, a2, b2 <= z) and u, v >= -(q+e), which the kernel checks.
template <int C, typename T>
inline void fill(const Fill F, constant Scoring &S, device const uchar *seq, device uchar *pbuf,
    device int *score, device uint *cigars, device int *n_cigar, uint fi, ushort lane)
{
    constexpr bool wide = is_same<T, short>::value;
    const int Tl = F.tlen, Q = F.qlen, stride = (Q + C - 1) / C * C;
    const T qe = (T)(S.q + S.e), qe2 = (T)(S.q2 + S.e2), q = (T)S.q, q2 = (T)S.q2;
    const T mch = (T)S.mch, mis = (T)S.mis, scN = (T)S.scN;
    const int m1 = S.m1, lt = S.long_thres;
    device const uchar *qs = seq + F.qoff, *ts = seq + F.toff;
    device uchar *p = pbuf + F.poff;
    const int j0 = lane * C;
    T x[C], v[C], x2[C], lo[C], hi[C]; // lo, hi: min of u and v, max of z before the cap
    uchar qb[C];
    for (int k = 0; k < C; ++k) {
        const int r = j0 + k; // boundary (-1, j)
        x[k] = -qe; x2[k] = -qe2;
        v[k] = r == 0 ? -qe : r < lt ? (T)-S.e : r == lt ? (T)S.long_diff : (T)-S.e2;
        qb[k] = r < Q ? qs[r] : (uchar)m1;
        lo[k] = 0, hi[k] = mch;
    }
    uint carry = 0; // (u, y, y2) of (i, j0 - 1) from lane - 1's previous step
    int H = 0, wrapped = 0;
    for (int s = 0; s < Tl + 31; ++s) {
        const int i = s - lane;
        const uint in = simd_shuffle_up(carry, (ushort)1);
        T u, y, y2;
        if (lane == 0) {
            u = i == 0 ? -qe : i < lt ? (T)-S.e : i == lt ? (T)S.long_diff : (T)-S.e2;
            y = -qe; y2 = -qe2;
        } else {
            u = (T)(char)(in & 0xff); y = (T)(char)(in >> 8 & 0xff); y2 = (T)(char)(in >> 16 & 0xff);
        }
        if (i >= 0 && i < Tl) {
            const int sb = ts[i];
            uchar dd[C];
            for (int k = 0; k < C; ++k) {
                const int sq = qb[k];
                T z = (sb == m1 || sq == m1) ? scN : (sb == sq ? mch : mis);
                const T xp = x[k], vp = v[k], x2p = x2[k], ut = u, yt = y, y2t = y2;
                T a = xp + vp, b = yt + ut, a2 = x2p + vp, b2 = y2t + ut;
                uchar d = a > z ? 1 : 0; z = max(z, a);
                d = b > z ? 2 : d; z = max(z, b);
                d = a2 > z ? 3 : d; z = max(z, a2);
                d = b2 > z ? 4 : d; z = max(z, b2);
                if (wide) hi[k] = max(hi[k], z);
                z = min(z, mch);
                if (wide) {
                    u = z - vp; v[k] = z - ut;
                    lo[k] = min(lo[k], min(u, v[k]));
                } else {
                    const int ui = (int)z - (int)vp, vi = (int)z - (int)ut;
                    if (j0 + k < Q) wrapped |= ((uint)(ui + 128) | (uint)(vi + 128)) > 255u ? 1 : 0;
                    u = (T)ui; v[k] = (T)vi;
                }
                T tmp = z - q; a = a - tmp; b = b - tmp;
                tmp = z - q2; a2 = a2 - tmp; b2 = b2 - tmp;
                x[k] = max(a, (T)0) - qe; d |= a > 0 ? 0x08 : 0;
                y = max(b, (T)0) - qe; d |= b > 0 ? 0x10 : 0;
                x2[k] = max(a2, (T)0) - qe2; d |= a2 > 0 ? 0x20 : 0;
                y2 = max(b2, (T)0) - qe2; d |= b2 > 0 ? 0x40 : 0;
                dd[k] = d;
                if (k == 0 && lane == 0) H += i == 0 ? (int)v[0] - (int)qe : (int)u;
            }
            if (j0 < Q) {
                if (C % 4 == 0) {
                    device uint *row = (device uint *)(p + (size_t)i * stride + j0);
                    for (int k = 0; k < C; k += 4)
                        row[k >> 2] = (uint)dd[k] | (uint)dd[k + 1] << 8 | (uint)dd[k + 2] << 16 | (uint)dd[k + 3] << 24;
                } else {
                    device uchar *row = p + (size_t)i * stride + j0;
                    for (int k = 0; k < C; ++k) row[k] = dd[k];
                }
            }
        }
        carry = (uint)(uchar)u | (uint)(uchar)y << 8 | (uint)(uchar)y2 << 16;
    }
    for (int k = 0; k < C; ++k)
        if (j0 + k < Q) {
            if (wide) wrapped |= lo[k] < -qe || hi[k] > mch ? 1 : 0;
            if (j0 + k > 0) H += v[k]; // v(T-1, j)
        }
    H = simd_sum(H);
    wrapped = simd_max(wrapped);
    simdgroup_barrier(mem_flags::mem_device);
    if (lane != 0) return;
    score[fi] = wrapped ? INT_MIN : H;
    if (wrapped) return;
    device uint *cigar = cigars + F.coff;
    int n = 0, i = Tl - 1, j = Q - 1, state = 0;
    while (i >= 0 && j >= 0) {
        const uint d = p[(size_t)i * stride + j];
        if (state == 0) state = d & 7;
        else if (!(d >> (state + 2) & 1)) state = 0;
        if (state == 0) state = d & 7;
        if (state == 0) push_cigar(cigar, n, 0, 1), --i, --j;
        else if (state == 1 || state == 3) push_cigar(cigar, n, 2, 1), --i;
        else push_cigar(cigar, n, 1, 1), --j;
    }
    if (i >= 0) push_cigar(cigar, n, 2, i + 1);
    if (j >= 0) push_cigar(cigar, n, 1, j + 1);
    n_cigar[fi] = n;
}

#define KSW_FILL(C, T, name) \
kernel void ksw_fill##C##name(device const Fill *fills [[buffer(0)]], constant Scoring &S [[buffer(1)]], \
    device const uchar *seq [[buffer(2)]], device uchar *p [[buffer(3)]], device int *score [[buffer(4)]], \
    device uint *cigars [[buffer(5)]], device int *n_cigar [[buffer(6)]], constant uint &n [[buffer(7)]], \
    uint gid [[thread_position_in_grid]], ushort lane [[thread_index_in_simdgroup]]) \
{ const uint fi = gid / 32; if (fi < n) fill<C, T>(fills[fi], S, seq, p, score, cigars, n_cigar, fi, lane); }
#define KSW_FILLS(C) KSW_FILL(C, char, _8) KSW_FILL(C, short, _16)
KSW_FILLS(4)
KSW_FILLS(7)
KSW_FILLS(8)
KSW_FILLS(12)
KSW_FILLS(16)
)METAL";

// Query columns per lane C for the 32 lanes of a fill: 7 suits HiFi's typical
// fills of 193-224 bases. Longer targets only grow the backtrack matrix.
constexpr int n_widths = 5;
constexpr int widths[n_widths] = {4, 7, 8, 12, 16};
constexpr int max_qlen = 512, max_tlen = 4096;

double now() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

int lane_columns(int qlen) {
    for (int c : widths)
        if (qlen <= 32 * c) return c;
    return 0;
}

size_t matrix_bytes(int qlen, int tlen) {
    const int c = lane_columns(qlen);
    return (size_t)tlen * ((qlen + c - 1) / c * c);
}

// Fibers: each mapping thread runs several reads, switching to another read in
// user space while one waits for the GPU. A fiber stays on the thread that made it.
// wgse_fiber_switch saves the callee-saved registers on the current stack, stores
// sp to *from and resumes the stack at to; wgse_fiber_boot starts a new fiber,
// calling x20(x19).
extern "C" void wgse_fiber_switch(void **from, void *to);
extern "C" void wgse_fiber_boot(void);
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

void fiber_main(Fiber *f) {
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
    bool enabled() const { return enabled_.load(std::memory_order_relaxed); }

    int workers(int n_threads) const { return n_threads * fibers_per_thread_; }
    void run_for(int n_threads, For &job);

    void set_scoring(int q, int e, int q2, int e2, const int8_t mat[25]);
    bool eligible(int qlen, int tlen, int w) const;
    void submit(Fiber &fiber);
    void print_metrics() const;
    bool failed(std::string &reason) const;

    std::atomic<size_t> taken{0}, drains{0};

private:
    struct Buffers {
        id<MTLBuffer> fills, seq, matrix, score, cigar, n_cigar;
        size_t fills_size = 0, seq_size = 0, matrix_size = 0, cigar_size = 0;
    };
    void thread_main(Thread &thread, Fiber *fibers, int n_fibers, For &job);
    bool help(void *km, ksw_extz_t &ez, bool tail);
    void dispatcher();
    bool ready(double now_s) const;
    void run_batch(std::vector<Pending> &batch, Buffers &buffers);
    bool grow(__strong id<MTLBuffer> &buffer, size_t &size, size_t needed, bool private_storage);
    void fail(const std::string &reason);
    void finish(Read &read, size_t fills);

    wgse::metal::Runtime runtime_;
    id<MTLComputePipelineState> pipelines_[2][n_widths] = {}; // [narrow][width]
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
    std::atomic<uint64_t> gpu_ns_{0}, cells_{0}, busy_ns_{0}, idle_ns_{0}, buffer_bytes_{0};
    std::atomic<int> finishing_{0}; // finish() calls that may still touch a read's owner
};

// Created by wgse_ksw_metal_start (--metal) only.
Engine *engine = nullptr;
int tune_fibers = 0, tune_batch = 0, tune_max_cost = -1, tune_fail = 0;

uint64_t nanoseconds(double seconds) { return (uint64_t)(std::max(0.0, seconds) * 1e9); }

bool Engine::start(std::string &reason) {
    if (!runtime_.device()) {
        reason = runtime_.failure();
        return false;
    }
    id<MTLLibrary> library = runtime_.library(kernel_source);
    for (int t = 0; t < 2; ++t)
        for (int k = 0; k < n_widths; ++k) {
            const std::string entry = "ksw_fill" + std::to_string(widths[k]) + (t ? "_16" : "_8");
            pipelines_[t][k] = runtime_.pipeline(library, entry.c_str());
        }
    reason = runtime_.failure();
    if (!reason.empty()) return false;
    if (tune_fibers > 0) fibers_per_thread_ = tune_fibers;
    if (tune_batch > 0) target_fills_ = (size_t)tune_batch;
    if (tune_max_cost >= 0) max_cost_ = tune_max_cost;
    fail_batches_ = tune_fail != 0;
    for (auto &set : pipelines_)
        for (id<MTLComputePipelineState> pipeline : set)
            if (pipeline.maxTotalThreadsPerThreadgroup < 64 || pipeline.threadExecutionWidth != 32) {
                reason = "unsupported Metal SIMD width";
                return false;
            }
    // One dispatcher packs a batch while the other's runs.
    for (int k = 0; k < dispatchers; ++k) std::thread([this] { dispatcher(); }).detach();
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
        // The first switch to the fiber pops this frame: x19 = f, x20 = fiber_main, x30 = boot.
        void **frame = (void **)((char *)m + stack_size + guard - 160);
        memset(frame, 0, 160);
        frame[0] = &f;
        frame[1] = (void *)&fiber_main;
        frame[11] = (void *)&wgse_fiber_boot;
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
        int min_sc = mat[1];
        for (int t = 0; t < 25; ++t) min_sc = std::min(min_sc, (int)mat[t]);
        // The kernel scores only matches, mismatches and N, like ksw_extd2_sse
        // without KSW_EZ_GENERIC_SC, and assumes ksw_extd2_sse's q/e swap is a no-op.
        bool simple = true;
        for (int a = 0; a < 4; ++a)
            for (int b = 0; b < 4; ++b) simple &= mat[a * 5 + b] == (a == b ? mat[0] : mat[1]);
        scoring_ok_ = simple && q + e <= q2 + e2 && -min_sc <= 2 * (q + e) && (q != q2 || e != e2);
        int lt = e != e2 ? (q2 - q) / (e - e2) - 1 : 0;
        if (q2 + e2 + lt * e2 > q + e + lt * e) ++lt;
        scoring_ = {q, e, q2, e2, mat[0], mat[1], mat[24] == 0 ? -e2 : mat[24], 4, lt,
            lt * (e - e2) - (q2 - q) - e2};
        memcpy(mat_, mat, sizeof mat_);
        // The 16-bit kernels equal int8 while z <= M before the cap and u, v >= -A
        // (they check both). Then, with A = q + e, A2 = q2 + e2, M the match score and
        // m the lowest score (z lies in [m, M]), whatever the order of q and q2:
        //   x, y in [-A, -e], x2, y2 in [-A2, -e2], u, v in [-A, M + A];
        //   a = x + v, b = y + u in [-2A, M + A - e]; a2, b2 in [-A - A2, M + A - e2];
        //   z - q in [m - q, M - q], z - q2 in [m - q2, M - q2];
        //   a - (z - q) in [-2A - M + q, q], a2 - (z - q2) in [-A - A2 - M + q2, q2];
        //   the boundary u, v: -A, -e, long_diff, -e2.
        // narrow_ requires all of these to lie in int8, where both kernels agree.
        const int A = q + e, A2 = q2 + e2, M = scoring_.mch, m = std::min(scoring_.mis, scoring_.scN);
        const int ld = scoring_.long_diff;
        const auto in8 = [](int lo, int hi) { return lo >= -128 && hi <= 127; };
        narrow_ = scoring_ok_ && in8(-A, M + A) && in8(-A2, -e2) && in8(-2 * A, M + A - e)
            && in8(-A - A2, M + A - e2) && in8(m - q, M - q) && in8(m - q2, M - q2)
            && in8(-2 * A - M + q, q) && in8(-A - A2 - M + q2, q2) && in8(ld, ld) && ld >= -A && ld <= M + A
            && A + A2 <= 127 && e2 <= A;
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

void Engine::dispatcher() {
    Buffers buffers;
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
        @autoreleasepool { run_batch(batch, buffers); }
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (--in_flight_ == 0) busy_ns_ += nanoseconds(now() - busy_since_);
            work_cv_.notify_all();
        }
    }
}
bool Engine::grow(__strong id<MTLBuffer> &buffer, size_t &size, size_t needed, bool private_storage) {
    if (buffer && size >= needed) return true;
    size_t capacity = std::max<size_t>(needed, 1 << 16);
    capacity = capacity + capacity / 2;
    buffer = private_storage
        ? [runtime_.device() newBufferWithLength:capacity options:MTLResourceStorageModePrivate]
        : runtime_.allocate(capacity);
    buffer_bytes_ += (buffer ? capacity : 0) - size;
    size = buffer ? capacity : 0;
    return buffer != nil;
}

void Engine::run_batch(std::vector<Pending> &batch, Buffers &b) {
    std::vector<uint32_t> order; // entry indices across the batch, grouped by width, longest first
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
    std::vector<Fill> fills(n);
    size_t seq = 0, matrix = 0, cigar = 0;
    for (size_t k = 0; k < n; ++k) {
        const Entry &e = refs[k].read->entries[refs[k].index];
        Fill &f = fills[k];
        f.qlen = e.qlen, f.tlen = e.tlen;
        f.qoff = (uint32_t)seq, seq += e.qlen;
        f.toff = (uint32_t)seq, seq += e.tlen;
        f.poff = (uint32_t)matrix, matrix += (matrix_bytes(e.qlen, e.tlen) + 3) & ~(size_t)3;
        f.coff = (uint32_t)cigar, cigar += e.qlen + e.tlen;
    }
    bool ok = enabled() && grow(b.fills, b.fills_size, n * sizeof(Fill), false)
        && grow(b.seq, b.seq_size, seq, false) && grow(b.matrix, b.matrix_size, matrix, true)
        && grow(b.cigar, b.cigar_size, cigar * 4, false);
    if (ok && (!b.score || b.score.length < n * 4)) {
        b.score = runtime_.allocate(std::max<size_t>(n, target_fills_) * 4);
        b.n_cigar = runtime_.allocate(std::max<size_t>(n, target_fills_) * 4);
        ok = b.score && b.n_cigar;
    }
    if (!ok && enabled()) fail("Metal buffer allocation failed");
    if (ok) {
        memcpy(b.fills.contents, fills.data(), n * sizeof(Fill));
        uint8_t *s = (uint8_t *)b.seq.contents;
        for (size_t k = 0; k < n; ++k) {
            const Read &r = *refs[k].read;
            const Entry &e = r.entries[refs[k].index];
            memcpy(s + fills[k].qoff, r.seq.data() + e.qoff, e.qlen);
            memcpy(s + fills[k].toff, r.seq.data() + e.toff, e.tlen);
        }
        uint64_t cells = 0;
        for (const Fill &f : fills) cells += (uint64_t)f.qlen * f.tlen;
        for (int attempt = 1;; ++attempt) {
            id<MTLCommandBuffer> command = [runtime_.queue() commandBuffer];
            id<MTLComputeCommandEncoder> encoder =
                [command computeCommandEncoderWithDispatchType:MTLDispatchTypeConcurrent];
            if (!command || !encoder) {
                fail("Metal command allocation failed");
                ok = false;
                break;
            }
            command.label = encoder.label = @"ksw_fill";
            [encoder setBytes:&scoring_ length:sizeof scoring_ atIndex:1];
            [encoder setBuffer:b.seq offset:0 atIndex:2];
            [encoder setBuffer:b.matrix offset:0 atIndex:3];
            for (size_t first = 0, k = 0; k < n_widths; ++k) {
                size_t last = first;
                while (last < n && refs[last].c == widths[k]) ++last;
                const uint32_t count = (uint32_t)(last - first);
                if (count) {
                    [encoder setComputePipelineState:pipelines_[narrow_][k]];
                    [encoder setBuffer:b.fills offset:first * sizeof(Fill) atIndex:0];
                    [encoder setBuffer:b.score offset:first * 4 atIndex:4];
                    [encoder setBuffer:b.cigar offset:0 atIndex:5];
                    [encoder setBuffer:b.n_cigar offset:first * 4 atIndex:6];
                    [encoder setBytes:&count length:4 atIndex:7];
                    [encoder dispatchThreads:MTLSizeMake(count * 32, 1, 1)
                        threadsPerThreadgroup:MTLSizeMake(64, 1, 1)];
                }
                first = last;
            }
            [encoder endEncoding];
            wgse::metal::Runtime::run(command);
            if (attempt < wgse::metal::Runtime::attempts
                && wgse::metal::Runtime::interrupted(command, attempt))
                continue;
            if (command.status == MTLCommandBufferStatusCompleted && !fail_batches_) {
                gpu_ns_ += nanoseconds(command.GPUEndTime - command.GPUStartTime);
                cells_ += cells;
                ++batches_;
            } else {
                fail(fail_batches_ ? std::string("injected GPU failure")
                    : wgse::metal::Runtime::describe(command));
                ok = false;
            }
            break;
        }
    }
    const int *score = ok ? (const int *)b.score.contents : nullptr;
    const int *n_cigar = ok ? (const int *)b.n_cigar.contents : nullptr;
    const uint32_t *cigars = ok ? (const uint32_t *)b.cigar.contents : nullptr;
    size_t gpu = 0, wrapped = 0;
    for (size_t k = 0; k < n; ++k) {
        Read &r = *refs[k].read;
        Entry &e = r.entries[refs[k].index];
        if (!ok || score[k] == INT_MIN) {
            e.score = INT_MIN;
            wrapped += ok;
            continue;
        }
        e.score = score[k];
        e.n_cigar = n_cigar[k];
        const uint32_t *src = cigars + fills[k].coff;
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
    fprintf(stderr, "[wgse-minimap2] Metal gap fills: %zu queued, %zu on the GPU (%zu used), "
        "%zu by idle CPU threads, %zu redone on the CPU (int8 overflow); %s kernels; %zu batches of %.0f fills, "
        "GPU %.2f s (%.2f G cells/s), batches in flight %.2f s, threads idle %.2f s; "
        "%.0f MB of GPU buffers; %zu Metal interruptions; %zu arena drains\n",
        queued_total_.load(), gpu_fills_.load(), taken.load(), helped_.load(), wrapped_.load(), narrow_ ? "16-bit" : "8-bit", batches,
        batches ? (double)gpu_fills_.load() / batches : 0.0, gpu,
        gpu > 0 ? cells_.load() / gpu / 1e9 : 0.0, busy_ns_.load() * 1e-9, idle_ns_.load() * 1e-9,
        buffer_bytes_.load() / 1048576.0, wgse::metal::Runtime::interruptions(), drains.load());
}

} // namespace

extern "C" {

void wgse_ksw_tune(int fibers_per_thread, int batch_fills, int max_cost, int fail_batches) {
    tune_fibers = fibers_per_thread, tune_batch = batch_fills, tune_max_cost = max_cost;
    tune_fail = fail_batches;
}

int wgse_ksw_metal_start(char *reason, int reason_size) {
    std::string why;
    if (!engine) engine = new Engine;
    if (engine->start(why)) return 1;
    delete engine;
    engine = nullptr;
    snprintf(reason, reason_size, "%s", why.c_str());
    return 0;
}

int wgse_ksw_metal_enabled(void) { return engine && engine->enabled() && current_fiber; }

int wgse_ksw_gpu_hardware_supported(void) {
    wgse::metal::Runtime runtime;
    return runtime.device() != nil;
}

void wgse_ksw_inject_interruptions(int count) {
    wgse::metal::Runtime::inject_interruptions((size_t)count);
}

void wgse_ksw_inject_hang(const char *label, double timeout) {
    wgse::metal::Runtime::inject_hang(label, timeout);
}

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

int wgse_ksw_metal_failed(char *reason, int reason_size) {
    std::string why;
    if (!engine || !engine->failed(why)) return 0;
    snprintf(reason, reason_size, "%s", why.c_str());
    return 1;
}

} // extern "C"
