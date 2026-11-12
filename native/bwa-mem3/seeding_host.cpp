#include "bwamem.h"
#include "FMI_search.h"
#include "smem_dedup.h"
#include "utils.h"

#include "metal_seeding.h"
#include "seeding_device.h"
#include "seeding_types.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <deque>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

SMEM *mem_collect_smem(FMI_search *fmi, const mem_opt_t *opt, const bseq1_t *seq_, int nseq,
    SMEM *matchArray, int32_t *min_intv_ar, int32_t *query_pos_ar, uint8_t *enc_qdb, int32_t *rid,
    mem_cache *mmc, int64_t &tot_smem, int tid);

namespace {

static_assert(sizeof(SeedParameters) == 80 && sizeof(SeedSmem) == 32, "seeding_types.h");
static_assert(sizeof(SeedRead) == 8 && sizeof(SeedCount) == 20, "seeding_types.h");
// Longer reads stay on the CPU, as in wgse-bwa-mem2.
constexpr uint32_t max_length = 512;
// SMEMs per read on the GPU; a read with more goes to the CPU.
constexpr uint32_t capacity = 128;
// Reads per GPU batch.
constexpr int batch_reads = 8192;

using Clock = std::chrono::steady_clock;

std::atomic<bool> enabled{false}, usable{true}, verify{false}, wait_for_batches{false},
    inject_failure{false};
std::mutex stats_mutex;
std::string failure_reason;
// A batch failed on the GPU (not setup, such as an index larger than the GPU's buffers).
bool batch_failed = false;
uint64_t chunks = 0, reads_total = 0, gpu_reads = 0, cpu_reads = 0, long_reads = 0, overflow_reads = 0,
    hits = 0, misses = 0, mismatches = 0, batches_run = 0, batches_late = 0;
double gpu_seconds = 0;

void fail(const std::string &reason, bool batch) {
    std::lock_guard<std::mutex> lock(stats_mutex);
    if (!usable.exchange(false)) return;
    failure_reason = reason;
    batch_failed = batch;
}

// Read i of a batch: its SMEMs are smems[offsets[i]..] if counts[i].status == seed_ok.
struct Batch {
    std::vector<SeedCount> counts;
    std::vector<uint32_t> offsets;
    std::vector<SeedSmem> smems;
    std::atomic<bool> ready{false};
};

// A chunk of reads, seeded on the GPU a batch at a time from the time it is read until it
// starts to map; batches in flight then may still finish in time for their reads.
struct Chunk {
    const bseq1_t *seqs = nullptr;
    int n = 0;
    SeedParameters parameters{};
    std::unique_ptr<Batch[]> batches;
    int batch_count = 0;
    // Guarded by the scheduler's mutex.
    int next = 0, in_flight = 0;
    // Guarded by pack_mutex: once set, the reads' bases are no longer read (they are about to
    // be encoded in place, and later freed).
    bool stopped = false;
    std::mutex pack_mutex;
};

class Seeder {
public:
    // FMI_search keeps ownership of its occurrence table, and frees it after the Seeder
    // (wgse_mem3_seed_release).
    explicit Seeder(FMI_search *fmi)
        : fmi_(fmi), device_(make_seeding_device(fmi->wgse_cp_occ(),
              size_t((fmi->reference_seq_len >> CP_SHIFT) + 1) * sizeof(CP_OCC),
              SeedingLimits{size_t(batch_reads), max_length, capacity})) {
        thread_ = std::thread([this] { loop(); });
    }

