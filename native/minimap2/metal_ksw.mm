// The Metal device of the gap-fill offload (ksw_engine.h): ksw_extd2_sse's
// KSW_EZ_APPROX_MAX fills, one simdgroup per fill.
#include "ksw_engine.h"
#include "ksw_gpu.h"
#include "runtime.h"

#include <algorithm>
#include <atomic>
#include <string>
#include <vector>

namespace {

using namespace wgse::ksw;

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

class MetalDevice final : public Device {
public:
    const char *name() const override { return "Metal"; }

    bool start(int slots, const Limits &limits, std::string &reason) override {
        @autoreleasepool {
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
            for (auto &set : pipelines_)
                for (id<MTLComputePipelineState> pipeline : set)
                    if (pipeline.maxTotalThreadsPerThreadgroup < 64 || pipeline.threadExecutionWidth != 32) {
                        reason = "unsupported Metal SIMD width";
                        return false;
                    }
            slots_ = std::vector<Slot>((size_t)slots);
            max_fills_ = limits.fills;
            return true;
        }
    }

    bool stage(int slot, size_t n, size_t seq_bytes, size_t matrix_bytes, size_t cigar_words,
        Batch &batch, std::string &reason) override {
        @autoreleasepool {
            Slot &b = slots_[(size_t)slot];
            bool ok = grow(b.fills, b.fills_size, n * sizeof(Fill), false)
                && grow(b.seq, b.seq_size, seq_bytes, false) && grow(b.matrix, b.matrix_size, matrix_bytes, true)
                && grow(b.cigar, b.cigar_size, cigar_words * 4, false);
            if (ok && (!b.score || b.score.length < n * 4)) {
                b.score = runtime_.allocate(std::max(n, max_fills_) * 4);
                b.n_cigar = runtime_.allocate(std::max(n, max_fills_) * 4);
                ok = b.score && b.n_cigar;
            }
            if (!ok) {
                reason = "Metal buffer allocation failed";
                return false;
            }
            batch.fills = (Fill *)b.fills.contents;
            batch.seq = (uint8_t *)b.seq.contents;
            batch.score = (const int32_t *)b.score.contents;
            batch.n_cigar = (const int32_t *)b.n_cigar.contents;
            batch.cigar = (const uint32_t *)b.cigar.contents;
            return true;
        }
    }

    bool run(int slot, const Scoring &scoring, bool narrow, size_t, const size_t (&first)[n_widths + 1],
        size_t, Batch &, double &gpu_seconds, std::string &reason) override {
        @autoreleasepool {
            Slot &b = slots_[(size_t)slot];
            for (int attempt = 1;; ++attempt) {
                id<MTLCommandBuffer> command = [runtime_.queue() commandBuffer];
                id<MTLComputeCommandEncoder> encoder =
                    [command computeCommandEncoderWithDispatchType:MTLDispatchTypeConcurrent];
                if (!command || !encoder) {
                    reason = "Metal command allocation failed";
                    return false;
                }
                command.label = encoder.label = @"ksw_fill";
                [encoder setBytes:&scoring length:sizeof scoring atIndex:1];
                [encoder setBuffer:b.seq offset:0 atIndex:2];
                [encoder setBuffer:b.matrix offset:0 atIndex:3];
                for (int k = 0; k < n_widths; ++k) {
                    const uint32_t count = (uint32_t)(first[k + 1] - first[k]);
                    if (!count) continue;
                    [encoder setComputePipelineState:pipelines_[narrow][k]];
                    [encoder setBuffer:b.fills offset:first[k] * sizeof(Fill) atIndex:0];
                    [encoder setBuffer:b.score offset:first[k] * 4 atIndex:4];
                    [encoder setBuffer:b.cigar offset:0 atIndex:5];
                    [encoder setBuffer:b.n_cigar offset:first[k] * 4 atIndex:6];
                    [encoder setBytes:&count length:4 atIndex:7];
                    [encoder dispatchThreads:MTLSizeMake(count * 32, 1, 1)
                        threadsPerThreadgroup:MTLSizeMake(64, 1, 1)];
                }
                [encoder endEncoding];
                wgse::metal::Runtime::run(command);
                if (attempt < wgse::metal::Runtime::attempts
                    && wgse::metal::Runtime::interrupted(command, attempt))
                    continue;
                if (command.status != MTLCommandBufferStatusCompleted) {
                    reason = wgse::metal::Runtime::describe(command);
                    return false;
                }
                gpu_seconds = command.GPUEndTime - command.GPUStartTime;
                return true;
            }
        }
    }

    size_t buffer_bytes() const override { return buffer_bytes_.load(); }
    size_t interruptions() const override { return wgse::metal::Runtime::interruptions(); }

private:
    struct Slot {
        id<MTLBuffer> fills, seq, matrix, score, cigar, n_cigar;
        size_t fills_size = 0, seq_size = 0, matrix_size = 0, cigar_size = 0;
    };

    bool grow(__strong id<MTLBuffer> &buffer, size_t &size, size_t needed, bool private_storage) {
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

    wgse::metal::Runtime runtime_;
    id<MTLComputePipelineState> pipelines_[2][n_widths] = {}; // [narrow][width]
    std::vector<Slot> slots_;
    size_t max_fills_ = 0;
    std::atomic<size_t> buffer_bytes_{0};
};

} // namespace

std::unique_ptr<Device> wgse::ksw::make_device() {
    return std::make_unique<MetalDevice>();
}

extern "C" {

int wgse_ksw_gpu_hardware_supported(void) {
    @autoreleasepool {
        wgse::metal::Runtime runtime;
        return runtime.device() != nil;
    }
}

void wgse_ksw_inject_interruptions(int count) {
    wgse::metal::Runtime::inject_interruptions((size_t)count);
}

void wgse_ksw_inject_hang(const char *label, double timeout) {
    wgse::metal::Runtime::inject_hang(label, timeout);
}

} // extern "C"
