/* wgse-minimap2: faster, output-identical index loading and statistics.
 * Appended to a generated copy of minimap2's index.c, so it can use the file's static
 * hash table type. Each bucket's hash table is filled in upstream's order, so the
 * tables, and everything mapped with them, are identical to upstream's. */
#include <errno.h>
#include <string.h>

static int wgse_n_threads = 1; // set from the reader's -t

static void wgse_fread(void *p, size_t size, size_t n, FILE *fp)
{
	if (n > 0 && fread(p, size, n, fp) != n) {
		fprintf(stderr, "[ERROR] failed to read the index: %s\n", ferror(fp)? strerror(errno) : "unexpected end of file");
		exit(EXIT_FAILURE);
	}
}

typedef struct {
	mm_idx_t *mi;
	uint32_t first;
	uint32_t *size;     // hash table size of each bucket in this chunk
	uint64_t **entries; // the (key, value) pairs of each bucket in this chunk
} wgse_load_t;

static void wgse_load_worker(void *data, long i, int tid)
{
	wgse_load_t *d = (wgse_load_t*)data;
	mm_idx_bucket_t *b = &d->mi->B[d->first + i];
	uint32_t j, size = d->size[i];
	const uint64_t *x = d->entries[i];
	idxhash_t *h;
	(void)tid;
	if (size == 0) return;
	b->h = h = kh_init(idx);
	kh_resize(idx, h, size);
	for (j = 0; j < size; ++j) {
		int absent;
		khint_t k = kh_put(idx, h, x[j * 2], &absent);
		assert(absent);
		kh_val(h, k) = x[j * 2 + 1];
	}
}

// As mm_idx_load(), reading each bucket's hash entries in one call and filling the
// tables of up to 64 MB of entries in parallel.
static mm_idx_t *wgse_mm_idx_load(FILE *fp, int n_threads)
{
	const uint64_t chunk_bytes = 64ULL << 20;
	char magic[4];
	uint32_t x[5], i, n_buckets;
	uint64_t sum_len = 0;
	mm_idx_t *mi;
	wgse_load_t d;
	uint8_t *buffer = 0;
	uint64_t buffer_cap = 0;

	if (fread(magic, 1, 4, fp) != 4) return 0;
	if (strncmp(magic, MM_IDX_MAGIC, 4) != 0) return 0;
	if (fread(x, 4, 5, fp) != 5) return 0;
	mi = mm_idx_init(x[0], x[1], x[2], x[4]);
	mi->n_seq = x[3];
	mi->seq = (mm_idx_seq_t*)kcalloc(mi->km, mi->n_seq, sizeof(mm_idx_seq_t));
	for (i = 0; i < mi->n_seq; ++i) {
		uint8_t l;
		mm_idx_seq_t *s = &mi->seq[i];
		wgse_fread(&l, 1, 1, fp);
		if (l) {
			s->name = (char*)kmalloc(mi->km, l + 1);
			wgse_fread(s->name, 1, l, fp);
			s->name[l] = 0;
		}
		wgse_fread(&s->len, 4, 1, fp);
		s->offset = sum_len;
		s->is_alt = 0;
		sum_len += s->len;
	}
	n_buckets = 1U << mi->b;
	d.mi = mi;
	d.size = (uint32_t*)calloc(n_buckets, sizeof(uint32_t));
	d.entries = (uint64_t**)calloc(n_buckets, sizeof(uint64_t*));
	for (i = 0; i < n_buckets;) {
		uint64_t used = 0;
		uint32_t first = i, j;
		for (; i < n_buckets && (i == first || used < chunk_bytes); ++i) {
			mm_idx_bucket_t *b = &mi->B[i];
			uint32_t size;
			wgse_fread(&b->n, 4, 1, fp);
			b->p = (uint64_t*)malloc(b->n * 8);
			wgse_fread(b->p, 8, b->n, fp);
			wgse_fread(&size, 4, 1, fp);
			d.size[i - first] = size;
			if (used + (uint64_t)size * 16 > buffer_cap) {
				// Grow the chunk buffer; entries are stored as offsets until it stops moving.
				buffer_cap = (used + (uint64_t)size * 16) * 2;
				buffer = (uint8_t*)realloc(buffer, buffer_cap);
			}
			wgse_fread(buffer + used, 16, size, fp);
			d.entries[i - first] = (uint64_t*)(uintptr_t)used;
			used += (uint64_t)size * 16;
		}
		for (j = 0; j < i - first; ++j)
			d.entries[j] = (uint64_t*)(buffer + (uintptr_t)d.entries[j]);
		d.first = first;
		kt_for(n_threads > 0? n_threads : 1, wgse_load_worker, &d, i - first);
	}
	free(buffer); free(d.size); free(d.entries);
	if (!(mi->flag & MM_I_NO_SEQ)) {
		mi->S = (uint32_t*)malloc((sum_len + 7) / 8 * 4);
		wgse_fread(mi->S, 4, (sum_len + 7) / 8, fp);
	}
	return mi;
}