    ~Seeder() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            quit_ = true;
        }
        changed_.notify_all();
        thread_.join();
    }

    FMI_search *fmi() const { return fmi_; }

    void add(std::shared_ptr<Chunk> chunk) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            pending_.push_back(std::move(chunk));
        }
        changed_.notify_all();
    }

    // The registered chunk for these reads, if any. It stops, as do the older ones (whose reads
    // mapped by another path), so no reads are read once they map. With only_this, older
    // chunks keep going.
    std::shared_ptr<Chunk> take(const bseq1_t *seqs, int n, bool only_this) {
        std::shared_ptr<Chunk> found;
        std::vector<std::shared_ptr<Chunk>> stopping;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            for (size_t i = 0; i < pending_.size(); ++i) {
                if (pending_[i]->seqs != seqs || (n >= 0 && pending_[i]->n != n)) continue;
                found = pending_[i];
                const size_t begin = only_this ? i : 0;
                stopping.assign(pending_.begin() + long(begin), pending_.begin() + long(i + 1));
                pending_.erase(pending_.begin() + long(begin), pending_.begin() + long(i + 1));
                break;
            }
        }
        for (const auto &chunk : stopping) {
            std::lock_guard<std::mutex> lock(chunk->pack_mutex);
            chunk->stopped = true;
        }
        return found;
    }

    // Waits for the chunk's GPU batches.
    void drain(Chunk &chunk) {
        std::unique_lock<std::mutex> lock(mutex_);
        changed_.wait(lock, [&chunk] { return chunk.in_flight == 0; });
    }

    // Waits until every batch of the registered chunk for these reads, if any, has run.
    void finish(const bseq1_t *seqs, int n) {
        std::unique_lock<std::mutex> lock(mutex_);
        changed_.wait(lock, [&] {
            if (!usable) return true;
            for (const auto &c : pending_)
                if (c->seqs == seqs && c->n == n) return c->next == c->batch_count && c->in_flight == 0;
            return true;
        });
    }

private:
    FMI_search *fmi_;
    std::unique_ptr<SeedingDevice> device_;
    std::mutex mutex_;
    std::condition_variable changed_;
    std::deque<std::shared_ptr<Chunk>> pending_;
    bool quit_ = false;
    std::thread thread_;

    void loop() {
        for (;;) {
            std::shared_ptr<Chunk> chunk;
            int b = 0;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                changed_.wait(lock, [this] {
                    if (quit_) return true;
                    for (const auto &c : pending_) if (c->next < c->batch_count) return true;
                    return false;
                });
                if (quit_) return;
                for (const auto &c : pending_)
                    if (c->next < c->batch_count) {
                        chunk = c;
                        break;
                    }
                b = chunk->next++;
                ++chunk->in_flight;
            }
            try {
                run(*chunk, b);
            } catch (const std::exception &error) {
                fail(error.what(), true);
            }
            {
                std::lock_guard<std::mutex> lock(mutex_);
                --chunk->in_flight;
                if (!usable) {
                    for (const auto &c : pending_) c->next = c->batch_count;
                }
            }
            changed_.notify_all();
            if (!usable) {
                std::unique_lock<std::mutex> lock(mutex_);
                changed_.wait(lock, [this] { return quit_; });
                return;
            }
        }
    }

    void run(Chunk &chunk, int b) {
        const int first = b * batch_reads, last = std::min(chunk.n, first + batch_reads);
        const size_t count = size_t(last - first);
        Batch &batch = chunk.batches[b];
        // Once the watchdog gives up, the GPU is not used again.
        const std::string hang = device_->hang();
        if (!hang.empty()) throw std::runtime_error(hang);
        if (inject_failure) throw std::runtime_error("injected GPU seeding failure (test)");
        std::vector<uint8_t> declined(count);
        uint32_t longest = 1;
        size_t total_bases = 0;
        {
            std::lock_guard<std::mutex> lock(chunk.pack_mutex);
            if (chunk.stopped) {
                std::lock_guard<std::mutex> stats(stats_mutex);
                ++batches_late;
                return;
            }
            for (int r = first; r < last; ++r) {
                const int length = chunk.seqs[r].l_seq;
                const bool fits = length >= 0 && uint32_t(length) <= max_length;
                declined[size_t(r - first)] = !fits;
                if (fits) {
                    total_bases += size_t(length);
                    longest = std::max(longest, uint32_t(length));
                }
            }
            uint8_t *bases = device_->bases(total_bases);
            SeedRead *reads = device_->reads(count);
            size_t offset = 0;
            for (size_t i = 0; i < count; ++i) {
                const bseq1_t &read = chunk.seqs[first + int(i)];
                const uint32_t length = declined[i] ? 0 : uint32_t(read.l_seq);
                for (uint32_t j = 0; j < length; ++j)
                    bases[offset + j] = nst_nt4_decode(uint8_t(read.seq[j]), 4);
                reads[i] = {uint32_t(offset), length};
                offset += length;
            }
        }
        SeedParameters p = chunk.parameters;
        p.reads = uint32_t(count);
        p.scratch = longest;
        p.capacity = capacity;
        const double elapsed = device_->collect(p, total_bases);
        const SeedCount *counts = device_->counts();
        const SeedSmem *smems = device_->smems();
        batch.counts.resize(count);
        batch.offsets.resize(count);
        size_t gpu = 0;
        for (size_t i = 0; i < count; ++i) {
            SeedCount c = counts[i];
            const size_t n = size_t(c.pass[0]) + c.pass[1] + c.pass[2];
            if (c.status == seed_ok && n > p.capacity) throw std::runtime_error("invalid GPU SMEM count");
            if (c.status != seed_ok && c.status != seed_overflow)
                throw std::runtime_error("invalid GPU seeding status");
            if (declined[i]) c.status = seed_declined;
            batch.counts[i] = c;
            batch.offsets[i] = uint32_t(batch.smems.size());
            if (c.status != seed_ok) continue;
            batch.smems.insert(batch.smems.end(), smems + i * p.capacity, smems + i * p.capacity + n);
            ++gpu;
        }
        batch.ready.store(true, std::memory_order_release);
        std::lock_guard<std::mutex> lock(stats_mutex);
        gpu_seconds += elapsed;
        gpu_reads += gpu;
        ++batches_run;
    }
};

