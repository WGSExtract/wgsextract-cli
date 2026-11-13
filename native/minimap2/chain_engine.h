// The GPU side of the chaining offload (chain_gpu.h): the batch layout chain_fill.cuh's
// kernels read and what chain_host.cpp asks of a device (CUDA: chain_cuda.cu).
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "minimap.h"

namespace wgse::chain {

// mg_lchain_dp's settings, after its own adjustments of max_dist_x and max_dist_y.
struct Params {
    int32_t max_dist_x, max_dist_y, bw, max_skip, max_iter, is_cdna;
    float pen_gap, pen_skip;
};
// An anchor of a run: the low 32 bits of a[].x (the high ones are the same across the
// run) and of a[].y's query position (under 2^24), with its q_span above them.
struct Anchor {
    uint32_t x, y;
};
// A run's anchors in the batch.
struct Run {
    uint32_t first, n;
};
// A filled anchor: f[] and p[] (an index in the run, or -1).
struct Score {
    int32_t f, p;
};

// Runs this long or longer are filled by a warp, shorter ones by a thread.
constexpr uint32_t warp_run = 64;

struct Batch {
    Anchor *anchors = nullptr;
    Run *runs = nullptr;
    const Score *scores = nullptr;
};

// The GPU that fills runs. Each dispatcher thread uses its own slot.
class Device {
public:
    virtual ~Device() = default;
    virtual bool probe(int slots, size_t anchors, size_t runs, std::string &reason) = 0;
    virtual bool start(int slots, size_t anchors, size_t runs, std::string &reason) = 0;
    // Host-visible buffers for a batch of this size.
    virtual bool stage(int slot, size_t anchors, size_t runs, Batch &batch, std::string &reason) = 0;
    // Fills the staged batch, whose runs [0, n_short) are shorter than warp_run.
    virtual bool run(int slot, const Params &params, size_t n_short, size_t runs, size_t anchors,
        double &gpu_seconds, std::string &reason) = 0;
    virtual size_t buffer_bytes() const = 0;
};

std::unique_ptr<Device> make_device();

// A read's anchors split into runs (chain_host.cpp): the runs for the GPU, packed, with
// each one's first anchor in a[], and the runs longer than the GPU takes.
struct Packed {
    std::vector<Anchor> anchors; // the first n_anchors are the runs'
    size_t n_anchors = 0;
    std::vector<Run> runs;
    std::vector<int64_t> starts;
    std::vector<int64_t> cpu_runs; // start, length pairs
};
// Splits a[0..n) into runs, fills lone anchors' f, p and v, and packs runs of up to
// max_gpu_run anchors; false if the GPU can't fill them.
bool split(const Params &params, int64_t n, const mm128_t *a, uint32_t max_gpu_run, Packed &packed,
    int32_t *f, int64_t *p, int32_t *v);
// f, p and v of the packed runs from the GPU's scores.
void unpack(const Packed &packed, const Score *scores, int32_t *f, int64_t *p, int32_t *v);
// Upstream's fill of len anchors from start (t is scratch).
void fill_cpu(const Params &params, const mm128_t *a, int64_t start, int64_t len, int32_t *f, int64_t *p,
    int32_t *v, std::vector<int32_t> &t);

} // namespace wgse::chain
