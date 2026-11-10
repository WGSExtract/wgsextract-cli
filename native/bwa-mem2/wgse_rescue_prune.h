#pragma once

#include <stdint.h>

// bwa-mem3's exact mate-rescue window pruning (its src/rescue_prune.h, MIT),
// for mem_matesw()'s ksw_align2(). From the exact K-mer matches between the
// mate q (len2 bases, oriented as aligned) and the reference window (len1
// bases), it proves which rows of the window can hold a row maximum of at least
// minsc (min_seed_len * a; mem_matesw() keeps nothing below it):
//   wgse_prune_none: none can, so ksw_align2() would return nothing used;
//   wgse_prune_rows: only rows [*hb, *he], and ksw_align2() on them, started
//     from zero state, gives the same score, ends, starts and suboptimal score;
//   wgse_prune_full: nothing proven (N bases, a scoring the bound does not hold
//     for, or a window too dense with matches to be worth it).
enum { wgse_prune_full = 0, wgse_prune_none = 1, wgse_prune_rows = 2 };
#ifdef __cplusplus
extern "C" {
#endif
int wgse_rescue_prune(int a, int b, int o_del, int e_del, int o_ins, int e_ins, int minsc,
    const uint8_t *ref, int len1, const uint8_t *q, int len2, int *hb, int *he);
#ifdef __cplusplus
}
#endif