std::mutex seeder_mutex;
std::unique_ptr<Seeder> seeder;
// The chunk being mapped, between wgse_mem3_seed_chunk and wgse_mem3_seed_chunk_done.
std::shared_ptr<Chunk> current;
std::mutex current_mutex;

SeedParameters parameters(FMI_search *fmi, const mem_opt_t *opt) {
    SeedParameters p{};
    std::copy_n(fmi->wgse_count(), 5, p.count);
    p.sentinel = fmi->sentinel_index;
    p.min_seed_len = opt->min_seed_len;
    p.split_len = int(opt->min_seed_len * opt->split_factor + .499);
    p.split_width = opt->split_width;
    p.max_mem_intv = int32_t(opt->max_mem_intv);
    p.third_pass = opt->max_mem_intv > 0;
    return p;
}

thread_local bool upstream_collect = false;

// Upstream's SMEMs for these reads, with upstream's per-read order, tags and deduplication.
const SMEM *cpu_collect(FMI_search *fmi, const mem_opt_t *opt, const bseq1_t *seq, int nseq,
    mem_cache *mmc, int tid, int64_t &total) {
    upstream_collect = true;
    total = 0;
    const SMEM *smems = mem_collect_smem(fmi, opt, seq, nseq, mmc->matchArray[tid],
        mmc->min_intv_ar[tid], mmc->query_pos_ar[tid], mmc->enc_qdb[tid], mmc->rid[tid], mmc, total, tid);
    upstream_collect = false;
    return smems;
}

bool same_smem(const SMEM &a, const SMEM &b) {
    return a.rid == b.rid && a.m == b.m && a.n == b.n && a.k == b.k && a.s == b.s;
}

} // namespace

void wgse_mem3_seed_set_enabled(bool value) { enabled = value; }
void wgse_mem3_seed_set_verify(bool value) { verify = value; }
void wgse_mem3_seed_set_wait(bool value) { wait_for_batches = value; }
void wgse_mem3_seed_inject_failure() { inject_failure = true; }

void wgse_mem3_seed_preseed(FMI_search *fmi, const mem_opt_t *opt, const bseq1_t *seqs, int n) {
    if (!enabled || !usable || n <= 0 || opt->meth_mode) return;
    Seeder *s;
    try {
        std::lock_guard<std::mutex> lock(seeder_mutex);
        if (!seeder || seeder->fmi() != fmi) {
            seeder.reset();
            seeder = std::make_unique<Seeder>(fmi);
        }
        s = seeder.get();
    } catch (const std::exception &error) {
        fail(error.what(), false);
        return;
    }
    auto chunk = std::make_shared<Chunk>();
    chunk->seqs = seqs;
    chunk->n = n;
    chunk->parameters = parameters(fmi, opt);
    chunk->batch_count = (n + batch_reads - 1) / batch_reads;
    chunk->batches.reset(new Batch[size_t(chunk->batch_count)]);
    s->add(std::move(chunk));
}

