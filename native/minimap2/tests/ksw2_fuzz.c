/* Compare wgse_ksw2_extd2.c (ksw_extd2_sse written for NEON) with upstream's ksw2_extd2_sse.c,
 * compiled as upstream_ksw_extd2, on random alignments: every flag combination, generic and
 * simple scoring (including scores that wrap int8 the same way in both), bands, Z-drop and
 * end bonus, on random and related sequences with ambiguous bases. Exits 1 on any difference
 * in the score fields or CIGAR.
 * Usage: ksw2_fuzz [CALLS] [SEED] */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ksw2.h"
#include "kalloc.h"

void upstream_ksw_extd2(void *km, int qlen, const uint8_t *query, int tlen, const uint8_t *target, int8_t m, const int8_t *mat,
						int8_t q, int8_t e, int8_t q2, int8_t e2, int w, int zdrop, int end_bonus, int flag, ksw_extz_t *ez);

static uint64_t rng;
static uint32_t rnd(uint32_t n) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)(rng >> 16) % n; }

static int same(const ksw_extz_t *a, const ksw_extz_t *b)
{
	return a->max == b->max && a->zdropped == b->zdropped && a->max_q == b->max_q && a->max_t == b->max_t
		&& a->mqe == b->mqe && a->mqe_t == b->mqe_t && a->mte == b->mte && a->mte_q == b->mte_q
		&& a->score == b->score && a->reach_end == b->reach_end && a->n_cigar == b->n_cigar
		&& (a->n_cigar == 0 || memcmp(a->cigar, b->cigar, a->n_cigar * 4) == 0);
}

int main(int argc, char **argv)
{
	long n = argc > 1 ? atol(argv[1]) : 20000, i, bad = 0;
	void *km = km_init();
	ksw_extz_t ea, eb;
	rng = (argc > 2 ? strtoull(argv[2], 0, 10) : 1) * 2654435761ULL + 88172645463325252ULL;
	memset(&ea, 0, sizeof ea); memset(&eb, 0, sizeof eb);
	for (i = 0; i < n; ++i) {
		int qlen = 1 + rnd(rnd(4) ? 300 : 1500), tlen = 1 + rnd(rnd(4) ? 300 : 1500), k, j, flag = 0;
		int m = rnd(100) == 0 ? (int)rnd(2) : 5; /* m <= 1 and empty sequences return early */
		if (rnd(100) == 0) qlen = 0;
		if (rnd(100) == 0) tlen = 0;
		int a = 1 + rnd(10), b = 1 + rnd(12), q = rnd(40), e = 1 + rnd(10), q2 = rnd(60), e2 = 1 + rnd(6), amb = rnd(3);
		int w = rnd(3) ? -1 : (int)rnd(400), zdrop = rnd(2) ? -1 : (int)rnd(400), end_bonus = rnd(20), div = rnd(4);
		uint8_t *qs = malloc(qlen), *ts = malloc(tlen);
		int8_t mat[25];
		if (rnd(8) == 0) a = 1 + rnd(60), q = rnd(70), q2 = rnd(70);
		if (q + e + q2 + e2 > 127) q2 = 0; /* minimap2 caps the sum */
		for (k = 0; k < 5; ++k)
			for (j = 0; j < 5; ++j)
				mat[k * 5 + j] = k == 4 || j == 4 ? -amb : k == j ? a : -b;
		if (rnd(10) == 0) {
			for (k = 0; k < 25; ++k) mat[k] = (int8_t)rnd(13) - 8;
			flag |= KSW_EZ_GENERIC_SC;
		}
		if (rnd(2)) flag |= KSW_EZ_APPROX_MAX;
		if (rnd(3) == 0) flag |= KSW_EZ_SCORE_ONLY;
		if (rnd(3) == 0) flag |= KSW_EZ_RIGHT;
		if (rnd(3) == 0) flag |= KSW_EZ_EXTZ_ONLY;
		if (rnd(4) == 0) flag |= KSW_EZ_REV_CIGAR;
		if (rnd(4) == 0) flag |= KSW_EZ_APPROX_DROP;
		for (k = 0; k < tlen; ++k) ts[k] = rnd(50) == 0 ? 4 : rnd(4);
		for (k = 0; k < qlen; ++k) /* random, or a mutated copy of the target */
			qs[k] = tlen == 0 || div == 0 || rnd(div * 8) == 0 ? rnd(5) : ts[(long)k * tlen / qlen];
		upstream_ksw_extd2(km, qlen, qs, tlen, ts, m, mat, q, e, q2, e2, w, zdrop, end_bonus, flag, &ea);
		ksw_extd2_sse(km, qlen, qs, tlen, ts, m, mat, q, e, q2, e2, w, zdrop, end_bonus, flag, &eb);
		if (!same(&ea, &eb) && bad++ < 10)
			fprintf(stderr, "call %ld differs: qlen=%d tlen=%d a=%d b=%d q=%d e=%d q2=%d e2=%d w=%d zdrop=%d end_bonus=%d flag=%d\n",
					i, qlen, tlen, a, b, q, e, q2, e2, w, zdrop, end_bonus, flag);
		free(qs); free(ts);
	}
	kfree(km, ea.cigar); kfree(km, eb.cigar);
	km_destroy(km);
	printf("%ld ksw_extd2 calls, %ld differ from upstream\n", n, bad);
	return bad != 0;
}
