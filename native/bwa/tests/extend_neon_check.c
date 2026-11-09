/* ksw_extend2() (extend_neon.c.in's NEON version when scores fit 16 bits) must
 * return exactly what upstream's ksw_extend2() returns, with every output:
 * random scoring, gap penalties, bands, end bonuses, z-drop, start scores,
 * query and target lengths, ambiguous bases, and planted (imperfect) matches. */
#include "../ksw_batch.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint64_t rng = 0x2545F4914F6CDD1DULL;
static uint32_t rnd(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)rng; }

int main(int argc, char **argv)
{
	int rounds = argc > 1 ? atoi(argv[1]) : 20000, bad = 0, n_zdrop = 0, n_wide = 0, n_long = 0;
	uint8_t ref[4000];
	for (int i = 0; i < 4000; ++i) ref[i] = rnd() % 4;
	for (int round = 0; round < rounds; ++round) {
		int8_t mat[25];
		int a = 1 + rnd() % 4, b = rnd() % 8 ? 1 + rnd() % 8 : 10 + rnd() % 30, k = 0, i, j;
		int o_del = rnd() % 12, e_del = 1 + rnd() % 4, o_ins = rnd() % 12, e_ins = 1 + rnd() % 4;
		if (rnd() % 3 == 0) { a = 1; b = 4; o_del = o_ins = 6; e_del = e_ins = 1; }
		for (i = 0; i < 4; ++i) { for (j = 0; j < 4; ++j) mat[k++] = i == j ? a : -b; mat[k++] = -1; }
		for (j = 0; j < 5; ++j) mat[k++] = -1;
		int ql = rnd() % 8 ? 1 + rnd() % 160 : 1 + rnd() % 700;
		int tl = rnd() % 8 ? ql + rnd() % 120 : 1 + rnd() % 900;
		uint8_t *q = malloc(ql), *tg = malloc(tl);
		int src = rnd() % 4000, dst = rnd() % 4000, errors = rnd() % 3 ? 12 + rnd() % 30 : 1 << 30;
		int ambiguous = rnd() % 4 == 0;
		for (i = 0; i < ql; ++i) q[i] = ambiguous && rnd() % 40 == 0 ? 4 : ref[(src + i) % 4000];
		for (i = 0; i < tl; ++i) tg[i] = ambiguous && rnd() % 40 == 0 ? 4 : ref[(dst + i) % 4000];
		if (rnd() % 4) { // the query continues into the target, with errors and indels
			int qi = 0, ti = 0;
			while (qi < ql && ti < tl) {
				int r = rnd() % errors;
				if (r == 0) tg[ti++] = rnd() % 4, ++qi;
				else if (r == 1 && rnd() % 2) ti += 1 + rnd() % 3;
				else if (r == 1) qi += 1 + rnd() % 3;
				else tg[ti++] = q[qi++] % 4;
			}
		}
		int w = rnd() % 4 ? 100 : 1 + rnd() % 200, end_bonus = rnd() % 3 ? 5 : rnd() % 20;
		int zdrop = rnd() % 4 ? 100 : rnd() % 3 ? 0 : 1 + rnd() % 60;
		int h0 = rnd() % 4 ? 19 * a + rnd() % 200 : 1 + rnd() % 2000;
		int want[6], got[6];
		want[0] = wgse_ksw_extend2_upstream(ql, q, tl, tg, 5, mat, o_del, e_del, o_ins, e_ins, w,
			end_bonus, zdrop, h0, &want[1], &want[2], &want[3], &want[4], &want[5]);
		got[0] = ksw_extend2(ql, q, tl, tg, 5, mat, o_del, e_del, o_ins, e_ins, w, end_bonus,
			zdrop, h0, &got[1], &got[2], &got[3], &got[4], &got[5]);
		n_zdrop += zdrop > 0 && want[2] < tl && want[1] < ql;
		n_wide += w > 100;
		n_long += ql > 512;
		if (memcmp(want, got, sizeof(want)) && bad++ < 5)
			printf("mismatch round %d ql=%d tl=%d w=%d h0=%d zdrop=%d: %d,%d,%d,%d,%d,%d vs %d,%d,%d,%d,%d,%d\n",
				round, ql, tl, w, h0, zdrop, got[0], got[1], got[2], got[3], got[4], got[5],
				want[0], want[1], want[2], want[3], want[4], want[5]);
		free(q);
		free(tg);
	}
	printf("calls=%d zdrop_stops=%d wide_bands=%d long_queries=%d mismatches=%d\n", rounds, n_zdrop,
		n_wide, n_long, bad);
	return bad || !n_zdrop || !n_wide || !n_long;
}