void wgse_mem3_seed_chunk(FMI_search *, const mem_opt_t *, const bseq1_t *seqs, int n) {
    std::shared_ptr<Chunk> chunk;
    {
        std::lock_guard<std::mutex> lock(seeder_mutex);
        if (seeder && wait_for_batches) seeder->finish(seqs, n);
        if (seeder) chunk = seeder->take(seqs, n, false);
    }
    std::lock_guard<std::mutex> lock(current_mutex);
    current = chunk;
    if (chunk) {
        std::lock_guard<std::mutex> stats(stats_mutex);
        ++chunks;
        reads_total += uint64_t(n);
    }
}

void wgse_mem3_seed_forget(const bseq1_t *seqs) {
    std::shared_ptr<Chunk> chunk;
    {
        std::lock_guard<std::mutex> lock(seeder_mutex);
        if (seeder) chunk = seeder->take(seqs, -1, true);
        if (chunk) seeder->drain(*chunk);
    }
}

bool wgse_mem3_seed_release(const FMI_search *fmi) {
    std::lock_guard<std::mutex> lock(seeder_mutex);
    if (!seeder || seeder->fmi() != fmi) return false;
    seeder.reset();
    return seeding_device_unsettled();
}

void wgse_mem3_seed_chunk_done(const bseq1_t *seqs) {
    std::shared_ptr<Chunk> chunk;
    {
        std::lock_guard<std::mutex> lock(current_mutex);
        if (!current || current->seqs != seqs) return;
        chunk = std::move(current);
        current.reset();
    }
    std::lock_guard<std::mutex> lock(seeder_mutex);
    if (seeder) seeder->drain(*chunk);
}

