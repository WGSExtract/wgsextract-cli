// The build's chaining device (chain_engine.h: CUDA) with chain_host.cpp's split and unpack,
// against upstream's score fill (wgse_lchain_dp_fill, mg_lchain_dp's loop) on random reads'
// anchors: chains along diagonals with indels, tandem repeats whose rows exit on the skip
// count, equal positions, both strands and several sequences, gaps that split the anchors
// into runs, and runs from one anchor to several thousand, with mg_lchain_dp's map-ont,
// map-hifi and splice settings and random ones (small bands, skip counts and iteration caps).
// f, p and v must be identical for every anchor. Exits 1 on any difference, and 77 (skipped)
// without a usable GPU.
// Usage: chain_gpu_fuzz [BATCHES] [SEED]
#include "chain_engine.h"
#include "chain_gpu.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

using namespace wgse::chain;

namespace {

uint64_t rng;
uint32_t rnd(uint32_t n) {
    rng ^= rng << 13;
    rng ^= rng >> 7;
    rng ^= rng << 17;
    return (uint32_t)(rng >> 16) % n;
}

// mg_lchain_dp's settings after its adjustments: map-ont, map-hifi, splice, then random.
Params params() {
    switch (rnd(5)) {
    case 0: return {5000, 5000, 500, 25, 5000, 0, 0.12f, 0.0f};
    case 1: return {5000, 5000, 500, 25, 5000, 0, 0.6f, 0.0f};
    case 2: return {200000, 2000, 200000, 25, 5000, 1, 0.12f, 0.0f};
    default: {
        const int32_t bw = 1 + (int32_t)rnd(800);
        const int32_t x = bw + (int32_t)rnd(4000);
        return {x, bw + (int32_t)rnd(4000), bw, (int32_t)rnd(30), 1 + (int32_t)rnd(300), (int32_t)rnd(2),
            0.01f * (float)rnd(100), rnd(2) ? 0.0f : 0.01f * (float)rnd(50)};
    }
    }
}

// A position past a sequence's 2^32 end carries into the next sequence, as no real one does.
mm128_t anchor(uint64_t rev, uint64_t tid, uint64_t tpos, uint64_t qpos, uint64_t span) {
    return {rev << 63 | ((tid << 32) + tpos), span << 32 | qpos};
}

// A read's anchors, sorted by x as collect_seed_hits sorts them (equal x in any order).
std::vector<mm128_t> read_anchors(const Params &p) {
    std::vector<mm128_t> a;
    const int loci = 1 + (int)rnd(12);
    const uint32_t qlen = 2000 + rnd(60000);
    const uint64_t far = rnd(40) ? 0 : 1u << 24; // query positions the GPU can't take
    for (int l = 0; l < loci; ++l) {
        const uint64_t rev = rnd(2), tid = rnd(3);
        uint64_t t = rnd(100) ? rnd(1u << 30) : 0xffffffffu - rnd(3000); // some near a sequence's end
        const int kind = (int)rnd(5), n = rnd(8) ? (int)rnd(400) : (int)rnd(6000);
        uint64_t q = rnd(qlen);
        for (int k = 0; k < n; ++k) {
            const uint64_t span = 10 + rnd(19);
            if (kind == 0) { // a chain with indels and noise
                const uint32_t step = 1 + rnd(60);
                q += step, t += step + (rnd(8) ? 0 : rnd(40)) - (rnd(8) ? 0 : rnd(20));
            } else if (kind == 1) { // a tandem repeat: each query position at several copies
                q += 1 + rnd(6);
                t = (t & ~(uint64_t)63) + rnd(3) * 64 + (q & 63);
            } else if (kind == 4) { // parallel copies of a segment: equal scores, early exits
                if (k % 4 == 0) q += 5 + rnd(20), t += 5 + rnd(20);
                a.push_back(anchor(rev, tid, t + (uint64_t)(k % 4) * (1 + rnd(3)) * (uint64_t)p.bw / 2, q % (1u << 24) | far, 15));
                continue;
            } else if (kind == 2) { // scattered hits, often too far apart to chain
                q = rnd(qlen), t += rnd(2 * (uint32_t)p.max_dist_x + 2);
            } else { // equal positions
                q += rnd(3);
                t += rnd(2);
            }
            a.push_back(anchor(rev, tid, t, (q % (1u << 24)) | far, span));
        }
    }
    std::sort(a.begin(), a.end(), [](const mm128_t &x, const mm128_t &y) { return x.x < y.x; });
    for (size_t k = 1; k < a.size(); ++k) // equal keys in random order
        if (a[k].x == a[k - 1].x && rnd(2)) std::swap(a[k], a[k - 1]);
    return a;
}

struct Read {
    std::vector<mm128_t> a;
    std::vector<int32_t> f, v, ef, ev;
    std::vector<int64_t> p, ep;
    Packed packed;
    size_t offset = 0;
};

} // namespace

