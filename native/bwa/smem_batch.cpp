// mem_collect_intv(), bwt_smem1a() and bwt_seed_strategy1() from BWA 0.7.19
// (Heng Li et al., MIT; see BWA-LICENSE), as one coroutine per read that
// suspends before each bwt_extend() so that other reads step while its
// occurrence blocks load. bwt_extend() itself is upstream's.

#include "smem_batch.h"

#include "metal_backend.h"

#include <algorithm>
#include <coroutine>
#include <exception>

namespace wgse::smem {
namespace {
struct Walk {
    struct promise_type {
        Walk get_return_object() {
            return Walk{std::coroutine_handle<promise_type>::from_promise(*this)};
        }
        std::suspend_always initial_suspend() noexcept { return {}; }
        std::suspend_always final_suspend() noexcept { return {}; }
        void return_void() noexcept {}
        void unhandled_exception() noexcept { std::terminate(); }
    };
    std::coroutine_handle<promise_type> handle;
};

inline const uint32_t *occ_block(const bwt_t *bwt, bwtint_t k) {
    k -= k >= bwt->primary;
    return bwt_occ_intv(bwt, k);
}

// Prefetches the blocks bwt_2occ4() reads for bwt_extend(bwt, ik, ok, is_back),
// then suspends.
std::suspend_always fetch(const bwt_t *bwt, const bwtintv_t &ik, int is_back) {
    const bwtint_t k = ik.x[!is_back] - 1, l = k + ik.x[2];
    const uint32_t *pk = k == bwtint_t(-1) ? nullptr : occ_block(bwt, k);
    const uint32_t *pl = l == bwtint_t(-1) ? nullptr : occ_block(bwt, l);
    // A block is 64 bytes and may straddle two cache lines.
    if (pk) __builtin_prefetch(pk), __builtin_prefetch(pk + 15);
    if (pl && pl != pk) __builtin_prefetch(pl), __builtin_prefetch(pl + 15);
    return {};
}

using Vector = std::vector<bwtintv_t>;

Walk collect_one(const mem_opt_t *opt, const bwt_t *bwt, int len, const uint8_t *seq, Vector &mem) {
    const uint8_t *q = seq;
    Vector mem1, a[2];
    bwtintv_t ik{}, ok[4]{};
    const int split_len = int(opt->min_seed_len * opt->split_factor + .499);
    const int start_width = 1;
    mem.clear();
    // Passes 1 and 2 call bwt_smem1() (bwt_smem1a() with max_intv 0) on the same body.
    int pass = 1, x = 0;
    size_t k = 0, old_n = 0;
    for (;;) {
        int sx, min_intv;
        if (pass == 1) {
            while (x < len && seq[x] >= 4) ++x;
            if (x >= len) {
                pass = 2;
                old_n = mem.size();
                continue;
            }
            sx = x;
            min_intv = start_width;
        } else {
            for (; k < old_n; ++k) {
                const bwtintv_t *p = &mem[k];
                const int start = int(p->info >> 32), end = int32_t(p->info);
                if (end - start < split_len || p->x[2] > uint64_t(opt->split_width)) continue;
                break;
            }
            if (k >= old_n) break;
            const bwtintv_t *p = &mem[k];
            const int start = int(p->info >> 32), end = int32_t(p->info);
            sx = (start + end) >> 1;
            min_intv = int(p->x[2] + 1);
            ++k;
        }

        // bwt_smem1a(bwt, len, q, sx, min_intv, 0, &mem1, tmpvec)
        int ret;
        {
            const uint64_t max_intv = 0;
            int i, j, c;
            Vector *prev = &a[0], *curr = &a[1], *swap;
            mem1.clear();
            if (q[sx] > 3) {
                ret = sx + 1;
            } else {
                if (min_intv < 1) min_intv = 1;
                bwt_set_intv(bwt, q[sx], ik);
                ik.info = uint64_t(sx + 1);

                for (i = sx + 1, curr->clear(); i < len; ++i) {
                    if (ik.x[2] < max_intv) {
                        curr->push_back(ik);
                        break;
                    } else if (q[i] < 4) {
                        c = 3 - q[i];
                        co_await fetch(bwt, ik, 0);
                        bwt_extend(bwt, &ik, ok, 0);
                        if (ok[c].x[2] != ik.x[2]) {
                            curr->push_back(ik);
                            if (ok[c].x[2] < uint64_t(min_intv)) break;
                        }
                        ik = ok[c];
                        ik.info = uint64_t(i + 1);
                    } else {
                        curr->push_back(ik);
                        break;
                    }
                }
                if (i == len) curr->push_back(ik);
                std::reverse(curr->begin(), curr->end());
                ret = int((*curr)[0].info);
                swap = curr, curr = prev, prev = swap;

                for (i = sx - 1; i >= -1; --i) {
                    c = i < 0 ? -1 : q[i] < 4 ? q[i] : -1;
                    curr->clear();
                    for (j = 0; j < int(prev->size()); ++j) {
                        bwtintv_t *p = &(*prev)[size_t(j)];
                        if (c >= 0 && ik.x[2] >= max_intv) {
                            co_await fetch(bwt, *p, 1);
                            bwt_extend(bwt, p, ok, 1);
                        }
                        if (c < 0 || ik.x[2] < max_intv || ok[c].x[2] < uint64_t(min_intv)) {
                            if (curr->empty()) {
                                if (mem1.empty() || uint64_t(i + 1) < mem1.back().info >> 32) {
                                    ik = *p;
                                    ik.info |= uint64_t(i + 1) << 32;
                                    mem1.push_back(ik);
                                }
                            }
                        } else if (curr->empty() || ok[c].x[2] != curr->back().x[2]) {
                            ok[c].info = p->info;
                            curr->push_back(ok[c]);
                        }
                    }
                    if (curr->empty()) break;
                    swap = curr, curr = prev, prev = swap;
                }
                std::reverse(mem1.begin(), mem1.end());
            }
        }
        if (pass == 1) {
            x = ret;
            for (const bwtintv_t &p : mem1) {
                const int slen = int(uint32_t(p.info) - (p.info >> 32));
                if (slen >= opt->min_seed_len) mem.push_back(p);
            }
        } else {
            for (const bwtintv_t &p : mem1)
                if (uint32_t(p.info) - (p.info >> 32) >= uint64_t(int64_t(opt->min_seed_len)))
                    mem.push_back(p);
        }
    }

    // Third pass: bwt_seed_strategy1(bwt, len, seq, x, min_seed_len, max_mem_intv, &m).
    if (opt->max_mem_intv > 0) {
        x = 0;
        while (x < len) {
            if (seq[x] < 4) {
                const int min_len = opt->min_seed_len, max_intv = opt->max_mem_intv;
                bwtintv_t m{};
                int i, c, next = len;
                bwt_set_intv(bwt, q[x], ik);
                for (i = x + 1; i < len; ++i) {
                    if (q[i] < 4) {
                        c = 3 - q[i];
                        co_await fetch(bwt, ik, 0);
                        bwt_extend(bwt, &ik, ok, 0);
                        if (ok[c].x[2] < uint64_t(int64_t(max_intv)) && i - x >= min_len) {
                            m = ok[c];
                            m.info = uint64_t(x) << 32 | uint64_t(i + 1);
                            next = i + 1;
                            break;
                        }
                        ik = ok[c];
                    } else {
                        next = i + 1;
                        break;
                    }
                }
                x = next;
                if (m.x[2] > 0) mem.push_back(m);
            } else
                ++x;
        }
    }
    wgse_sort_intervals(mem.size(), mem.data());
}
} // namespace

void collect(const mem_opt_t *opt, const bwt_t *bwt, Read *reads, size_t count) {
    constexpr size_t ways = 16;
    std::coroutine_handle<Walk::promise_type> walks[ways];
    size_t next = 0, active = 0;
    auto start = [&]() {
        Read &read = reads[next++];
        return collect_one(opt, bwt, read.length, read.seq, *read.out).handle;
    };
    while (active < ways && next < count) walks[active++] = start();
    while (active) {
        for (size_t i = 0; i < active;) {
            walks[i].resume();
            if (!walks[i].done()) {
                ++i;
                continue;
            }
            walks[i].destroy();
            if (next < count)
                walks[i++] = start();
            else
                walks[i] = walks[--active];
        }
    }
}
} // namespace wgse::smem
