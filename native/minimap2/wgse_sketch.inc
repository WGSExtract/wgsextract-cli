/* wgse-minimap2: mm_sketch() over part of a sequence, so that index construction can
 * sketch one long sequence on several threads with the same result.
 * Appended to a generated copy of minimap2's sketch.c, so it can use its static helpers.
 *
 * The body is mm_sketch()'s, except that the loop starts at warm, records minimizers
 * only from iterations that start at or after beg, stops before end, and makes the final
 * write only when end is the end of the sequence. Starting from a fresh state at warm
 * gives the same output as upstream from beg on when [warm, beg) has no ambiguous base,
 * warm and beg start homopolymer runs (for HPC), and at least w + k k-mers were counted
 * in [warm, beg): the k-mer, homopolymer queue and span then depend only on the last k
 * iterations, the window buffer on the last w writes (each counted k-mer writes one),
 * the minimum is always the most recent smallest entry of the buffer, and every test of
 * l is past its threshold. The function returns that count of k-mers (l at beg), and the
 * caller falls back to upstream's mm_sketch() if it is too small. */
int wgse_mm_sketch_range(void *km, const char *str, int len, int warm, int beg, int end, int w, int k, uint32_t rid, int is_hpc, mm128_v *p)
{
	uint64_t shift1 = 2 * (k - 1), mask = (1ULL<<2*k) - 1, kmer[2] = {0,0};
	int i, j, l, buf_pos, min_pos, kmer_span = 0, l_beg = -1, keep = 0;
	mm128_t buf[256], min = { UINT64_MAX, UINT64_MAX };
	tiny_queue_t tq;

	assert(len > 0 && (w > 0 && w < 256) && (k > 0 && k <= 28));
	assert(0 <= warm && warm <= beg && beg <= end && end <= len);
	memset(buf, 0xff, w * 16);
	memset(&tq, 0, sizeof(tiny_queue_t));
	kv_resize(mm128_t, km, *p, p->n + (end - beg)/w);

#define WGSE_PUSH(v) do { if (keep) kv_push(mm128_t, km, *p, (v)); } while (0)
	for (i = warm, l = buf_pos = min_pos = 0; i < end; ++i) {
		int c = seq_nt4_table[(uint8_t)str[i]];
		mm128_t info = { UINT64_MAX, UINT64_MAX };
		if (!keep && i >= beg) keep = 1, l_beg = l;
		if (c < 4) { // not an ambiguous base
			int z;
			if (is_hpc) {
				int skip_len = 1;
				if (i + 1 < len && seq_nt4_table[(uint8_t)str[i + 1]] == c) {
					for (skip_len = 2; i + skip_len < len; ++skip_len)
						if (seq_nt4_table[(uint8_t)str[i + skip_len]] != c)
							break;
					i += skip_len - 1; // put $i at the end of the current homopolymer run
				}
				tq_push(&tq, skip_len);
				kmer_span += skip_len;
				if (tq.count > k) kmer_span -= tq_shift(&tq);
			} else kmer_span = l + 1 < k? l + 1 : k;
			kmer[0] = (kmer[0] << 2 | c) & mask;           // forward k-mer
			kmer[1] = (kmer[1] >> 2) | (3ULL^c) << shift1; // reverse k-mer
			if (kmer[0] == kmer[1]) continue; // skip "symmetric k-mers" as we don't know it strand
			z = kmer[0] < kmer[1]? 0 : 1; // strand
			++l;
			if (l >= k && kmer_span < 256) {
				info.x = hash64(kmer[z], mask) << 8 | kmer_span;
				info.y = (uint64_t)rid<<32 | (uint32_t)i<<1 | z;
			}
		} else l = 0, tq.count = tq.front = 0, kmer_span = 0;
		buf[buf_pos] = info; // need to do this here as appropriate buf_pos and buf[buf_pos] are needed below
		if (l == w + k - 1 && min.x != UINT64_MAX) { // special case for the first window - because identical k-mers are not stored yet
			for (j = buf_pos + 1; j < w; ++j)
				if (min.x == buf[j].x && buf[j].y != min.y) WGSE_PUSH(buf[j]);
			for (j = 0; j < buf_pos; ++j)
				if (min.x == buf[j].x && buf[j].y != min.y) WGSE_PUSH(buf[j]);
		}
		if (info.x <= min.x) { // a new minimum; then write the old min
			if (l >= w + k && min.x != UINT64_MAX) WGSE_PUSH(min);
			min = info, min_pos = buf_pos;
		} else if (buf_pos == min_pos) { // old min has moved outside the window
			if (l >= w + k - 1 && min.x != UINT64_MAX) WGSE_PUSH(min);
			for (j = buf_pos + 1, min.x = UINT64_MAX; j < w; ++j) // the two loops are necessary when there are identical k-mers
				if (min.x >= buf[j].x) min = buf[j], min_pos = j; // >= is important s.t. min is always the closest k-mer
			for (j = 0; j <= buf_pos; ++j)
				if (min.x >= buf[j].x) min = buf[j], min_pos = j;
			if (l >= w + k - 1 && min.x != UINT64_MAX) { // write identical k-mers
				for (j = buf_pos + 1; j < w; ++j) // these two loops make sure the output is sorted
					if (min.x == buf[j].x && min.y != buf[j].y) WGSE_PUSH(buf[j]);
				for (j = 0; j <= buf_pos; ++j)
					if (min.x == buf[j].x && min.y != buf[j].y) WGSE_PUSH(buf[j]);
			}
		}
		if (++buf_pos == w) buf_pos = 0;
	}
#undef WGSE_PUSH
	if (!keep) l_beg = l;
	if (end == len && min.x != UINT64_MAX)
		kv_push(mm128_t, km, *p, min);
	return l_beg;
}
