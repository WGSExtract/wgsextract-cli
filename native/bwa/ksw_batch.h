#pragma once

#include "ksw.h"

#ifdef __cplusplus
extern "C" {
#endif

// ksw_align2() for n tasks at once with upstream's exact results (ksw_oracle.c.in),
// for wgse-bwa and wgse-bwa-mem2.
typedef struct {
    int qlen, tlen, xtra;
    uint8_t *query, *target;
    kswr_t r;
} wgse_ksw_task_t;
void wgse_ksw_align2_batch(int n, wgse_ksw_task_t *tasks, int m, const int8_t *mat,
    int o_del, int e_del, int o_ins, int e_ins);

// Upstream's ksw_extend2(); ksw_extend2() itself runs an exact NEON version
// when the scores fit 16 bits and the CPU replacements are on (extend_neon.c.in).
int wgse_ksw_extend2_upstream(int qlen, const uint8_t *query, int tlen, const uint8_t *target,
    int m, const int8_t *mat, int o_del, int e_del, int o_ins, int e_ins, int w, int end_bonus,
    int zdrop, int h0, int *qle, int *tle, int *gtle, int *gscore, int *max_off);
// Whether ksw_extend2() runs the NEON version for these inputs (extend_neon.c.in).
int wgse_extend_fits(int qlen, int m, const int8_t *mat, int o_del, int e_del, int o_ins,
    int e_ins, int h0);

#ifdef __cplusplus
}
#endif
