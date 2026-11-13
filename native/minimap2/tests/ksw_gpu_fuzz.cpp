// The build's gap-fill device (ksw_engine.h: CUDA or Metal) against upstream's
// ksw_extd2_sse with KSW_EZ_APPROX_MAX, as mm_align1 calls it, on random batches: minimap2's
// presets' scorings and random ones the kernels take, 8- and 16-bit kernels, every lane
// width, query and target lengths up to the engine's limits, and random and related
// sequences with ambiguous bases. A fill the device returns as INT_MIN (int8 wrap) is
// counted, not compared: the engine redoes it on the CPU. Exits 1 on any difference, and 77
// (skipped) without a usable GPU.
// Usage: ksw_gpu_fuzz [BATCHES] [SEED]
#include "ksw_engine.h"

extern "C" {
#include "kalloc.h"
#include "ksw2.h"
}

#include <algorithm>
#include <climits>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

using namespace wgse::ksw;

namespace {

uint64_t rng;
uint32_t rnd(uint32_t n) {
    rng ^= rng << 13;
    rng ^= rng >> 7;
    rng ^= rng << 17;
    return (uint32_t)(rng >> 16) % n;
}

struct Gaps {
    int a, b, q, e, q2, e2;
};
// map-hifi and map-ont (minimap2 2.31's defaults), and tests/upstream.sh's scorings just
// inside (16-bit kernels) and just outside (8-bit) the 16-bit kernels' bounds.
constexpr Gaps presets[] = {{1, 4, 6, 2, 26, 1}, {2, 4, 4, 2, 24, 1}, {104, 40, 20, 2, 40, 1},
    {105, 40, 20, 2, 40, 1}, {10, 60, 30, 4, 69, 1}};
constexpr int n_presets = sizeof presets / sizeof presets[0];

int8_t i8(int x) { return (int8_t)(((x + 128) & 255) - 128); }

// Whether ksw_extd2_sse's int8 differences u or v wrap on this fill, the case the 8-bit
// kernels leave to the CPU: their cell recurrence in row-major order, which sees the same
// (i-1, j) and (i, j-1) values as the anti-diagonal order.
bool wraps(const std::vector<uint8_t> &qs, const std::vector<uint8_t> &ts, const Scoring &S) {
    const int qe = S.q + S.e, qe2 = S.q2 + S.e2, Q = (int)qs.size(), T = (int)ts.size();
    const auto boundary = [&](int r) {
        return r == 0 ? -qe : r < S.long_thres ? -S.e : r == S.long_thres ? S.long_diff : -S.e2;
    };
    std::vector<int> x((size_t)Q, -qe), x2((size_t)Q, -qe2), v((size_t)Q);
    for (int j = 0; j < Q; ++j) v[j] = boundary(j);
    for (int i = 0; i < T; ++i) {
        int u = boundary(i), y = -qe, y2 = -qe2;
        for (int j = 0; j < Q; ++j) {
            int z = ts[i] == S.m1 || qs[j] == S.m1 ? S.scN : ts[i] == qs[j] ? S.mch : S.mis;
            const int vp = v[j], ut = u;
            int a = i8(x[j] + vp), b = i8(y + ut), a2 = i8(x2[j] + vp), b2 = i8(y2 + ut);
            z = std::min(std::max({z, a, b, a2, b2}), S.mch);
            const int ui = z - vp, vi = z - ut;
            if (ui < -128 || ui > 127 || vi < -128 || vi > 127) return true;
            u = ui, v[j] = vi;
            int t = i8(z - S.q);
            a = i8(a - t), b = i8(b - t);
            t = i8(z - S.q2);
            a2 = i8(a2 - t), b2 = i8(b2 - t);
            x[j] = i8(std::max(a, 0) - qe), y = i8(std::max(b, 0) - qe);
            x2[j] = i8(std::max(a2, 0) - qe2), y2 = i8(std::max(b2, 0) - qe2);
        }
    }
    return false;
}

} // namespace

