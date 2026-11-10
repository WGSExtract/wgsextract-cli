/* Compare radix_sort_128x (wgse_sort128.inc, the out-of-place replay) with upstream minimap2's
 * in-place radix sort (ksort.h's KRADIX_SORT_INIT, here radix_sort_up128x) on random anchor
 * arrays: many equal keys, keys that agree on their high bytes (skipped passes), bytes with
 * only two values (two-digit passes), one to many buckets per level, and sizes around
 * RS_MIN_SIZE (64), WGSE_RS_MIN (256) and larger. The sort is not stable, so the order of
 * equal keys matters: each element's y holds its input position, and the outputs must be
 * byte-identical. Exits 1 on any difference.
 * Usage: sort128_fuzz [ARRAYS] [SEED] */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "minimap.h"
#include "wgse_ksort.h"

#define sort_key_128x(a) ((a).x)
KRADIX_SORT_INIT(up128x, mm128_t, sort_key_128x, 8)
WGSE_KRADIX_SORT_BASE(128x, mm128_t, sort_key_128x)
#include "wgse_sort128.inc"

static uint64_t rng;
static uint64_t rnd64(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return rng; }
static uint32_t rnd(uint32_t n) { return (uint32_t)(rnd64() >> 16) % n; }

int main(int argc, char **argv)
{
	long n_arrays = argc > 1 ? atol(argv[1]) : 3000, a, bad = 0;
	rng = (argc > 2 ? strtoull(argv[2], 0, 10) : 1) * 2654435761ULL + 88172645463325252ULL;
	for (a = 0; a < n_arrays; ++a) {
		const int kind = rnd(8);
		int64_t n = kind == 0 ? 1 + rnd(80) : kind <= 3 ? 200 + rnd(120) : kind <= 6 ? 300 + rnd(5000) : 20000 + rnd(300000);
		uint64_t base = rnd64(), vals[8][4];
		int nv[8], b, k;
		int64_t i;
		mm128_t *x = malloc(n * sizeof(mm128_t)), *y = malloc(n * sizeof(mm128_t));
		/* Per key byte: constant (a skipped pass), two values (a two-digit pass), a few, or any. */
		for (b = 0; b < 8; ++b) {
			const int r = rnd(10);
			nv[b] = r < 4 ? 1 : r < 6 ? 2 : r < 8 ? 3 + rnd(2) : 0;
			for (k = 0; k < 4; ++k) vals[b][k] = rnd(256);
		}
		for (i = 0; i < n; ++i) {
			uint64_t key = 0;
			for (b = 0; b < 8; ++b) {
				const uint64_t v = nv[b] == 0 ? rnd(256) : nv[b] == 1 ? (base >> (8 * b) & 0xff) : vals[b][rnd(nv[b])];
				key |= v << (8 * b);
			}
			if (i > 0 && rnd(4) == 0) key = x[rnd((uint32_t)i)].x; /* more equal keys */
			x[i].x = key, x[i].y = (uint64_t)i;
		}
		memcpy(y, x, n * sizeof(mm128_t));
		radix_sort_up128x(x, x + n);
		radix_sort_128x(y, y + n);
		if (memcmp(x, y, n * sizeof(mm128_t)) != 0 && bad++ < 10)
			fprintf(stderr, "array %ld (n=%lld) differs\n", a, (long long)n);
		free(x); free(y);
	}
	printf("%ld arrays, %ld differ from upstream's sort\n", n_arrays, bad);
	return bad != 0;
}