/* Index construction. Step 0 packs each sequence's bases with whole 32-bit words written
 * in parallel; step 1 sketches long sequences in segments on several threads (see
 * wgse_sketch.inc) and joins the segments' minimizers in order, so the index is the same. */
// The tests build a copy with small blocks and segments (tests/segments.sh).
#ifndef WGSE_PACK_BLOCK
#define WGSE_PACK_BLOCK (1 << 16)    // words per packing job
#endif
#ifndef WGSE_SKETCH_SEGMENT
#define WGSE_SKETCH_SEGMENT 4000000  // target bases per sketching job
#endif
#define WGSE_SKETCH_WARM 1024        // unambiguous bases sketched before each segment

int wgse_mm_sketch_range(void *km, const char *str, int len, int warm, int beg, int end, int w, int k, uint32_t rid, int is_hpc, mm128_v *p);

typedef struct {
	uint32_t *S;
	uint64_t word0, n_words;
	const char *seq; // the base packed at the start of word0
} wgse_pack_t;

static void wgse_pack_worker(void *data, long i, int tid)
{
	wgse_pack_t *d = (wgse_pack_t*)data;
	uint64_t w = (uint64_t)i * WGSE_PACK_BLOCK, end = w + WGSE_PACK_BLOCK;
	const uint8_t *s = (const uint8_t*)d->seq + w * 8;
	(void)tid;
	if (end > d->n_words) end = d->n_words;
	for (; w < end; ++w, s += 8) {
		uint32_t x = 0;
		int j;
		for (j = 0; j < 8; ++j) x |= (uint32_t)seq_nt4_table[s[j]] << (j << 2);
		d->S[d->word0 + w] |= x;
	}
}

// As upstream's loop of mm_seq4_set(S, offset + j, seq_nt4_table[seq[j]]).
static void wgse_pack(uint32_t *S, uint64_t offset, const char *seq, uint32_t len)
{
	uint64_t j = 0, n_words;
	wgse_pack_t d;
	for (; j < len && (offset + j) & 7; ++j)
		mm_seq4_set(S, offset + j, seq_nt4_table[(uint8_t)seq[j]]);
	n_words = (len - j) / 8;
	if (n_words > 0) {
		d.S = S, d.word0 = (offset + j) >> 3, d.n_words = n_words, d.seq = seq + j;
		kt_for(wgse_n_threads > 0? wgse_n_threads : 1, wgse_pack_worker, &d, (n_words + WGSE_PACK_BLOCK - 1) / WGSE_PACK_BLOCK);
		j += n_words * 8;
	}
	for (; j < len; ++j)
		mm_seq4_set(S, offset + j, seq_nt4_table[(uint8_t)seq[j]]);
}

typedef struct {
	int seq, warm, beg, end;
	mm128_v a;
	int l_beg;
} wgse_segment_t;

typedef struct {
	const mm_idx_t *mi;
	const mm_bseq1_t *seq;
	wgse_segment_t *seg;
} wgse_sketch_t;

static void wgse_sketch_worker(void *data, long i, int tid)
{
	wgse_sketch_t *d = (wgse_sketch_t*)data;
	wgse_segment_t *g = &d->seg[i];
	const mm_bseq1_t *t = &d->seq[g->seq];
	(void)tid;
	g->l_beg = wgse_mm_sketch_range(0, t->seq, t->l_seq, g->warm, g->beg, g->end, d->mi->w, d->mi->k, t->rid, d->mi->flag&MM_I_HPC, &g->a);
}

static inline int wgse_run_start(const char *s, int i) // homopolymer runs start at i
{
	return seq_nt4_table[(uint8_t)s[i - 1]] != seq_nt4_table[(uint8_t)s[i]];
}

