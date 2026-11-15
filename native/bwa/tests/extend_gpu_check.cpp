// Compare the Vulkan kernel directly with upstream, including batches spanning
// several submissions and length classes with partially filled workgroups.
#include "gpu_device.h"

#include <cstdio>
#include <future>
#include <random>

extern "C" int wgse_ksw_extend2_upstream(int, const uint8_t *, int, const uint8_t *,
    int, const int8_t *, int, int, int, int, int, int, int, int, int *, int *, int *, int *, int *);

namespace {
ExtendCall oracle(const ExtendParameters &p, const uint8_t *query, int qlen,
    const uint8_t *target, int tlen, int band, int bonus, int h0) {
    int8_t matrix[25];
    for (int i = 0; i < 5; ++i)
        for (int j = 0; j < 5; ++j)
            matrix[i * 5 + j] = int8_t(i == 4 || j == 4 ? p.ambiguous
                : i == j ? p.match : p.mismatch);
    ExtendCall call{};
    call.score = wgse_ksw_extend2_upstream(qlen, query, tlen, target, 5, matrix,
        p.o_del, p.e_del, p.o_ins, p.e_ins, band, bonus, p.zdrop, h0,
        &call.qle, &call.tle, &call.gtle, &call.gscore, &call.max_off);
    return call;
}

bool same(const ExtendCall &a, const ExtendCall &b) {
    return a.score == b.score && a.qle == b.qle && a.tle == b.tle
        && a.gtle == b.gtle && a.gscore == b.gscore && a.max_off == b.max_off;
}
} // namespace

int main(int argc, char **) {
    using namespace wgse::gpu;
    if (!vulkan_hardware_supported()) return 77;
    DeviceOptions options;
    options.force_staging = argc > 1;
    options.profile = true;
    std::string reason;
    auto device = make_vulkan_device(options, reason);
    if (!device) {
        fprintf(stderr, "%s\n", reason.c_str());
        return 1;
    }
    constexpr size_t count = 9001;
    auto buffers = device->extend_buffers(count * 1100, count);
    std::mt19937 random(675);
    size_t used = 0;
    for (size_t i = 0; i < count; ++i) {
        auto &job = buffers->jobs[i];
        job = {};
        auto side = [&](uint32_t &query, uint32_t &target, uint32_t &qlen, uint32_t &tlen) {
            qlen = random() % 257;
            tlen = qlen + 7;
            query = uint32_t(used);
            for (size_t j = 0; j < qlen; ++j)
                buffers->bases[used++] = uint8_t(random() % 5);
            target = uint32_t(used);
            for (size_t j = 0; j < tlen; ++j) {
                uint8_t base = j < qlen ? buffers->bases[query + j] : uint8_t(random() % 4);
                if (random() % 17 == 0) base = uint8_t(random() % 5);
                buffers->bases[used++] = base;
            }
        };
        side(job.left_query, job.left_target, job.left_qlen, job.left_tlen);
        side(job.right_query, job.right_target, job.right_qlen, job.right_tlen);
        job.left_h0 = 5 + random() % 45;
    }
    for (int band : {8, 100}) {
        ExtendParameters p{};
        p.w = band;
        p.o_del = p.o_ins = 6;
        p.e_del = p.e_ins = 1;
        p.match = p.max = 1;
        p.mismatch = -4;
        p.ambiguous = -1;
        p.left_bonus = p.right_bonus = 5;
        p.zdrop = 30;
        std::promise<bool> completion;
        auto finished = completion.get_future();
        device->extend(p, *buffers, count, 1,
            [&](Outcome outcome, double, const std::string &error) {
                if (outcome != Outcome::done) fprintf(stderr, "%s\n", error.c_str());
                completion.set_value(outcome == Outcome::done);
            });
        if (!finished.get()) return 1;
        for (size_t i = 0; i < count; ++i) {
            const auto &job = buffers->jobs[i];
            const auto &result = buffers->results[i];
            int h0 = int(job.left_h0);
            for (int side = 0; side < 2; ++side) {
                const int qlen = int(side ? job.right_qlen : job.left_qlen);
                const int tlen = int(side ? job.right_tlen : job.left_tlen);
                if (!qlen) continue;
                if (side && result.right_h0 != h0) return 1;
                const uint8_t *query = buffers->bases + (side ? job.right_query : job.left_query);
                const uint8_t *target = buffers->bases + (side ? job.right_target : job.left_target);
                int score = side ? h0 : -1;
                int tries = 0;
                for (int t = 0; t < 2; ++t) {
                    const int w = p.w << t;
                    const auto call = oracle(p, query, qlen, target, tlen, w,
                        side ? p.right_bonus : p.left_bonus, h0);
                    if (!same(call, result.calls[side * 2 + t])) {
                        fprintf(stderr, "Vulkan mismatch: job=%zu side=%d try=%d band=%d\n",
                            i, side, t, band);
                        return 1;
                    }
                    ++tries;
                    const bool stop = call.score == score || call.max_off < (w >> 1) + (w >> 2);
                    score = call.score;
                    if (stop) break;
                }
                if (tries != (side ? result.right_tries : result.left_tries)) return 1;
                if (!side) h0 = score;
            }
        }
    }
    printf("Vulkan: %zu exact upstream extension jobs, %s\n", count * 2,
        options.force_staging ? "staged" : "mapped");
    device->report_profile();
}