int main(int argc, char **argv) {
    const long batches = argc > 1 ? atol(argv[1]) : 40;
    rng = (argc > 2 ? strtoull(argv[2], nullptr, 10) : 1) * 2654435761ULL + 88172645463325252ULL;
    const Limits limits{256, 256 * size_t(max_qlen + max_tlen), size_t(256) << 20,
        256 * size_t(max_qlen + max_tlen)};
    std::unique_ptr<Device> device = make_device();
    std::string reason;
    if (!device->start(1, limits, reason)) {
        printf("skipped: no usable GPU (%s)\n", reason.c_str());
        return 77;
    }
    void *km = km_init();
    ksw_extz_t ez;
    memset(&ez, 0, sizeof ez);
    long fills_total = 0, wrapped = 0, bad = 0, narrow_batches = 0, wrapped8 = 0, fills8 = 0;
    for (long batch_index = 0; batch_index < batches; ++batch_index) {
        Gaps g = presets[rnd(n_presets)];
        if (rnd(3) == 0)
            g = {1 + (int)rnd(10), 1 + (int)rnd(12), (int)rnd(40), 1 + (int)rnd(8), (int)rnd(60), 1 + (int)rnd(4)};
        const int amb = (int)rnd(3);
        int8_t mat[25];
        for (int k = 0; k < 5; ++k)
            for (int j = 0; j < 5; ++j) mat[k * 5 + j] = k == 4 || j == 4 ? -amb : k == j ? g.a : -g.b;
        Scoring scoring;
        bool narrow = false;
        if (g.q + g.e + g.q2 + g.e2 > 127 || !make_scoring(g.q, g.e, g.q2, g.e2, mat, scoring, narrow)) {
            --batch_index; // a scoring minimap2 or the kernels reject
            continue;
        }
        // The 8-bit kernels are exact for any scoring the kernels take, so run them on half
        // of the batches the 16-bit ones could.
        if (narrow && rnd(2)) narrow = false;
        narrow_batches += narrow;
        // Fills of one width class per batch part, sorted as the engine sorts them.
        struct Case { std::vector<uint8_t> q, t; };
        std::vector<Case> cases;
        size_t matrix = 0;
        const size_t n_target = 8 + rnd(120);
        while (cases.size() < n_target) {
            const int qlen = 1 + (int)rnd(rnd(3) ? 300 : max_qlen);
            const int tlen = 1 + (int)rnd(rnd(3) ? 400 : rnd(4) ? 1200 : max_tlen);
            if (matrix + matrix_bytes(qlen, tlen) + 4 > limits.matrix_bytes) break;
            matrix += (matrix_bytes(qlen, tlen) + 3) & ~(size_t)3;
            Case c{std::vector<uint8_t>((size_t)qlen), std::vector<uint8_t>((size_t)tlen)};
            const int div = (int)rnd(4);
            for (int k = 0; k < tlen; ++k) c.t[k] = rnd(50) == 0 ? 4 : (uint8_t)rnd(4);
            for (int k = 0; k < qlen; ++k)
                c.q[k] = div == 0 || rnd(div * 8) == 0 ? (uint8_t)rnd(5) : c.t[(size_t)k * tlen / qlen];
            cases.push_back(std::move(c));
        }
        std::stable_sort(cases.begin(), cases.end(), [](const Case &x, const Case &y) {
            const int cx = lane_columns((int)x.q.size()), cy = lane_columns((int)y.q.size());
            return cx != cy ? cx < cy : x.t.size() > y.t.size();
        });
        const size_t n = cases.size();
        size_t first[n_widths + 1] = {}, seq = 0, cigar = 0;
        for (int k = 0, i = 0; k < n_widths; ++k) {
            first[k] = (size_t)i;
            while ((size_t)i < n && lane_columns((int)cases[i].q.size()) == widths[k]) ++i;
            first[k + 1] = (size_t)i;
        }
        for (const Case &c : cases) seq += c.q.size() + c.t.size(), cigar += c.q.size() + c.t.size();
        Batch b;
        if (!device->stage(0, n, seq, matrix, cigar, b, reason)) {
            fprintf(stderr, "stage failed: %s\n", reason.c_str());
            return 1;
        }
        seq = matrix = cigar = 0;
        for (size_t k = 0; k < n; ++k) {
            const Case &c = cases[k];
            Fill &f = b.fills[k];
            f.qlen = (int)c.q.size(), f.tlen = (int)c.t.size();
            f.qoff = (uint32_t)seq, seq += f.qlen;
            f.toff = (uint32_t)seq, seq += f.tlen;
            f.poff = (uint32_t)matrix, matrix += (matrix_bytes(f.qlen, f.tlen) + 3) & ~(size_t)3;
            f.coff = (uint32_t)cigar, cigar += f.qlen + f.tlen;
            memcpy(b.seq + f.qoff, c.q.data(), c.q.size());
            memcpy(b.seq + f.toff, c.t.data(), c.t.size());
        }
        double gpu_seconds = 0;
        if (!device->run(0, scoring, narrow, n, first, cigar, b, gpu_seconds, reason)) {
            fprintf(stderr, "run failed: %s\n", reason.c_str());
            return 1;
        }
        for (size_t k = 0; k < n; ++k) {
            const Case &c = cases[k];
            ++fills_total;
            fills8 += !narrow;
            if (b.score[k] == INT_MIN) {
                ++wrapped;
                wrapped8 += !narrow;
                // The 16-bit kernels' bounds are conservative; the 8-bit ones' check is exact.
                if (!narrow && !wraps(c.q, c.t, scoring) && bad++ < 10)
                    fprintf(stderr, "batch %ld fill %zu: the 8-bit kernel reports a wrap that ksw_extd2_sse "
                        "doesn't have (qlen=%zu tlen=%zu a=%d b=%d q=%d e=%d q2=%d e2=%d)\n", batch_index, k,
                        c.q.size(), c.t.size(), g.a, g.b, g.q, g.e, g.q2, g.e2);
                continue;
            }
            ksw_extd2_sse(km, (int)c.q.size(), c.q.data(), (int)c.t.size(), c.t.data(), 5, mat, (int8_t)g.q,
                (int8_t)g.e, (int8_t)g.q2, (int8_t)g.e2, -1, -1, 0, KSW_EZ_APPROX_MAX, &ez);
            bool same = ez.score == b.score[k] && ez.n_cigar == b.n_cigar[k];
            const uint32_t *gpu = b.cigar + b.fills[k].coff;
            for (int i = 0; same && i < ez.n_cigar; ++i) same = ez.cigar[i] == gpu[ez.n_cigar - 1 - i];
            if (!same && bad++ < 10)
                fprintf(stderr, "batch %ld fill %zu differs: qlen=%zu tlen=%zu a=%d b=%d q=%d e=%d q2=%d e2=%d "
                    "%s kernels; score %d (GPU %d), %d CIGAR ops (GPU %d)\n", batch_index, k, c.q.size(),
                    c.t.size(), g.a, g.b, g.q, g.e, g.q2, g.e2, narrow ? "16-bit" : "8-bit", ez.score,
                    b.score[k], ez.n_cigar, b.n_cigar[k]);
        }
    }
    kfree(km, ez.cigar);
    km_destroy(km);
    printf("%s: %ld fills in %ld batches (%ld with the 16-bit kernels), %ld left to the CPU (%ld of the "
        "8-bit kernels' %ld), %ld differ from upstream\n", device->name(), fills_total, batches, narrow_batches,
        wrapped, wrapped8, fills8, bad);
    return bad != 0;
}