// The next segment start at or after t: preceded by WGSE_SKETCH_WARM unambiguous bases
// (the warm-up starting at a homopolymer run), or len if there is none.
static int wgse_next_split(const char *s, int len, int t, int is_hpc, int *warm)
{
	int p = t < WGSE_SKETCH_WARM + 1? WGSE_SKETCH_WARM + 1 : t;
	while (p < len) {
		int q;
		for (q = p - 1; q >= p - WGSE_SKETCH_WARM && seq_nt4_table[(uint8_t)s[q]] < 4; --q) {}
		if (q >= p - WGSE_SKETCH_WARM) { p = q + 1 + WGSE_SKETCH_WARM; continue; }
		if (is_hpc && !wgse_run_start(s, p)) { ++p; continue; }
		for (q = p - WGSE_SKETCH_WARM; is_hpc && q < p && !wgse_run_start(s, q); ++q) {}
		*warm = q;
		return p;
	}
	return len;
}

static void wgse_sketch_batch(const mm_idx_t *mi, int n_seq, mm_bseq1_t *seq, mm128_v *a)
{
	int i, j, n = 0, m = 0, is_hpc = !!(mi->flag&MM_I_HPC);
	int min_l = mi->w + mi->k;
	size_t total;
	wgse_segment_t *g = 0;
	wgse_sketch_t d;
	for (i = 0; i < n_seq; ++i) {
		const mm_bseq1_t *t = &seq[i];
		int beg = 0, warm = 0;
		if (t->l_seq <= 0) {
			if (mm_verbose >= 2)
				fprintf(stderr, "[WARNING] the length database sequence '%s' is 0\n", t->name);
			continue;
		}
		while (beg < t->l_seq) {
			int next_warm = 0, end = wgse_next_split(t->seq, t->l_seq, beg + WGSE_SKETCH_SEGMENT, is_hpc, &next_warm);
			if (n == m) {
				m = m? m * 2 : 64;
				g = (wgse_segment_t*)realloc(g, m * sizeof(wgse_segment_t));
			}
			memset(&g[n], 0, sizeof(wgse_segment_t));
			g[n].seq = i, g[n].warm = warm, g[n].beg = beg, g[n].end = end;
			++n;
			beg = end, warm = next_warm;
		}
	}
	d.mi = mi, d.seq = seq, d.seg = g;
	kt_for(wgse_n_threads > 0? wgse_n_threads : 1, wgse_sketch_worker, &d, n);
	for (i = 0, total = a->n; i < n; ++i) total += g[i].a.n;
	if (total > a->m) kv_resize(mm128_t, 0, *a, total); // one exact allocation while the segments are still held
	for (i = 0; i < n;) {
		int ok = 1;
		for (j = i; j < n && g[j].seq == g[i].seq; ++j)
			if (g[j].beg > 0 && g[j].l_beg < min_l) ok = 0;
		if (!ok) { // a warm-up was too short: sketch the sequence as upstream does
			const mm_bseq1_t *t = &seq[g[i].seq];
			mm_sketch(0, t->seq, t->l_seq, mi->w, mi->k, t->rid, is_hpc, a);
		}
		for (; i < j; ++i) {
			if (ok) {
				if (a->n + g[i].a.n > a->m) kv_resize(mm128_t, 0, *a, a->n + g[i].a.n);
				memcpy(a->a + a->n, g[i].a.a, g[i].a.n * sizeof(mm128_t));
				a->n += g[i].a.n;
			}
			kfree(0, g[i].a.a);
		}
	}
	free(g);
}

