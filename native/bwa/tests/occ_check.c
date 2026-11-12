/* bwt_occ4() and bwt_2occ4() (occ_oracle.c.in) must return the counts of each
 * base up to k: checked against counting a random BWT directly, for every k
 * (including -1 and those around the primary row and block edges) and for
 * pairs in the same and in different occurrence blocks. */
#include "bwt.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint64_t rng = 0x2545F4914F6CDD1DULL;
static uint32_t rnd(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)rng; }

int main(int argc, char **argv)
{
	const bwtint_t n = argc > 1 ? (bwtint_t)atoll(argv[1]) : 100003; /* bases besides $ */
	uint8_t *s = malloc(n);
	bwtint_t (*want)[4] = malloc((n + 1) * sizeof(*want)), i, j, bad = 0, checked = 0;
	bwt_t bwt;
	memset(&bwt, 0, sizeof(bwt));
	for (i = 0; i < n; ++i) s[i] = rnd() % (i % 1000 < 300 ? 1 : 4); /* runs of A too */
	bwt.seq_len = n;
	bwt.primary = rnd() % (n + 1);
	/* Upstream's layout: per 128 bases, the counts before the block, then 8 words. */
	bwt.bwt_size = (n + 127) / 128 * 16;
	bwt.bwt = calloc(bwt.bwt_size, 4);
	{
		bwtint_t c[4] = {0, 0, 0, 0};
		for (i = 0; i < n; ++i) {
			uint32_t *block = bwt.bwt + i / 128 * 16;
			if (i % 128 == 0) memcpy(block, c, sizeof(c));
			block[8 + i % 128 / 16] |= (uint32_t)s[i] << ((15 - i % 16) * 2);
			++c[s[i]];
			memcpy(want[i + 1], c, sizeof(c)); /* want[x + 1]: counts of s[0..x] */
		}
		memset(want[0], 0, sizeof(want[0]));
	}
	/* The counts bwt_occ4(k) returns: of s[0..k - (k >= primary)], none for k == -1. */
#define WANT(k) ((k) == (bwtint_t)-1 ? want[0] : want[(k) - ((k) >= bwt.primary) + 1])
	for (i = (bwtint_t)-1; i != n + 1; ++i) {
		bwtint_t got[4];
		bwt_occ4(&bwt, i, got);
		bad += memcmp(got, WANT(i), sizeof(got)) != 0;
		++checked;
	}
	for (j = 0; j < 2000000; ++j) {
		bwtint_t k = rnd() % (n + 2) - 1, l = rnd() % 4 ? k + rnd() % 300 : rnd() % (n + 1), gk[4], gl[4];
		if (l != (bwtint_t)-1 && l > n) l = n;
		if (k != (bwtint_t)-1 && (l == (bwtint_t)-1 || k > l)) { bwtint_t t = k; k = l; l = t; }
		bwt_2occ4(&bwt, k, l, gk, gl);
		bad += memcmp(gk, WANT(k), sizeof(gk)) != 0 || memcmp(gl, WANT(l), sizeof(gl)) != 0;
		++checked;
	}
	printf("occ checks=%llu wrong=%llu\n", (unsigned long long)checked, (unsigned long long)bad);
	free(bwt.bwt), free(want), free(s);
	return bad != 0;
}
