// The CPU backend without Metal (Linux): the exact CPU replacements of
// metal_backend.mm (interleaved seeding and suffix-array locate, batched mate
// rescue), with every GPU seam inert. PROTOTYPE for #671: duplicates the CPU
// parts of metal_backend.mm until they are split out of it.
#include "metal_backend.h"
#include "smem_batch.h"

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

extern "C" void kt_for(int n_threads, void (*func)(void *, long, int), void *data, long n);

namespace {
using Intervals = std::vector<bwtintv_t>;

bool cpu_upstream = false;
bool cpu_rescue_batching = true;
size_t cpu_rescue_bytes = size_t(4) << 20;

inline int stride(const mem_opt_t *opt) { return (opt->flag & MEM_F_PE) ? 2 : 1; }

inline size_t row_count(const bwtintv_t *begin, const bwtintv_t *end, int max_occ) {
    size_t total = 0;
    for (const bwtintv_t *p = begin; p != end; ++p)
        total += p->x[2] > static_cast<uint64_t>(max_occ) ? static_cast<size_t>(max_occ)
            : static_cast<size_t>(p->x[2]);
    return total;
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

struct Prepared {
    const Intervals *intervals;
    const uint64_t *rows, *positions;
    size_t count;
};
struct Current {
    const uint8_t *seq[2];
    const Prepared *prepared[2];
};
thread_local Current current;
struct Cursor {
    const uint64_t *rows = nullptr, *positions = nullptr;
    size_t next = 0, count = 0;
};
thread_local Cursor cursor;
std::atomic<size_t> locate_misses{0}, cpu_located_rows{0};

void finish_cursor() {
    if (cursor.next < cursor.count) locate_misses.fetch_add(1, std::memory_order_relaxed);
    cursor = {};
}

inline bwtint_t inverse_psi(const bwt_t *bwt, bwtint_t k) {
    const bwtint_t x = bwt_B0(bwt, k - (k > bwt->primary));
    return k == bwt->primary ? 0 : bwt->L2[x] + bwt_occ(bwt, k, ubyte_t(x));
}

inline void prefetch_occ(const bwt_t *bwt, bwtint_t k) {
    __builtin_prefetch(bwt_occ_intv(bwt, k - (k > bwt->primary)));
}

void locate_rows(const bwt_t *bwt, const uint64_t *rows, uint64_t *positions, size_t count) {
    struct Walk {
        bwtint_t k, steps;
        size_t index;
    };
    constexpr size_t ways = 16;
    Walk walks[ways];
    const bwtint_t mask = bwtint_t(bwt->sa_intv) - 1;
    size_t next = 0, active = 0;
    for (; active < ways && next < count; ++active, ++next) {
        walks[active] = {rows[next], 0, next};
        prefetch_occ(bwt, rows[next]);
    }
    while (active) {
        for (size_t i = 0; i < active;) {
            Walk &walk = walks[i];
            if (walk.k & mask) {
                ++walk.steps;
                walk.k = inverse_psi(bwt, walk.k);
                if (walk.k & mask) prefetch_occ(bwt, walk.k);
                ++i;
                continue;
            }
            positions[walk.index] = walk.steps + bwt->sa[walk.k / bwtint_t(bwt->sa_intv)];
            if (next < count) {
                walk = {rows[next], 0, next};
                prefetch_occ(bwt, rows[next]);
                ++next;
                ++i;
            } else {
                walk = walks[--active];
            }
        }
    }
}

thread_local std::vector<uint64_t> cpu_rows, cpu_positions;
constexpr size_t cpu_locate_max_rows = size_t(1) << 20;

void locate_on_cpu(const bwt_t *bwt, const mem_opt_t *opt, const bwtintv_t *begin,
    const bwtintv_t *end) {
    if (opt->max_occ <= 0) return;
    const size_t bound = row_count(begin, end, opt->max_occ);
    if (bound < 2 || bound > cpu_locate_max_rows) return;
    cpu_rows.clear();
    append_rows(begin, end, opt->max_occ, cpu_rows);
    const size_t count = cpu_rows.size();
    cpu_positions.resize(count);
    locate_rows(bwt, cpu_rows.data(), cpu_positions.data(), count);
    cpu_located_rows.fetch_add(count, std::memory_order_relaxed);
    cursor = {cpu_rows.data(), cpu_positions.data(), 0, count};
}

struct CpuMap {
    const mem_opt_t *opt;
    const bwt_t *bwt;
    bseq1_t *seqs;
    void (*worker)(void *, long, int);
    void *data;
    long items;
    int stride;
};
constexpr long cpu_map_block = 8;

struct CpuMapScratch {
    std::vector<Intervals> intervals;
    std::vector<Prepared> prepared;
    std::vector<uint64_t> rows, positions;
    std::vector<size_t> offsets;
    std::vector<wgse::smem::Read> walks;
};
thread_local CpuMapScratch cpu_map_scratch;

void cpu_map_worker(void *context, long block, int tid) {
    const CpuMap &m = *static_cast<const CpuMap *>(context);
    CpuMapScratch &s = cpu_map_scratch;
    const long first = block * cpu_map_block, last = std::min(m.items, first + cpu_map_block);
    const size_t reads = size_t(last - first) * size_t(m.stride);
    s.intervals.resize(reads);
    s.prepared.assign(reads, Prepared{nullptr, nullptr, nullptr, 0});
    s.rows.clear();
    s.walks.clear();
    for (size_t r = 0; r < reads; ++r) {
        bseq1_t &seq = m.seqs[size_t(first) * size_t(m.stride) + r];
        if (seq.l_seq < m.opt->min_seed_len) continue;
        uint8_t *bases = reinterpret_cast<uint8_t *>(seq.seq);
        for (int i = 0; i < seq.l_seq; ++i)
            bases[i] = bases[i] < 4 ? bases[i] : nst_nt4_table[bases[i]];
        s.walks.push_back({bases, seq.l_seq, &s.intervals[r]});
        s.prepared[r].intervals = &s.intervals[r];
    }
    wgse::smem::collect(m.opt, m.bwt, s.walks.data(), s.walks.size());
    std::vector<size_t> &offsets = s.offsets;
    offsets.assign(reads + 1, 0);
    for (size_t r = 0; r < reads; ++r) {
        offsets[r] = s.rows.size();
        if (!s.prepared[r].intervals) continue;
        const bwtintv_t *p = s.intervals[r].data(), *end = p + s.intervals[r].size();
        const size_t bound = row_count(p, end, m.opt->max_occ);
        if (m.opt->max_occ > 0 && bound && bound <= cpu_locate_max_rows)
            append_rows(p, end, m.opt->max_occ, s.rows);
    }
    offsets[reads] = s.rows.size();
    s.positions.resize(s.rows.size());
    locate_rows(m.bwt, s.rows.data(), s.positions.data(), s.rows.size());
    cpu_located_rows.fetch_add(s.rows.size(), std::memory_order_relaxed);
    for (size_t r = 0; r < reads; ++r) {
        if (offsets[r + 1] == offsets[r]) continue;
        s.prepared[r].rows = s.rows.data() + offsets[r];
        s.prepared[r].positions = s.positions.data() + offsets[r];
        s.prepared[r].count = offsets[r + 1] - offsets[r];
    }
    for (long item = first; item < last; ++item) {
        current = {};
        for (int mate = 0; mate < m.stride; ++mate) {
            const size_t r = size_t(item - first) * size_t(m.stride) + size_t(mate);
            if (!s.prepared[r].intervals) continue;
            current.seq[mate] = reinterpret_cast<const uint8_t *>(
                m.seqs[size_t(item) * size_t(m.stride) + size_t(mate)].seq);
            current.prepared[mate] = &s.prepared[r];
        }
        m.worker(m.data, item, tid);
        finish_cursor();
    }
    current = {};
}

// Mate rescue: a pair's windows aligned together (wgse_ksw_align2_batch).
struct RescueKey {
    const uint8_t *ms;
    int64_t rb, re;
    int l_ms, is_rev, xtra;
    uint32_t result;
};
constexpr uint32_t skipped_job = UINT32_MAX;

struct CpuRescue {
    std::vector<RescueKey> keys;
    std::vector<uint8_t> bases;
    std::vector<wgse_ksw_task_t> tasks;
    std::vector<size_t> offsets;
    bool active = false;
    size_t jobs = 0, hits = 0, skipped = 0;
};
thread_local CpuRescue cpu_rescue;
std::atomic<size_t> cpu_backend_jobs{0}, cpu_backend_hits{0}, cpu_backend_skipped{0};

void emit_cpu(void *context, const uint8_t *ms, int l_ms, int is_rev, int64_t rb,
    int64_t re, const uint8_t *ref, int xtra) {
    auto &cpu = *static_cast<CpuRescue *>(context);
    const int64_t tlen = re - rb;
    if (!(xtra & KSW_XBYTE)
        || cpu.bases.size() + size_t(l_ms) + size_t(tlen) > cpu_rescue_bytes) {
        cpu.keys.push_back({ms, rb, re, l_ms, is_rev, xtra, skipped_job});
        ++cpu.skipped;
        return;
    }
    const size_t query = cpu.bases.size(), target = query + size_t(l_ms);
    cpu.bases.resize(target + size_t(tlen));
    uint8_t *bases = cpu.bases.data();
    for (int i = 0; i < l_ms; ++i) {
        if (is_rev) bases[query + size_t(l_ms - 1 - i)] = ms[i] < 4 ? 3 - ms[i] : 4;
        else bases[query + size_t(i)] = ms[i];
    }
    memcpy(bases + target, ref, size_t(tlen));
    cpu.keys.push_back({ms, rb, re, l_ms, is_rev, xtra, uint32_t(cpu.tasks.size())});
    cpu.tasks.push_back({l_ms, int(tlen), xtra, nullptr, nullptr, {}});
    cpu.offsets.push_back(query);
}

void align_on_cpu(const mem_opt_t *opt, const bntseq_t *bns, const uint8_t *pac,
    const mem_pestat_t *pes, const bseq1_t *seqs, const mem_alnreg_v *regs, long pair) {
    CpuRescue &cpu = cpu_rescue;
    cpu.keys.clear();
    cpu.bases.clear();
    cpu.tasks.clear();
    cpu.offsets.clear();
    cpu.active = false;
    wgse_rescue_candidates(opt, bns, pac, pes, seqs + 2 * pair, regs + 2 * pair, emit_cpu, &cpu);
    if (cpu.tasks.size() < 2) return;
    for (size_t i = 0; i < cpu.tasks.size(); ++i) {
        cpu.tasks[i].query = cpu.bases.data() + cpu.offsets[i];
        cpu.tasks[i].target = cpu.tasks[i].query + cpu.tasks[i].qlen;
    }
    wgse_ksw_align2_batch(int(cpu.tasks.size()), cpu.tasks.data(), 5, opt->mat, opt->o_del,
        opt->e_del, opt->o_ins, opt->e_ins);
    cpu.jobs += cpu.tasks.size();
    cpu.active = true;
}

struct CpuPair {
    const mem_opt_t *opt;
    const bntseq_t *bns;
    const uint8_t *pac;
    const mem_pestat_t *pes;
    const bseq1_t *seqs;
    const mem_alnreg_v *regs;
    void (*worker)(void *, long, int);
    void *data;
};

void cpu_pair_worker(void *context, long pair, int tid) {
    const CpuPair &c = *static_cast<const CpuPair *>(context);
    CpuRescue &cpu = cpu_rescue;
    align_on_cpu(c.opt, c.bns, c.pac, c.pes, c.seqs, c.regs, pair);
    c.worker(c.data, pair, tid);
    cpu.active = false;
    if (cpu.jobs) cpu_backend_jobs.fetch_add(cpu.jobs, std::memory_order_relaxed);
    if (cpu.hits) cpu_backend_hits.fetch_add(cpu.hits, std::memory_order_relaxed);
    if (cpu.skipped) cpu_backend_skipped.fetch_add(cpu.skipped, std::memory_order_relaxed);
    cpu.jobs = cpu.hits = cpu.skipped = 0;
}
} // namespace

extern "C" void *wgse_metal_calloc(size_t count, size_t size) { return calloc(count, size); }
extern "C" int wgse_metal_enabled(void) { return 0; }
extern "C" int wgse_fast_cpu(void) { return !cpu_upstream; }

extern "C" int wgse_metal_map(const mem_opt_t *opt, const bwt_t *bwt, int n, bseq1_t *seqs,
    void (*worker)(void *, long, int), void *data) {
    if (cpu_upstream || bwa_verbose >= 4) return 0;
    CpuMap m{opt, bwt, seqs, worker, data, long(n / stride(opt)), stride(opt)};
    kt_for(opt->n_threads, cpu_map_worker, &m, (m.items + cpu_map_block - 1) / cpu_map_block);
    return 1;
}

extern "C" int wgse_metal_pair(const mem_opt_t *opt, const bntseq_t *bns, const uint8_t *pac,
    const mem_pestat_t pes[4], int n, bseq1_t *seqs, mem_alnreg_v *regs,
    void (*worker)(void *, long, int), void *data) {
    if (cpu_upstream || !cpu_rescue_batching || !(opt->flag & MEM_F_PE)
        || (opt->flag & MEM_F_NO_RESCUE)) return 0;
    CpuPair pair{opt, bns, pac, pes, seqs, regs, worker, data};
    kt_for(opt->n_threads, cpu_pair_worker, &pair, n >> 1);
    return 1;
}

extern "C" int wgse_metal_matesw(const mem_opt_t *, const uint8_t *ms, uint8_t *, int l_ms,
    uint8_t *, int is_rev, int64_t rb, int64_t re, int xtra, kswr_t *aln) {
    CpuRescue &cpu = cpu_rescue;
    if (!cpu.active) return 0;
    for (const RescueKey &key : cpu.keys) {
        if (key.ms != ms || key.rb != rb || key.re != re || key.l_ms != l_ms
            || key.is_rev != is_rev || key.xtra != xtra) continue;
        if (key.result == skipped_job) return 0;
        *aln = cpu.tasks[key.result].r;
        ++cpu.hits;
        return 1;
    }
    return 0;
}

extern "C" int wgse_metal_extend(int, const uint8_t *, int, const uint8_t *, int, int, int, int *,
    int *, int *, int *, int *, int *) {
    return 0;
}

extern "C" int wgse_metal_take(const mem_opt_t *opt, const bwt_t *bwt, const uint8_t *seq,
    bwtintv_v *out) {
    finish_cursor();
    const Prepared *entry = current.seq[0] == seq ? current.prepared[0]
        : current.seq[1] == seq ? current.prepared[1] : nullptr;
    if (!entry) return 0;
    const auto &intervals = *entry->intervals;
    if (out->m < intervals.size()) {
        auto *storage = static_cast<bwtintv_t *>(
            realloc(out->a, intervals.size() * sizeof(bwtintv_t)));
        if (!storage) return 0;
        out->a = storage;
        out->m = intervals.size();
    }
    out->n = intervals.size();
    if (!intervals.empty()) memcpy(out->a, intervals.data(), intervals.size() * sizeof(bwtintv_t));
    if (entry->rows) cursor = {entry->rows, entry->positions, 0, entry->count};
    else locate_on_cpu(bwt, opt, intervals.data(), intervals.data() + intervals.size());
    return 1;
}

extern "C" void wgse_metal_locate(const mem_opt_t *opt, const bwt_t *bwt, const bwtintv_v *mem) {
    if (!cpu_upstream) locate_on_cpu(bwt, opt, mem->a, mem->a + mem->n);
}

extern "C" bwtint_t wgse_metal_sa(const bwt_t *bwt, bwtint_t k) {
    if (cursor.rows) {
        if (cursor.next < cursor.count && cursor.rows[cursor.next] == k)
            return cursor.positions[cursor.next++];
        locate_misses.fetch_add(1, std::memory_order_relaxed);
        cursor = {};
    }
    return bwt_sa(bwt, k);
}

bool wgse_metal_available(std::string &reason) {
    reason = "this build has no Metal backend";
    return false;
}
bool wgse_metal_hardware_supported() { return false; }
void wgse_set_backend(Backend) {}
bool wgse_metal_execution_failed(std::string &reason) {
    reason.clear();
    return false;
}
void wgse_finish_backend() {}
void wgse_report_cpu_backend() {
    fprintf(stderr, "[wgse-bwa] cpu_located_rows=%zu locate_misses=%zu rescue_cpu_jobs=%zu "
        "rescue_cpu_hits=%zu rescue_cpu_skipped=%zu\n", cpu_located_rows.load(),
        locate_misses.load(), cpu_backend_jobs.load(), cpu_backend_hits.load(),
        cpu_backend_skipped.load());
}
void wgse_set_cpu_upstream() { cpu_upstream = true; }
void wgse_set_cpu_rescue_batching(bool enable) { cpu_rescue_batching = enable; }
void wgse_set_cpu_rescue_bytes(size_t bytes) { cpu_rescue_bytes = bytes; }
// The GPU test knobs have nothing to set without Metal.
void wgse_set_gpu_policy(bool) {}
void wgse_set_gpu_batch(size_t) {}
void wgse_inject_gpu_failure() {}
void wgse_inject_locate_failure() {}
void wgse_inject_gpu_interruptions(size_t) {}
void wgse_set_gpu_locate(bool) {}
void wgse_set_gpu_locate_dense(unsigned) {}
void wgse_set_gpu_locate_dense_after(size_t) {}
void wgse_set_gpu_locate_dense_slice(size_t) {}
void wgse_set_gpu_locate_rows(size_t) {}
void wgse_set_gpu_rescue(bool) {}
void wgse_set_gpu_rescue_batch(size_t) {}
void wgse_inject_rescue_failure() {}
void wgse_verify_gpu_rescue() {}
void wgse_set_gpu_extend(bool) {}
void wgse_set_gpu_extend_pairs(size_t) {}
void wgse_set_gpu_extend_batches(size_t) {}
size_t wgse_gpu_extend_pool_pairs() { return 0; }
void wgse_inject_extend_failure() {}
void wgse_verify_gpu_extend() {}