// As upstream's worker_pipeline(), packing and sketching in parallel.
static void *wgse_worker_pipeline(void *shared, int step, void *in)
{
	int i;
	pipeline_t *p = (pipeline_t*)shared;
	if (step == 0) { // step 0: read sequences
		step_t *s;
		if (p->sum_len > p->batch_size) return 0;
		s = (step_t*)calloc(1, sizeof(step_t));
		s->seq = mm_bseq_read(p->fp, p->mini_batch_size, 0, &s->n_seq); // read a mini-batch
		if (s->seq) {
			uint32_t old_m, m;
			assert((uint64_t)p->mi->n_seq + s->n_seq <= UINT32_MAX); // to prevent integer overflow
			old_m = p->mi->n_seq, m = p->mi->n_seq + s->n_seq;
			kroundup32(m); kroundup32(old_m);
			if (old_m != m)
				p->mi->seq = (mm_idx_seq_t*)krealloc(p->mi->km, p->mi->seq, m * sizeof(mm_idx_seq_t));
			if (!(p->mi->flag & MM_I_NO_SEQ)) {
				uint64_t sum_len, old_max_len, max_len;
				for (i = 0, sum_len = 0; i < s->n_seq; ++i) sum_len += s->seq[i].l_seq;
				old_max_len = (p->sum_len + 7) / 8;
				max_len = (p->sum_len + sum_len + 7) / 8;
				kroundup64(old_max_len); kroundup64(max_len);
				if (old_max_len != max_len) {
					p->mi->S = (uint32_t*)realloc(p->mi->S, max_len * 4);
					memset(&p->mi->S[old_max_len], 0, 4 * (max_len - old_max_len));
				}
			}
			for (i = 0; i < s->n_seq; ++i) {
				mm_idx_seq_t *seq = &p->mi->seq[p->mi->n_seq];
				if (!(p->mi->flag & MM_I_NO_NAME)) {
					seq->name = (char*)kmalloc(p->mi->km, strlen(s->seq[i].name) + 1);
					strcpy(seq->name, s->seq[i].name);
				} else seq->name = 0;
				seq->len = s->seq[i].l_seq;
				seq->offset = p->sum_len;
				seq->is_alt = 0;
				if (!(p->mi->flag & MM_I_NO_SEQ))
					wgse_pack(p->mi->S, p->sum_len, s->seq[i].seq, seq->len);
				p->sum_len += seq->len;
				s->seq[i].rid = p->mi->n_seq++;
			}
			return s;
		} else free(s);
	} else if (step == 1) { // step 1: compute sketch
		step_t *s = (step_t*)in;
		wgse_sketch_batch(p->mi, s->n_seq, s->seq, &s->a);
		for (i = 0; i < s->n_seq; ++i) {
			free(s->seq[i].seq); free(s->seq[i].name);
		}
		free(s->seq); s->seq = 0;
		return s;
	} else if (step == 2) { // dispatch sketch to buckets
		step_t *s = (step_t*)in;
		mm_idx_add(p->mi, s->a.n, s->a.a);
		kfree(0, s->a.a); free(s);
	}
	return 0;
}

/* Occurrence counts: values below WGSE_OCC_DIRECT are counted in per-thread histograms;
 * larger ones are rare and collected in a list. */
#define WGSE_OCC_DIRECT 65536

typedef struct {
	const mm_idx_t *mi;
	int n_threads;
	uint64_t *hist;   // n_threads * WGSE_OCC_DIRECT counts
	int64_t *n, *n1;  // per bucket: distinct minimizers, singletons
	uint64_t *sum;    // per bucket: total occurrences
	uint32_t **large; // per bucket: counts >= WGSE_OCC_DIRECT
	uint32_t *n_large;
} wgse_occ_t;

static void wgse_occ_worker(void *data, long i, int tid)
{
	wgse_occ_t *d = (wgse_occ_t*)data;
	idxhash_t *h = (idxhash_t*)d->mi->B[i].h;
	uint64_t *hist = d->hist? d->hist + (size_t)tid * WGSE_OCC_DIRECT : 0;
	int64_t n1 = 0;
	uint64_t sum = 0;
	khint_t k;
	if (h == 0) return;
	for (k = 0; k < kh_end(h); ++k) {
		uint32_t c;
		if (!kh_exist(h, k)) continue;
		c = kh_key(h, k)&1? 1 : (uint32_t)kh_val(h, k);
		sum += c;
		if (kh_key(h, k)&1) ++n1;
		if (hist == 0) continue;
		if (c < WGSE_OCC_DIRECT) ++hist[c];
		else {
			if ((d->n_large[i] & (d->n_large[i] - 1)) == 0)
				d->large[i] = (uint32_t*)realloc(d->large[i], (d->n_large[i]? d->n_large[i] * 2 : 1) * sizeof(uint32_t));
			d->large[i][d->n_large[i]++] = c;
		}
	}
	d->n[i] = kh_size(h), d->n1[i] = n1, d->sum[i] = sum;
}

static void wgse_occ_scan(wgse_occ_t *d, const mm_idx_t *mi, int with_hist)
{
	uint32_t n_buckets = 1U << mi->b;
	d->mi = mi;
	d->n_threads = wgse_n_threads > 0? wgse_n_threads : 1;
	d->hist = with_hist? (uint64_t*)calloc((size_t)d->n_threads * WGSE_OCC_DIRECT, 8) : 0;
	d->n = (int64_t*)calloc(n_buckets, 8);
	d->n1 = (int64_t*)calloc(n_buckets, 8);
	d->sum = (uint64_t*)calloc(n_buckets, 8);
	d->large = (uint32_t**)calloc(n_buckets, sizeof(uint32_t*));
	d->n_large = (uint32_t*)calloc(n_buckets, sizeof(uint32_t));
	kt_for(d->n_threads, wgse_occ_worker, d, n_buckets);
}

