// The GPU side of the gap-fill offload (ksw_gpu.h): what ksw_host.cpp's engine asks of
// a device (Metal: metal_ksw.mm, CUDA: ksw_cuda.cu), and the batch layout its kernels read.
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>

namespace wgse::ksw {

// Must match the kernels' structs (metal_ksw.mm's source, ksw_fill.cuh).
struct Scoring {
    int32_t q, e, q2, e2, mch, mis, scN, m1, long_thres, long_diff;
};
// A fill's query and target in the batch's sequence buffer, its backtrack matrix in the
// device's matrix buffer and its CIGAR (written reversed) in the CIGAR buffer.
struct Fill {
    uint32_t qoff, toff, poff, coff;
    int32_t qlen, tlen;
};

// Query columns per lane C for the 32 lanes of a fill: 7 suits HiFi's typical
// fills of 193-224 bases. Longer targets only grow the backtrack matrix.
constexpr int n_widths = 5;
constexpr int widths[n_widths] = {4, 7, 8, 12, 16};
constexpr int max_qlen = 512, max_tlen = 4096;

inline int lane_columns(int qlen) {
    for (int c : widths)
        if (qlen <= 32 * c) return c;
    return 0;
}

// A fill's backtrack matrix: a byte per cell, rows padded to whole lanes.
inline size_t matrix_bytes(int qlen, int tlen) {
    const int c = lane_columns(qlen);
    return (size_t)tlen * ((qlen + c - 1) / c * c);
}

// A batch's host-visible buffers: the engine writes the fills and sequences before
// run(), and reads the scores (INT_MIN where the CPU must redo a fill), CIGAR lengths
// and CIGARs after it succeeds.
struct Batch {
    Fill *fills = nullptr;
    uint8_t *seq = nullptr;
    const int32_t *score = nullptr, *n_cigar = nullptr;
    const uint32_t *cigar = nullptr;
};

// The largest batch the engine builds, so a device can allocate for it up front.
struct Limits {
    size_t fills, seq_bytes, matrix_bytes, cigar_words;
};

// The GPU that computes gap fills. Each of the engine's dispatcher threads uses its own
// slot, so a slot serves one thread at a time and batches on different slots overlap.
class Device {
public:
    virtual ~Device() = default;
    virtual const char *name() const = 0;
    // Sets the device up with `slots` slots for batches within `limits`.
    virtual bool start(int slots, const Limits &limits, std::string &reason) = 0;
    // Whether start would succeed, without allocating the batch buffers.
    virtual bool probe(int slots, const Limits &limits, std::string &reason) {
        return start(slots, limits, reason);
    }
    // Host-visible buffers for a batch of n fills with these totals.
    virtual bool stage(int slot, size_t n, size_t seq_bytes, size_t matrix_bytes,
        size_t cigar_words, Batch &batch, std::string &reason) = 0;
    // Runs the staged batch: fills [first[k], first[k + 1]) have widths[k] columns per
    // lane. Returns false with the reason if the GPU failed (the CPU then redoes it).
    virtual bool run(int slot, const Scoring &scoring, bool narrow, size_t n,
        const size_t (&first)[n_widths + 1], size_t cigar_words, Batch &batch,
        double &gpu_seconds, std::string &reason) = 0;
    virtual size_t buffer_bytes() const = 0;
    // Command buffers the device retried after an interruption, for the metrics.
    virtual size_t interruptions() const { return 0; }
};

// The kernels' scoring for minimap2's (ksw_gen_ts_mat's matrix and gap costs), and whether
// the 16-bit kernels apply (narrow); false if the kernels can't compute it.
bool make_scoring(int q, int e, int q2, int e2, const int8_t mat[25], Scoring &scoring, bool &narrow);

// The build's device (metal_ksw.mm or ksw_cuda.cu).
std::unique_ptr<Device> make_device();

} // namespace wgse::ksw
