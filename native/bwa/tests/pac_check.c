/* wgse_unpack_pac() and wgse_unpack_pac_reverse() (pac_oracle.c.in) must give
 * bns_get_seq()'s loops' bases for every range: checked against _get_pac() on
 * a random packed sequence, for ranges of every length up to 300 at every
 * alignment, and long ones. */
#include "metal_backend.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* bntseq.c's */
#define _set_pac(pac, l, c) ((pac)[(l)>>2] |= (c)<<((~(l)&3)<<1))
#define _get_pac(pac, l) ((pac)[(l)>>2]>>((~(l)&3)<<1)&3)

static uint64_t rng = 0x2545F4914F6CDD1DULL;
static uint32_t rnd(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)rng; }

int main(void)
{
	enum { n = 40000 };
	uint8_t *pac = calloc(n / 4, 1), *seq = malloc(n + 64), *want = malloc(n);
	int64_t beg, end, k, checks = 0, wrong = 0;
	for (k = 0; k < n; ++k) _set_pac(pac, k, rnd() % 4);
	for (int round = 0; round < 60000; ++round) {
		int64_t len = round < 50000 ? round % 301 : (int64_t)(rnd() % (n - 1));
		beg = rnd() % (n - len), end = beg + len;
		for (k = beg; k < end; ++k) want[k - beg] = _get_pac(pac, k);
		memset(seq, 9, (size_t)len + 64);
		wgse_unpack_pac(pac, beg, end, seq);
		wrong += memcmp(seq, want, (size_t)len) != 0 || seq[len] != 9;
		/* bns_get_seq()'s reverse strand: 3 - _get_pac() from end down to beg + 1 */
		for (k = end; k > beg; --k) want[end - k] = 3 - _get_pac(pac, k);
		memset(seq, 9, (size_t)len + 64);
		wgse_unpack_pac_reverse(pac, beg, end, seq);
		wrong += memcmp(seq, want, (size_t)len) != 0 || seq[len] != 9;
		checks += 2;
	}
	printf("pac checks=%lld wrong=%lld\n", (long long)checks, (long long)wrong);
	free(pac), free(seq), free(want);
	return wrong != 0;
}
