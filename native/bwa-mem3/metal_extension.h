#pragma once

// GPU banded Smith-Waterman (--metal) for bwa-mem3's 8-bit seed-extension tier, with
// extension.metal's extend_striped kernel. It computes the scalar banded SW that upstream's
// NEON getScores8 reproduces byte for byte.
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

// extension.metal's Pair.
struct WgseBswJob {
    int32_t idr, idq, id, len1, len2, h0, seqid, regid;
    int32_t score, tle, gtle, qle, gscore, max_off;
    int32_t bandwidth;
};

struct WgseBswScoring {
    int32_t open_deletion, extend_deletion, open_insertion, extend_insertion, zdrop, end_bonus;
    const int8_t *matrix; // 5 x 5
};

// The longest query the kernel scores; longer extensions stay on the CPU.
constexpr int wgse_mem3_max_gpu_query_length = 256;
// Compute threads per -t thread with the GPU, in percent: Apple Silicon's unified memory
// leaves its workers time to spare while they wait; on x86-64 with CUDA, more threads than
// cores were slower (#674).
#ifdef __APPLE__
constexpr int wgse_mem3_default_thread_percent = 200;
#else
constexpr int wgse_mem3_default_thread_percent = 100;
#endif

// This build's GPU backend ("Metal" on macOS, "CUDA" on Linux) and whether the machine has a
// GPU that it supports.
const char *wgse_mem3_gpu_name();
bool wgse_mem3_gpu_hardware_supported();
// Test hooks: report the first `count` GPU commands interrupted, so they are committed again;
// withhold the first GPU command whose label starts with `label`, as a hung GPU would, which
// the watchdog gives up on after 2 s.
void wgse_mem3_gpu_inject_interruptions(size_t count);
void wgse_mem3_gpu_inject_hang(const char *label);

// Whether the kernel compiles and its buffers fit the device, or the reason not.
bool wgse_mem3_bsw_available(std::string &reason);

struct WgseMem3MetalLimits {
    std::string device;
    uint64_t max_buffer_bytes = 0, recommended_working_set_bytes = 0;
};
WgseMem3MetalLimits wgse_mem3_metal_limits();
uint64_t wgse_mem3_physical_memory();
// Test hook: the physical memory the low-memory warning assumes.
void wgse_mem3_set_physical_memory(uint64_t bytes);
// Warns on stderr when a mem run with this index and chunk would not fit in memory.
void wgse_mem3_memory_preflight(const char *prefix, int64_t chunk_bases);
// Compiles the kernel and enables the GPU for the 8-bit tier, or returns false with the reason.
bool wgse_mem3_bsw_start(std::string &reason);
// Prints the GPU's share; returns nonzero if the GPU failed (the CPU scored the rest).
int wgse_mem3_bsw_finish();
bool wgse_mem3_bsw_enabled();
// Whether the GPU's queue is long enough that the caller should score its pairs on the CPU.
bool wgse_mem3_bsw_busy();
void wgse_mem3_bsw_set_backlog(int64_t pairs);
// Compute threads for the user's -t: more under --metal, as workers block on the GPU.
int wgse_mem3_compute_threads(int threads);
// --gpu-threads: compute threads per -t thread under --metal, in percent.
void wgse_mem3_bsw_set_threads(int percent);
// Test hook: every GPU batch fails, so the CPU must score it.
void wgse_mem3_bsw_inject_failure();
// Counts a getScores8 call the kernel cannot reproduce, which the CPU scores.
void wgse_mem3_bsw_decline();
// Scores jobs on the GPU (ref + idr, qer + idq), blocking until done; false if the CPU must.
bool wgse_mem3_bsw_score(WgseBswJob *jobs, int32_t count, const uint8_t *ref,
    const uint8_t *qer, const WgseBswScoring &scoring);

// getScores8 on the GPU: the same results, and the same padding lanes (up to a multiple of
// the CPU kernel's `lanes`), as upstream's kernel.
template <class Pair>
bool wgse_mem3_bsw8(Pair *pairs, const uint8_t *ref, const uint8_t *qer, int32_t count,
    int32_t w, const WgseBswScoring &scoring, int32_t lanes)
{
    if (count <= 0 || !wgse_mem3_bsw_enabled() || wgse_mem3_bsw_busy()) return false;
    thread_local std::vector<WgseBswJob> jobs;
    jobs.resize(size_t(count));
    uint8_t high = 0;
    for (int32_t i = 0; i < count; ++i) {
        const Pair &p = pairs[i];
        // The kernel packs a row's best column in 9 bits; empty pairs stay upstream's.
        if (p.len1 <= 0 || p.len2 <= 0 || p.len2 > wgse_mem3_max_gpu_query_length || p.h0 < 0
            || p.h0 > 32767) {
            wgse_mem3_bsw_decline();
            return false;
        }
        const uint8_t *r = ref + p.idr, *q = qer + p.idq;
        for (int32_t j = 0; j < p.len1; ++j) high = r[j] > high ? r[j] : high;
        for (int32_t j = 0; j < p.len2; ++j) high = q[j] > high ? q[j] : high;
        jobs[size_t(i)] = WgseBswJob{p.idr, p.idq, i, p.len1, p.len2, p.h0, 0, 0,
            0, 0, 0, 0, 0, 0, w};
    }
    if (high > 4) { // the kernel scores A, C, G, T and N only
        wgse_mem3_bsw_decline();
        return false;
    }
    if (!wgse_mem3_bsw_score(jobs.data(), count, ref, qer, scoring)) return false;
    for (int32_t i = 0; i < count; ++i) {
        const WgseBswJob &job = jobs[size_t(i)];
        Pair &p = pairs[i];
        p.score = job.score;
        p.tle = job.tle;
        p.gtle = job.gtle;
        p.qle = job.qle;
        p.gscore = job.gscore;
        p.max_off = job.max_off;
    }
    const int32_t round = (count + lanes - 1) / lanes * lanes;
    for (int32_t i = count; i < round; ++i) {
        pairs[i].id = i;
        pairs[i].len1 = pairs[i].len2 = 0;
        pairs[i].idr = pairs[i].idq = 0;
        pairs[i].h0 = 0;
    }
    return true;
}
