/* radix_sort_128x with the same result as upstream's in-place MSD radix sort
 * (WGSE_KRADIX_SORT_INIT(128x, ...), included after it in misc.c), faster.
 *
 * That sort is not stable, and minimap2's chaining depends on the order it leaves
 * anchors with equal keys in, so this one reproduces each of its passes exactly.
 * In a pass, upstream's cycle chasing takes the next unplaced element of the region
 * of the array where bucket `cur` ends up, places it in bucket d (its digit), and
 * continues in region d; when region d is used up it continues in the lowest region
 * not used up. Each bucket's elements land in the order they are taken. This code
 * replays that walk on a byte array of digits, records where each element goes, then
 * moves them out of place in one gather. The walk is a chain of dependent loads, so
 * up to WGSE_RS_LANES sibling buckets walk together; a pass with only two digits keeps
 * its walk in registers. Passes, the skipping of digits on which a bucket's keys all
 * agree, and the insertion sort of buckets of at most RS_MIN_SIZE elements follow
 * upstream's, so every pass sees the same elements in the same order. */
#include <pthread.h>

#define WGSE_RS_LANES 4
#define WGSE_RS_LEVELS 8
#ifndef WGSE_RS_MIN
#define WGSE_RS_MIN 256          /* smaller arrays use the in-place sort */
#endif
#define WGSE_RS_MAX (1 << 22)    /* larger ones too, to bound the scratch memory (21 bytes per element) */

typedef struct {
	int64_t off;                 /* bucket's offset in the arrays */
	uint32_t n;
	int s, in_a, cur, low, nd;   /* digit shift, data in A, walk state, number of digits */
	uint8_t dg[256];             /* the digits present, ascending */
	uint32_t head[256], tail[256], end[256]; /* per present digit: next to take, next to fill, region end */
	uint64_t any[256], all[256]; /* OR and AND of each child bucket's keys */
} wgse_rs_lane_t;

typedef struct {
	mm128_t *A, *B;              /* the array, and scratch of the same size */
	uint8_t *D;                  /* digits */
	uint32_t *P;                 /* source of each destination slot */
	int64_t cap;
	wgse_rs_lane_t lane[WGSE_RS_LEVELS][WGSE_RS_LANES];
} wgse_rs_t;

static pthread_key_t wgse_rs_key;
static pthread_once_t wgse_rs_once = PTHREAD_ONCE_INIT;

static void wgse_rs_free(void *p)
{
	wgse_rs_t *w = (wgse_rs_t*)p;
	if (w == 0) return;
	free(w->B); free(w->D); free(w->P); free(w);
}

static void wgse_rs_key_init(void) { pthread_key_create(&wgse_rs_key, wgse_rs_free); }

/* The calling thread's scratch for n elements, or NULL. */
static wgse_rs_t *wgse_rs_scratch(int64_t n)
{
	wgse_rs_t *w;
	pthread_once(&wgse_rs_once, wgse_rs_key_init);
	w = (wgse_rs_t*)pthread_getspecific(wgse_rs_key);
	if (w == 0) {
		if ((w = (wgse_rs_t*)calloc(1, sizeof(wgse_rs_t))) == 0) return 0;
		pthread_setspecific(wgse_rs_key, w);
	}
	if (n > w->cap) {
		int64_t cap = n > w->cap * 3 / 2? n : w->cap * 3 / 2;
		if (cap > WGSE_RS_MAX) cap = WGSE_RS_MAX; /* n <= WGSE_RS_MAX */
		free(w->B); free(w->D); free(w->P);
		w->B = (mm128_t*)malloc(cap * sizeof(mm128_t));
		w->D = (uint8_t*)malloc(cap);
		w->P = (uint32_t*)malloc(cap * sizeof(uint32_t));
		if (w->B == 0 || w->D == 0 || w->P == 0) {
			free(w->B); free(w->D); free(w->P);
			w->B = 0, w->D = 0, w->P = 0, w->cap = 0;
			return 0;
		}
		w->cap = cap;
	}
	return w;
}

