// --metal: before mm_align_skeleton aligns a read's regions, queue the gap fills
// mm_align1 will run with KSW_EZ_APPROX_MAX on the GPU and wait for them; mm_align_pair
// then takes a GPU result only for byte-identical inputs (wgse_ksw_take). This
// replays mm_align1's anchor clean-up and gap-filling loop on a copy of each
// region's anchors; it cannot know where a Z-drop ends a region, so later fills
// of that region go unused.
static void wgse_ksw_prefetch(void *km, const mm_mapopt_t *opt, const mm_idx_t *mi, int qlen,
	uint8_t *qseq0[2], int n_regs, const mm_reg1_t *regs, const mm128_t *a)
{
	int32_t i, bw_long, m_tmp = 0;
	int8_t mat[25];
	mm128_t *tmp = 0;
	(void)qlen;
	if (!wgse_ksw_gpu_enabled() || n_regs == 0) return;
	// mm_align_pair's ksw_extd2_sse path, without KSW_EZ_GENERIC_SC or splicing.
	if (opt->flag & (MM_F_SR | MM_F_SPLICE | MM_F_SR_RNA)) return;
	if ((opt->q == opt->q2 && opt->e == opt->e2) || (opt->transition != 0 && opt->b != opt->transition)) return;
	ksw_gen_ts_mat(5, mat, opt->a, opt->b, opt->transition, opt->sc_ambi);
	wgse_ksw_set_scoring(opt->q, opt->e, opt->q2, opt->e2, mat);
	bw_long = (int)(opt->bw_long * 1.5 + 1.);
	if (bw_long < (int)(opt->bw * 1.5 + 1.)) bw_long = (int)(opt->bw * 1.5 + 1.);
	wgse_ksw_begin_read();
	for (i = 0; i < n_regs; ++i) {
		const mm_reg1_t *r = &regs[i];
		mm_reg1_t t;
		int32_t as1, cnt1, j, rid, rev, rs, qs, re, qe;
		if (r->cnt == 0) continue;
		if (r->cnt > m_tmp) {
			m_tmp = r->cnt;
			tmp = (mm128_t*)krealloc(km, tmp, m_tmp * sizeof(mm128_t));
		}
		memcpy(tmp, &a[r->as], r->cnt * sizeof(mm128_t));
		t = *r, t.as = 0;
		rid = tmp[0].x<<1>>33, rev = tmp[0].x>>63;
		if (!(opt->flag & MM_F_NO_END_FLT))
			mm_fix_bad_ends(&t, tmp, opt->bw, opt->min_chain_score * 2, &as1, &cnt1);
		else as1 = 0, cnt1 = r->cnt;
		mm_filter_bad_seeds(km, as1, cnt1, tmp, 10, 40, opt->max_gap>>1, 10);
		mm_filter_bad_seeds_alt(km, as1, cnt1, tmp, 30, opt->max_gap>>1);
		mm_adjust_minier(mi, qseq0, &tmp[as1], &rs, &qs);
		for (j = 1; j < cnt1; ++j) {
			if ((tmp[as1+j].y & (MM_SEED_IGNORE|MM_SEED_TANDEM)) && j != cnt1 - 1) continue;
			mm_adjust_minier(mi, qseq0, &tmp[as1 + j], &re, &qe);
			if (j == cnt1 - 1 || (tmp[as1+j].y&MM_SEED_LONG_JOIN) || (qe - qs >= opt->min_ksw_len && re - rs >= opt->min_ksw_len)) {
				int32_t bw1 = bw_long;
				if (tmp[as1+j].y & MM_SEED_LONG_JOIN)
					bw1 = qe - qs > re - rs? qe - qs : re - rs;
				if (!(opt->max_sw_mat > 0 && (int64_t)(re - rs) * (qe - qs) > opt->max_sw_mat)
					&& wgse_ksw_eligible(qe - qs, re - rs, bw1)) {
					uint8_t *tseq;
					if (opt->flag & MM_F_QSTRAND) {
						tseq = wgse_ksw_add(qe - qs, &qseq0[0][qs], re - rs, bw1);
						mm_idx_getseq2(mi, rev, rid, rs, re, tseq);
					} else {
						tseq = wgse_ksw_add(qe - qs, &qseq0[rev][qs], re - rs, bw1);
						mm_idx_getseq(mi, rid, rs, re, tseq);
					}
				}
				rs = re, qs = qe;
			}
		}
	}
	kfree(km, tmp);
	wgse_ksw_run_read();
}