static void wgse_occ_free(wgse_occ_t *d, const mm_idx_t *mi)
{
	uint32_t i;
	for (i = 0; i < 1U << mi->b; ++i) free(d->large[i]);
	free(d->hist); free(d->n); free(d->n1); free(d->sum); free(d->large); free(d->n_large);
}

// As upstream's mm_idx_cal_max_occ(): the ((1 - f) * n)-th smallest occurrence count,
// plus one, found from a histogram instead of a selection over every count.
static int32_t wgse_mm_idx_cal_max_occ(const mm_idx_t *mi, float f)
{
	wgse_occ_t d;
	uint32_t i, n_buckets = 1U << mi->b, thres = 0;
	size_t n = 0, rank, below = 0, n_large = 0;
	int t, found = 0;
	uint32_t v;
	if (f <= 0.) return INT32_MAX;
	wgse_occ_scan(&d, mi, 1);
	for (i = 0; i < n_buckets; ++i) n += d.n[i], n_large += d.n_large[i];
	if (n == 0) {
		wgse_occ_free(&d, mi);
		return INT32_MAX;
	}
	rank = (uint32_t)((1. - f) * n);
	for (v = 0; v < WGSE_OCC_DIRECT && !found; ++v) {
		uint64_t c = 0;
		for (t = 0; t < d.n_threads; ++t) c += d.hist[(size_t)t * WGSE_OCC_DIRECT + v];
		if (below + c > rank) thres = v, found = 1;
		below += c;
	}
	if (!found) {
		uint32_t *large = (uint32_t*)malloc((n_large? n_large : 1) * sizeof(uint32_t));
		size_t m = 0;
		for (i = 0; i < n_buckets; ++i) {
			memcpy(large + m, d.large[i], d.n_large[i] * sizeof(uint32_t));
			m += d.n_large[i];
		}
		thres = ks_ksmall_uint32_t(m, large, rank - below);
		free(large);
	}
	wgse_occ_free(&d, mi);
	return thres + 1;
}

// As upstream's mm_idx_stat(), summing the buckets in parallel.
static void wgse_mm_idx_stat(const mm_idx_t *mi)
{
	wgse_occ_t d;
	int64_t n = 0, n1 = 0;
	uint32_t i;
	uint64_t sum = 0, len = 0;
	fprintf(stderr, "[M::%s] kmer size: %d; skip: %d; is_hpc: %d; #seq: %d\n", "mm_idx_stat", mi->k, mi->w, mi->flag&MM_I_HPC, mi->n_seq);
	for (i = 0; i < mi->n_seq; ++i)
		len += mi->seq[i].len;
	wgse_occ_scan(&d, mi, 0);
	for (i = 0; i < 1U << mi->b; ++i) n += d.n[i], n1 += d.n1[i], sum += d.sum[i];
	wgse_occ_free(&d, mi);
	fprintf(stderr, "[M::%s::%.3f*%.2f] distinct minimizers: %ld (%.2f%% are singletons); average occurrences: %.3lf; average spacing: %.3lf; total length: %ld\n",
			"mm_idx_stat", realtime() - mm_realtime0, cputime() / (realtime() - mm_realtime0), (long)n, 100.0*n1/n, (double)sum / n, (double)len / sum, (long)len);
}

/* Prefetches for mm_seed_collect_all(): the bucket's hash table header, then the slot
 * mm_idx_get() probes first. */
void wgse_mm_idx_prefetch_table(const mm_idx_t *mi, uint64_t minier)
{
	const mm_idx_bucket_t *b = &mi->B[minier & ((1U << mi->b) - 1)];
	if (b->h) __builtin_prefetch(b->h);
}

void wgse_mm_idx_prefetch_slot(const mm_idx_t *mi, uint64_t minier)
{
	const idxhash_t *h = (const idxhash_t*)mi->B[minier & ((1U << mi->b) - 1)].h;
	khint_t i;
	if (h == 0 || h->n_buckets == 0) return;
	i = (khint_t)idx_hash(minier >> mi->b << 1) & (h->n_buckets - 1);
	__builtin_prefetch(&h->flags[i >> 4]);
	__builtin_prefetch(&h->keys[i]);
	__builtin_prefetch(&h->vals[i]);
}