bool wgse_mem3_seed_collect(FMI_search *fmi, const mem_opt_t *opt, const bseq1_t *seq, int nseq,
    mem_cache *mmc, int64_t &total, int tid, SMEM *&smems) {
    if (upstream_collect) return false;
    std::shared_ptr<Chunk> chunk;
    {
        std::lock_guard<std::mutex> lock(current_mutex);
        chunk = current;
    }
    if (!chunk) return false;
    // Each read's index in the chunk: its place in the chunk's array, or (for the copies
    // --dedup-reads maps) its id relative to the chunk's first; the bases must be the same.
    // Its batch must have finished.
    thread_local std::vector<int> index;
    index.resize(size_t(nseq));
    for (int r = 0; r < nseq; ++r) {
        // Not pointer subtraction: the compacted copies are in another array.
        const uintptr_t address = reinterpret_cast<uintptr_t>(seq + r),
            base = reinterpret_cast<uintptr_t>(chunk->seqs);
        const bool inside = address >= base && address < base + size_t(chunk->n) * sizeof(bseq1_t)
            && (address - base) % sizeof(bseq1_t) == 0;
        const long id = long(seq[r].id) - long(chunk->seqs[0].id);
        const long i = inside ? long((address - base) / sizeof(bseq1_t)) : id;
        if (i < 0 || i >= chunk->n || chunk->seqs[i].seq != seq[r].seq
            || !chunk->batches[i / batch_reads].ready.load(std::memory_order_acquire)) {
            std::lock_guard<std::mutex> lock(stats_mutex);
            ++misses;
            return false;
        }
        index[size_t(r)] = int(i);
    }
    thread_local std::vector<SMEM> out;
    out.clear();
    uint64_t cpu = 0, longer = 0, overflowed = 0;
    for (int r = 0; r < nseq; ++r) {
        const Batch &batch = chunk->batches[index[size_t(r)] / batch_reads];
        const size_t i = size_t(index[size_t(r)] % batch_reads);
        const SeedCount &count = batch.counts[i];
        if (count.status == seed_ok) {
            const SeedSmem *in = batch.smems.data() + batch.offsets[i];
            const size_t first = out.size(), n = size_t(count.pass[0]) + count.pass[1] + count.pass[2];
            for (size_t k = 0; k < n; ++k) {
                SMEM smem{};
                smem.rid = uint32_t(r);
                smem.m = in[k].m;
                smem.n = in[k].n;
                smem.k = in[k].k;
                smem.l = in[k].l;
                smem.s = in[k].s;
                out.push_back(smem);
            }
            // Upstream's per-read (m, n) order; SMEMs with equal (m, n) differ at most in l,
            // which nothing downstream reads. They carry no SA tag (info 0): SA lookup then
            // walks to a sampled row for the same coordinate.
            std::sort(out.begin() + long(first), out.end(),
                [](const SMEM &a, const SMEM &b) { return a.m != b.m ? a.m < b.m : a.n < b.n; });
            continue;
        }
        int64_t n = 0;
        const SMEM *in = cpu_collect(fmi, opt, seq + r, 1, mmc, tid, n);
        for (int64_t k = 0; k < n; ++k) {
            out.push_back(in[k]);
            out.back().rid = uint32_t(r);
        }
        ++cpu;
        longer += count.status == seed_declined;
        overflowed += count.status == seed_overflow;
    }
    if (opt->smem_dedup) out.resize(size_t(smem_dedup_inplace(out.data(), int64_t(out.size()))));
    if (verify) {
        int64_t n = 0;
        const SMEM *expected = cpu_collect(fmi, opt, seq, nseq, mmc, tid, n);
        if (!std::equal(out.begin(), out.end(), expected, expected + n, same_smem)) {
            std::lock_guard<std::mutex> lock(stats_mutex);
            if (mismatches++ < 3)
                fprintf(stderr, "[wgse-bwa-mem3] seeding mismatch: gpu=%zu cpu=%lld SMEMs\n", out.size(),
                    (long long)n);
            smems = const_cast<SMEM *>(expected);
            total = n;
            return true;
        }
    }
    if (int64_t(out.size()) > mmc->wsize_mem[tid]) {
        // As upstream's grow in mem_kernel1_core: the per-read arrays are sized by wsize_mem as
        // well, and running out of memory ends the process.
        const size_t size = out.size();
        _mm_free(mmc->matchArray[tid]);
        mmc->matchArray[tid] = static_cast<SMEM *>(_mm_malloc(size * sizeof(SMEM), 64));
        if (!mmc->matchArray[tid]) err_fatal(__func__, "out of memory growing matchArray");
        const std::pair<int32_t **, const char *> arrays[] = {{&mmc->min_intv_ar[tid], "min_intv_ar"},
            {&mmc->query_pos_ar[tid], "query_pos_ar"}, {&mmc->rid[tid], "rid"}};
        for (const auto &array : arrays) {
            auto *grown = static_cast<int32_t *>(realloc(*array.first, size * sizeof(int32_t)));
            if (!grown) err_fatal(__func__, "out of memory growing %s", array.second);
            *array.first = grown;
        }
        mmc->wsize_mem[tid] = mmc->wsize_mem_s[tid] = mmc->wsize_mem_r[tid] = int64_t(size);
    }
    std::copy(out.begin(), out.end(), mmc->matchArray[tid]);
    smems = mmc->matchArray[tid];
    total = int64_t(out.size());
    std::lock_guard<std::mutex> lock(stats_mutex);
    ++hits;
    cpu_reads += cpu;
    long_reads += longer;
    overflow_reads += overflowed;
    return true;
}

bool wgse_mem3_seed_finish() {
    {
        std::lock_guard<std::mutex> lock(seeder_mutex);
        seeder.reset();
    }
    std::lock_guard<std::mutex> lock(stats_mutex);
    if (!chunks && usable) return false;
    fprintf(stderr, "[wgse-bwa-mem3] seed_chunks=%llu seed_reads=%llu seed_gpu_reads=%llu "
        "seed_cpu_reads=%llu seed_long_reads=%llu seed_overflow_reads=%llu seed_hits=%llu seed_misses=%llu seed_mismatches=%llu seed_batches=%llu "
        "seed_late_batches=%llu seed_gpu_s=%.2f\n",
        (unsigned long long)chunks, (unsigned long long)reads_total, (unsigned long long)gpu_reads,
        (unsigned long long)cpu_reads, (unsigned long long)long_reads,
        (unsigned long long)overflow_reads, (unsigned long long)hits, (unsigned long long)misses,
        (unsigned long long)mismatches, (unsigned long long)batches_run,
        (unsigned long long)batches_late, gpu_seconds);
    if (!usable)
        fprintf(stderr, "[wgse-bwa-mem3] %s: %s; seeding ran on the CPU\n",
            batch_failed ? "CPU fallback: GPU seeding failed" : "GPU seeding is off",
            failure_reason.c_str());
    return batch_failed;
}