/* Count the lane's digits at L->s and lay out its buckets. */
static void wgse_rs_prepare(wgse_rs_t *w, wgse_rs_lane_t *L)
{
	uint32_t c0[256], c1[256], off = 0;
	const mm128_t *__restrict a = (L->in_a? w->A : w->B) + L->off;
	uint8_t *__restrict D = w->D + L->off;
	uint32_t i, n = L->n;
	int s = L->s, c, k;
	memset(c0, 0, sizeof(c0));
	memset(c1, 0, sizeof(c1));
	for (i = 0; i + 1 < n; i += 2) { /* two tables: runs of one digit do not serialize */
		uint8_t d0 = (uint8_t)(a[i].x >> s), d1 = (uint8_t)(a[i+1].x >> s);
		D[i] = d0, D[i+1] = d1, ++c0[d0], ++c1[d1];
	}
	if (i < n) {
		uint8_t d0 = (uint8_t)(a[i].x >> s);
		D[i] = d0, ++c0[d0];
	}
	for (c = 0, L->nd = 0; c < 256; c += 64) {
		uint64_t b = 0;
		for (k = 0; k < 64; ++k) /* vectorized: which digits are present */
			c0[c + k] += c1[c + k], b |= (uint64_t)(c0[c + k] != 0) << k;
		while (b) {
			int d = c | __builtin_ctzll(b);
			b &= b - 1;
			L->dg[L->nd++] = (uint8_t)d;
			L->head[d] = L->tail[d] = off, off += c0[d], L->end[d] = off;
		}
	}
	L->low = 0, L->cur = L->dg[0];
}

#define WGSE_RS_DECL(X, l) \
	wgse_rs_lane_t *X##L = (l); \
	uint32_t *__restrict X##h = X##L->head, *__restrict X##t = X##L->tail; \
	const uint32_t *__restrict X##e = X##L->end; \
	const uint8_t *__restrict X##D = w->D + X##L->off; \
	uint32_t *__restrict X##P = w->P + X##L->off; \
	int X##c = X##L->cur
