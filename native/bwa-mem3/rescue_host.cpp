// GPU mate rescue for bwa-mem3's batched kswv (rescue_gpu.h): packs a worker's 8-bit pairs for
// wgse-bwa's mate-rescue kernel and hands the CPU's kswv the pairs the GPU leaves.
#include "rescue_gpu.h"

#include "rescue_device.h"
#include "../bwa/rescue_parameters.h"

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <mutex>
#include <string>
#include <vector>

namespace {

// kswv's widest tier writes padding lanes up to its SIMD width past the pairs it scores.
constexpr int64_t padding = 64;

std::atomic<bool> enabled{false}, usable{true}, inject_failure{false};
std::mutex stats_mutex;
std::string failure_reason;
bool batch_failed = false;
uint64_t calls = 0, batches = 0, gpu_pairs = 0, cpu_pairs = 0;
double gpu_seconds = 0;

void fail(const std::string &reason, bool batch) {
    std::lock_guard<std::mutex> lock(stats_mutex);
    if (!usable.exchange(false)) return;
    failure_reason = reason;
    batch_failed = batch;
}

// This thread's batch: whether it may use the GPU, and which pairs (by regid) the GPU did.
struct State {
    bool active = false;
    RescueParameters parameters{};
    std::vector<uint8_t> done;
    bool any_done = false;
    std::vector<SeqPair> rest0, rest1;
    std::vector<uint8_t> bases;
    std::vector<RescueJob> jobs;
    std::vector<RescueResult> results;
    std::vector<int64_t> job_pair;
};

State &state() {
    thread_local State value;
    return value;
}

} // namespace

// Without a GPU for it (Metal, for now), mate rescue stays on the CPU, as with --gpu-rescue off.
void wgse_mem3_rescue_set_enabled(bool value) {
    std::string reason;
    enabled = value && wgse_mem3_rescue_device_available(reason);
}

void wgse_mem3_rescue_inject_failure() { inject_failure = true; }

bool wgse_mem3_rescue_finish() {
    std::lock_guard<std::mutex> lock(stats_mutex);
    if (!calls && usable) return false;
    fprintf(stderr, "[wgse-bwa-mem3] rescue_calls=%llu rescue_batches=%llu rescue_gpu_pairs=%llu "
        "rescue_cpu_pairs=%llu rescue_gpu_s=%.2f\n", (unsigned long long)calls,
        (unsigned long long)batches, (unsigned long long)gpu_pairs, (unsigned long long)cpu_pairs,
        gpu_seconds);
    if (!usable)
        fprintf(stderr, "[wgse-bwa-mem3] %s: %s; the CPU rescued the rest\n",
            batch_failed ? "CPU fallback: GPU mate rescue failed" : "GPU mate rescue is off",
            failure_reason.c_str());
    return batch_failed;
}

WgseRescueScope::WgseRescueScope(const mem_opt_t *opt, int64_t count) {
    State &s = state();
    s.active = enabled && usable && !opt->meth_mode && count > 0
        && wgse::rescue::parameters(*opt, s.parameters);
    s.any_done = false;
    if (s.active) s.done.assign(size_t(count), 0);
}

WgseRescueScope::~WgseRescueScope() { state().active = false; }

SeqPair *wgse_mem3_rescue_pass0(SeqPair *pairs, int64_t &count, const uint8_t *ref,
    const uint8_t *qer, kswr_t *aln) {
    State &s = state();
    if (!s.active || count <= 0 || !usable) return pairs;
    s.bases.clear();
    s.jobs.clear();
    s.job_pair.clear();
    s.rest0.clear();
    size_t rows = 0;
    for (int64_t i = 0; i < count; ++i) {
        const SeqPair &sp = pairs[i];
        bool fits = (sp.h0 & KSW_XBYTE) && sp.len1 > 0 && sp.len2 > 0
            && sp.len2 <= int(rescue_max_query) && sp.regid >= 0
            && size_t(sp.regid) < s.done.size();
        const uint8_t *q = qer + sp.idq, *t = ref + sp.idr;
        for (int j = 0; fits && j < sp.len2; ++j) fits = q[j] <= 4;
        for (int j = 0; fits && j < sp.len1; ++j) fits = t[j] <= 4;
        if (!fits) {
            s.rest0.push_back(sp);
            continue;
        }
        const size_t offset = s.bases.size();
        s.bases.insert(s.bases.end(), q, q + sp.len2);
        s.bases.insert(s.bases.end(), t, t + sp.len1);
        s.jobs.push_back({uint32_t(offset), uint32_t(offset + size_t(sp.len2)), uint32_t(rows),
            uint32_t(sp.len2), uint32_t(sp.len1), uint32_t(sp.h0)});
        s.job_pair.push_back(i);
        rows += size_t(sp.len1);
    }
    if (s.jobs.empty()) return pairs;
    s.results.resize(s.jobs.size());
    double seconds = 0;
    std::string reason;
    const bool ok = !inject_failure
        && wgse_mem3_rescue_device_align(s.parameters, s.bases.data(), s.bases.size(),
            s.jobs.data(), s.jobs.size(), rows, s.results.data(), seconds, reason);
    if (!ok) {
        fail(inject_failure ? "injected GPU mate-rescue failure (test)" : reason, true);
        std::lock_guard<std::mutex> lock(stats_mutex);
        ++calls;
        cpu_pairs += uint64_t(count);
        return pairs;
    }
    uint64_t gpu = 0;
    for (size_t k = 0; k < s.jobs.size(); ++k) {
        const SeqPair &sp = pairs[s.job_pair[k]];
        const RescueResult &r = s.results[k];
        if (r.status != rescue_ok) {
            s.rest0.push_back(sp);
            continue;
        }
        aln[sp.regid] = kswr_t{r.score, r.te, r.qe, r.score2, r.te2, r.tb, r.qb};
        s.done[size_t(sp.regid)] = 1;
        ++gpu;
    }
    s.any_done = s.any_done || gpu > 0;
    {
        std::lock_guard<std::mutex> lock(stats_mutex);
        ++calls;
        ++batches;
        gpu_pairs += gpu;
        cpu_pairs += uint64_t(count) - gpu;
        gpu_seconds += seconds;
    }
    count = int64_t(s.rest0.size());
    s.rest0.resize(size_t(count + padding));
    return s.rest0.data();
}

SeqPair *wgse_mem3_rescue_pass1(SeqPair *pairs, int64_t &count) {
    State &s = state();
    if (!s.active || !s.any_done || count <= 0) return pairs;
    s.rest1.clear();
    for (int64_t i = 0; i < count; ++i)
        if (!s.done[size_t(pairs[i].regid)]) s.rest1.push_back(pairs[i]);
    count = int64_t(s.rest1.size());
    s.rest1.resize(size_t(count + padding));
    return s.rest1.data();
}