int main(int argc, char **argv) {
    const int batches = argc > 1 ? atoi(argv[1]) : 40;
    rng = argc > 2 ? strtoull(argv[2], nullptr, 10) * 0x9e3779b97f4a7c15ULL + 1 : 1;
    constexpr size_t cap = size_t(1) << 22;
    std::unique_ptr<Device> device = make_device();
    std::string reason;
    if (!device || !device->start(1, cap, cap / 2, reason)) {
        printf("skipped: %s\n", device ? reason.c_str() : "no chaining device");
        return 77;
    }
    size_t anchors_checked = 0, warp_runs = 0, rejected = 0, bad = 0;
    std::vector<int32_t> t;
    for (int b = 0; b < batches; ++b) {
        const Params p = params();
        const uint32_t max_gpu_run = rnd(4) ? 16384 : 1 + rnd(100);
        std::vector<Read> reads(1 + rnd(24));
        size_t anchors = 0, runs = 0, n_short = 0;
        for (Read &r : reads) {
            r.a = read_anchors(p);
            const int64_t n = (int64_t)r.a.size();
            if (n == 0) continue;
            r.ef.assign(n, 0), r.ev.assign(n, 0), r.ep.assign(n, 0), t.assign(n, 0);
            wgse_lchain_dp_fill(p.max_dist_x, p.max_dist_y, p.bw, p.max_skip, p.max_iter, p.pen_gap, p.pen_skip,
                p.is_cdna, 1, n, r.a.data(), r.ef.data(), r.ep.data(), r.ev.data(), t.data());
            r.f.assign(n, -1), r.v.assign(n, -1), r.p.assign(n, -2);
            if (!split(p, n, r.a.data(), max_gpu_run, r.packed, r.f.data(), r.p.data(), r.v.data())
                || anchors + r.packed.n_anchors > cap) {
                ++rejected;
                r.a.clear();
                continue;
            }
            r.offset = anchors;
            anchors += r.packed.n_anchors, runs += r.packed.runs.size();
            for (const Run &run : r.packed.runs) n_short += run.n < warp_run, warp_runs += run.n >= warp_run;
        }
        Batch batch;
        if (!device->stage(0, anchors, runs, batch, reason)) {
            printf("stage failed: %s\n", reason.c_str());
            return 1;
        }
        size_t next_short = 0, next_long = n_short;
        for (const Read &r : reads) {
            if (r.a.empty()) continue;
            memcpy(batch.anchors + r.offset, r.packed.anchors.data(), r.packed.n_anchors * sizeof(Anchor));
            for (const Run &run : r.packed.runs)
                batch.runs[run.n < warp_run ? next_short++ : next_long++] = {(uint32_t)(r.offset + run.first), run.n};
        }
        double seconds = 0;
        if (anchors && !device->run(0, p, n_short, runs, anchors, seconds, reason)) {
            printf("run failed: %s\n", reason.c_str());
            return 1;
        }
        for (Read &r : reads) {
            if (r.a.empty()) continue;
            unpack(r.packed, batch.scores + r.offset, r.f.data(), r.p.data(), r.v.data());
            for (size_t k = 0; k < r.packed.cpu_runs.size(); k += 2)
                fill_cpu(p, r.a.data(), r.packed.cpu_runs[k], r.packed.cpu_runs[k + 1], r.f.data(), r.p.data(),
                    r.v.data(), t);
            for (size_t i = 0; i < r.a.size(); ++i) {
                ++anchors_checked;
                if (r.f[i] == r.ef[i] && r.p[i] == r.ep[i] && r.v[i] == r.ev[i]) continue;
                if (bad++ < 10)
                    printf("batch %d anchor %zu: f %d p %lld v %d, upstream f %d p %lld v %d\n", b, i, r.f[i],
                        (long long)r.p[i], r.v[i], r.ef[i], (long long)r.ep[i], r.ev[i]);
            }
        }
    }
    printf("%zu anchors checked (%zu runs filled by warps), %zu reads left to the CPU, %zu differences\n",
        anchors_checked, warp_runs, rejected, bad);
    return bad || anchors_checked == 0 || warp_runs == 0 ? 1 : 0;
}