#define WGSE_RS_STEP(X) do { \
		uint32_t h_ = X##h[X##c]; \
		int d_; \
		if (__builtin_expect(h_ == X##e[X##c], 0)) { /* region used up */ \
			while (X##h[X##L->dg[X##L->low]] == X##e[X##L->dg[X##L->low]]) ++X##L->low; \
			X##c = X##L->dg[X##L->low], h_ = X##h[X##c]; \
		} \
		X##h[X##c] = h_ + 1; \
		d_ = X##D[h_]; \
		X##P[X##t[d_]++] = h_; \
		X##c = d_; \
	} while (0)

static void wgse_rs_walk1(wgse_rs_t *w, wgse_rs_lane_t *l, uint32_t steps)
{
	uint32_t k;
	WGSE_RS_DECL(p, l);
	for (k = 0; k < steps; ++k) WGSE_RS_STEP(p);
	pL->cur = pc;
}

/* The walk of a pass with exactly two digits c0 < c1. */
static void wgse_rs_walk2(wgse_rs_t *w, wgse_rs_lane_t *l)
{
	const int c0 = l->dg[0], c1 = l->dg[1];
	const uint8_t *__restrict D = w->D + l->off;
	uint32_t *__restrict P = w->P + l->off;
	uint32_t h0 = l->head[c0], h1 = l->head[c1], e0 = l->end[c0], e1 = l->end[c1], t0 = h0, t1 = h1, k;
	int cur = 0;
	for (k = 0; k < l->n; ++k) {
		uint32_t h = cur? h1 : h0;
		int d = D[h] == c1, ex;
		h0 += !cur, h1 += cur;
		P[d? t1 : t0] = h;
		t0 += !d, t1 += d;
		ex = d? h1 == e1 : h0 == e0;
		cur = ex? h0 >= e0 : d;
	}
}

static void wgse_rs_walk(wgse_rs_t *w, wgse_rs_lane_t **a, int n)
{
	uint32_t k, m;
	int i;
	if (n == 4) {
		WGSE_RS_DECL(p, a[0]); WGSE_RS_DECL(q, a[1]); WGSE_RS_DECL(r, a[2]); WGSE_RS_DECL(u, a[3]);
		for (i = 1, m = a[0]->n; i < 4; ++i) if (a[i]->n < m) m = a[i]->n;
		for (k = 0; k < m; ++k) { WGSE_RS_STEP(p); WGSE_RS_STEP(q); WGSE_RS_STEP(r); WGSE_RS_STEP(u); }
		pL->cur = pc, qL->cur = qc, rL->cur = rc, uL->cur = uc;
		for (i = 0; i < 4; ++i) wgse_rs_walk1(w, a[i], a[i]->n - m);
	} else if (n >= 2) {
		WGSE_RS_DECL(p, a[0]); WGSE_RS_DECL(q, a[1]);
		m = a[0]->n < a[1]->n? a[0]->n : a[1]->n;
		for (k = 0; k < m; ++k) { WGSE_RS_STEP(p); WGSE_RS_STEP(q); }
		pL->cur = pc, qL->cur = qc;
		for (i = 0; i < 2; ++i) wgse_rs_walk1(w, a[i], a[i]->n - m);
		if (n == 3) wgse_rs_walk1(w, a[2], a[2]->n);
	} else if (n == 1) wgse_rs_walk1(w, a[0], a[0]->n);
}

/* A bucket no pass will touch: move it to A, insertion-sorted if upstream sorts it. */
static void wgse_rs_leaf(wgse_rs_t *w, int64_t off, int64_t n, int in_a, int sort)
{
	if (in_a) {
		if (sort && n > 1) rs_insertsort_128x(w->A + off, w->A + off + n);
	} else if (!sort || n == 1) {
		memcpy(w->A + off, w->B + off, n * sizeof(mm128_t));
	} else { /* the same stable insertion sort, from B into A */
		const mm128_t *__restrict s = w->B + off;
		mm128_t *__restrict d = w->A + off;
		int64_t i, j;
		d[0] = s[0];
		for (i = 1; i < n; ++i) {
			mm128_t t = s[i];
			for (j = i; j > 0 && sort_key_128x(t) < sort_key_128x(d[j-1]); --j) d[j] = d[j-1];
			d[j] = t;
		}
	}
}

static void wgse_rs_group(wgse_rs_t *w, int level, int n);

/* Queue a bucket for its pass at the highest shift at most s at which its keys
 * differ (upstream skips the passes in between, which leave the order unchanged). */
static void wgse_rs_add(wgse_rs_t *w, int level, int *ng, int64_t off, int64_t n, int in_a, int s, uint64_t diff)
{
	wgse_rs_lane_t *g;
	while (s > 0 && (diff >> s & 0xff) == 0) s -= 8;
	if ((diff >> s & 0xff) == 0) {
		wgse_rs_leaf(w, off, n, in_a, 0);
		return;
	}
	g = &w->lane[level][*ng];
	g->off = off, g->n = (uint32_t)n, g->s = s, g->in_a = in_a;
	wgse_rs_prepare(w, g);
	if (++*ng == WGSE_RS_LANES) {
		wgse_rs_group(w, level, *ng);
		*ng = 0;
	}
}

/* Run the passes of the level's queued buckets, then of their children. */
static void wgse_rs_group(wgse_rs_t *w, int level, int n)
{
	wgse_rs_lane_t *a[WGSE_RS_LANES], *L = w->lane[level];
	int i, k, m = 0, ng = 0;
	for (i = 0; i < n; ++i) {
		if (L[i].nd == 2) wgse_rs_walk2(w, &L[i]);
		else a[m++] = &L[i];
	}
	wgse_rs_walk(w, a, m);
	for (i = 0; i < n; ++i) {
		wgse_rs_lane_t *l = &L[i];
		const mm128_t *__restrict src = (l->in_a? w->A : w->B) + l->off;
		mm128_t *__restrict dst = (l->in_a? w->B : w->A) + l->off;
		const uint32_t *__restrict P = w->P + l->off;
		uint32_t lo = 0, hi, j;
		for (k = 0; k < l->nd; ++k, lo = hi) {
			uint64_t any = 0, all = ~(uint64_t)0;
			hi = l->end[l->dg[k]];
			for (j = lo; j < hi; ++j) {
				mm128_t e = src[P[j]];
				dst[j] = e, any |= sort_key_128x(e), all &= sort_key_128x(e);
			}
			l->any[k] = any, l->all[k] = all;
		}
	}
	for (i = 0; i < n; ++i) {
		wgse_rs_lane_t *l = &L[i];
		uint32_t lo = 0, hi;
		for (k = 0; k < l->nd; ++k, lo = hi) {
			hi = l->end[l->dg[k]];
			if (l->s && hi - lo > RS_MIN_SIZE)
				wgse_rs_add(w, level + 1, &ng, l->off + lo, hi - lo, !l->in_a, l->s - 8, l->any[k] ^ l->all[k]);
			else wgse_rs_leaf(w, l->off + lo, hi - lo, !l->in_a, l->s != 0);
		}
	}
	if (ng) wgse_rs_group(w, level + 1, ng);
}

void radix_sort_128x(mm128_t *beg, mm128_t *end)
{
	int64_t n = end - beg, i;
	uint64_t any = 0, all = ~(uint64_t)0;
	wgse_rs_t *w;
	int ng = 0;
	if (n <= RS_MIN_SIZE) {
		rs_insertsort_128x(beg, end);
		return;
	}
	if (n < WGSE_RS_MIN || n > WGSE_RS_MAX || (w = wgse_rs_scratch(n)) == 0) {
		rs_sort_128x(beg, end, RS_MAX_BITS, 7 * RS_MAX_BITS);
		return;
	}
	for (i = 0; i < n; ++i) any |= sort_key_128x(beg[i]), all &= sort_key_128x(beg[i]);
	w->A = beg;
	wgse_rs_add(w, 0, &ng, 0, n, 1, 7 * RS_MAX_BITS, any ^ all);
	if (ng) wgse_rs_group(w, 0, ng);
}
