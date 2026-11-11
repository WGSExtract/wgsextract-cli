/* wgse-minimap2: minimap2's ksw_extd2_sse() (ksw2_extd2_sse.c, MIT), written for NEON.
 *
 * Upstream's arm64 build runs the SSE code through sse2neon. This is the same
 * algorithm, int8 arithmetic and control flow, so it computes the same values and
 * returns the same result; only the vector code differs:
 *  - the previous diagonal's values shifted by one column, x[t-1..t+14], are one EXT
 *    of the vector before and the current one, instead of two shifts and an OR;
 *  - x = max(a, 0) - qe, and the backtrack flag bits are inserted with one BIT;
 *  - blends are single BSLs.
 * The scalar parts are upstream's. */
#include <string.h>
#include <stdio.h>
#include <assert.h>
#include <arm_neon.h>
#include "ksw2.h"

/* d with the bits of src where mask is set (one BIT; the compiler would split it into AND and ORR) */
static inline uint8x16_t wgse_bit(uint8x16_t d, uint8x16_t src, uint8x16_t mask)
{
	__asm__("bit %0.16b, %1.16b, %2.16b" : "+w"(d) : "w"(src), "w"(mask));
	return d;
}

void ksw_extd2_sse(void *km, int qlen, const uint8_t *query, int tlen, const uint8_t *target, int8_t m, const int8_t *mat,
				   int8_t q, int8_t e, int8_t q2, int8_t e2, int w, int zdrop, int end_bonus, int flag, ksw_extz_t *ez)
{
	/* x1_, v1_, x21_ hold the previous vectors of x, v and x2 (lane 15 = column t-1) */
#define __dp_code_block1 \
	z = s[t]; \
	xt = x[t]; xt1 = vextq_s8(x1_, xt, 15); x1_ = xt;     /* xt1 <- x[r-1][t-1..t+14] */ \
	vt = v[t]; vt1 = vextq_s8(v1_, vt, 15); v1_ = vt;     /* vt1 <- v[r-1][t-1..t+14] */ \
	a = vaddq_s8(xt1, vt1);                               /* a <- x[r-1][t-1..t+14] + v[r-1][t-1..t+14] */ \
	ut = u[t];                                            /* ut <- u[t..t+15] */ \
	b = vaddq_s8(y[t], ut);                               /* b <- y[r-1][t..t+15] + u[r-1][t..t+15] */ \
	x2t = x2[t]; x2t1 = vextq_s8(x21_, x2t, 15); x21_ = x2t; \
	a2 = vaddq_s8(x2t1, vt1); \
	b2 = vaddq_s8(y2[t], ut);

#define __dp_code_block2 \
	u[t] = vsubq_s8(z, vt1);                              /* u[r][t..t+15] <- z - v[r-1][t-1..t+14] */ \
	v[t] = vsubq_s8(z, ut);                               /* v[r][t..t+15] <- z - u[r-1][t..t+15] */ \
	tmp = vsubq_s8(z, q_); \
	a = vsubq_s8(a, tmp); \
	b = vsubq_s8(b, tmp); \
	tmp = vsubq_s8(z, q2_); \
	a2 = vsubq_s8(a2, tmp); \
	b2 = vsubq_s8(b2, tmp);

	int r, t, qe = q + e, n_col_, *off = 0, *off_end = 0, tlen_, qlen_, last_st, last_en, wl, wr, max_sc, min_sc, long_thres, long_diff;
	int with_cigar = !(flag&KSW_EZ_SCORE_ONLY), approx_max = !!(flag&KSW_EZ_APPROX_MAX);
	int32_t *H = 0, H0 = 0, last_H0_t = 0;
	uint8_t *qr, *sf, *mem, *mem2 = 0;
	int8x16_t q_, q2_, qe_, qe2_, zero_, sc_mch_, sc_mis_, sc_N_;
	uint8x16_t m1_, k1 = vdupq_n_u8(1), k2 = vdupq_n_u8(2), k3 = vdupq_n_u8(3), k4 = vdupq_n_u8(4);
	uint8x16_t k8 = vdupq_n_u8(0x08), k16 = vdupq_n_u8(0x10), k32 = vdupq_n_u8(0x20), k64 = vdupq_n_u8(0x40);
	int8x16_t *u, *v, *x, *y, *x2, *y2, *s;
	uint8x16_t *p = 0;

	ksw_reset_extz(ez);
	if (m <= 1 || qlen <= 0 || tlen <= 0) return;

	if (q2 + e2 < q + e) t = q, q = q2, q2 = t, t = e, e = e2, e2 = t; // make sure q+e no larger than q2+e2

	zero_   = vdupq_n_s8(0);
	q_      = vdupq_n_s8(q);
	q2_     = vdupq_n_s8(q2);
	qe_     = vdupq_n_s8(q + e);
	qe2_    = vdupq_n_s8(q2 + e2);
	sc_mch_ = vdupq_n_s8(mat[0]);
	sc_mis_ = vdupq_n_s8(mat[1]);
	sc_N_   = mat[m*m-1] == 0? vdupq_n_s8(-e2) : vdupq_n_s8(mat[m*m-1]);
	m1_     = vdupq_n_u8(m - 1); // wildcard

	if (w < 0) w = tlen > qlen? tlen : qlen;
	wl = wr = w;
	tlen_ = (tlen + 15) / 16;
	n_col_ = qlen < tlen? qlen : tlen;
	n_col_ = ((n_col_ < w + 1? n_col_ : w + 1) + 15) / 16 + 1;
	qlen_ = (qlen + 15) / 16;
	for (t = 1, max_sc = mat[0], min_sc = mat[1]; t < m * m; ++t) {
		max_sc = max_sc > mat[t]? max_sc : mat[t];
		min_sc = min_sc < mat[t]? min_sc : mat[t];
	}
	if (-min_sc > 2 * (q + e)) return; // otherwise, we won't see any mismatches

	long_thres = e != e2? (q2 - q) / (e - e2) - 1 : 0;
	if (q2 + e2 + long_thres * e2 > q + e + long_thres * e)
		++long_thres;
	long_diff = long_thres * (e - e2) - (q2 - q) - e2;

	mem = (uint8_t*)kcalloc(km, tlen_ * 8 + qlen_ + 1, 16);
	u = (int8x16_t*)(((size_t)mem + 15) >> 4 << 4); // 16-byte aligned
	v = u + tlen_, x = v + tlen_, y = x + tlen_, x2 = y + tlen_, y2 = x2 + tlen_;
	s = y2 + tlen_, sf = (uint8_t*)(s + tlen_), qr = sf + tlen_ * 16;
	memset(u,  -q  - e,  tlen_ * 16);
	memset(v,  -q  - e,  tlen_ * 16);
	memset(x,  -q  - e,  tlen_ * 16);
	memset(y,  -q  - e,  tlen_ * 16);
	memset(x2, -q2 - e2, tlen_ * 16);
	memset(y2, -q2 - e2, tlen_ * 16);
	if (!approx_max) {
		H = (int32_t*)kmalloc(km, tlen_ * 16 * 4);
		for (t = 0; t < tlen_ * 16; ++t) H[t] = KSW_NEG_INF;
	}
	if (with_cigar) {
		mem2 = (uint8_t*)kmalloc(km, ((size_t)(qlen + tlen - 1) * n_col_ + 1) * 16);
		p = (uint8x16_t*)(((size_t)mem2 + 15) >> 4 << 4);
		off = (int*)kmalloc(km, (qlen + tlen - 1) * sizeof(int) * 2);
		off_end = off + qlen + tlen - 1;
	}

	for (t = 0; t < qlen; ++t) qr[t] = query[qlen - 1 - t];
	memcpy(sf, target, tlen);

	for (r = 0, last_st = last_en = -1; r < qlen + tlen - 1; ++r) {
		int st = 0, en = tlen - 1, st0, en0, st_, en_;
		int8_t x1, x21, v1;
		uint8_t *qrr = qr + (qlen - 1 - r);
		int8_t *u8 = (int8_t*)u, *v8 = (int8_t*)v, *x8 = (int8_t*)x, *x28 = (int8_t*)x2;
		int8x16_t x1_, x21_, v1_;
		// find the boundaries
		if (st < r - qlen + 1) st = r - qlen + 1;
		if (en > r) en = r;
		if (st < (r-wr+1)>>1) st = (r-wr+1)>>1; // take the ceil
		if (en > (r+wl)>>1) en = (r+wl)>>1; // take the floor
		if (st > en) {
			ez->zdropped = 1;
			break;
		}
		st0 = st, en0 = en;
		st = st / 16 * 16, en = (en + 16) / 16 * 16 - 1;
		// set boundary conditions
		if (st > 0) {
			if (st - 1 >= last_st && st - 1 <= last_en) {
				x1 = x8[st - 1], x21 = x28[st - 1], v1 = v8[st - 1]; // (r-1,s-1) calculated in the last round
			} else {
				x1 = -q - e, x21 = -q2 - e2;
				v1 = -q - e;
			}
		} else {
			x1 = -q - e, x21 = -q2 - e2;
			v1 = r == 0? -q - e : r < long_thres? -e : r == long_thres? long_diff : -e2;
		}
		if (en >= r) {
			((int8_t*)y)[r] = -q - e, ((int8_t*)y2)[r] = -q2 - e2;
			u8[r] = r == 0? -q - e : r < long_thres? -e : r == long_thres? long_diff : -e2;
		}
		// loop fission: set scores first
		if (!(flag & KSW_EZ_GENERIC_SC)) {
			for (t = st0; t <= en0; t += 16) {
				uint8x16_t sq, st, mask;
				int8x16_t tmp;
				sq = vld1q_u8(&sf[t]);
				st = vld1q_u8(&qrr[t]);
				mask = vorrq_u8(vceqq_u8(sq, m1_), vceqq_u8(st, m1_));
				tmp = vbslq_s8(vceqq_u8(sq, st), sc_mch_, sc_mis_);
				tmp = vbslq_s8(mask, sc_N_, tmp);
				vst1q_s8((int8_t*)s + t, tmp);
			}
		} else {
			for (t = st0; t <= en0; ++t)
				((uint8_t*)s)[t] = mat[sf[t] * m + qrr[t]];
		}
		// core loop
		x1_  = vsetq_lane_s8(x1, zero_, 15);
		x21_ = vsetq_lane_s8(x21, zero_, 15);
		v1_  = vsetq_lane_s8(v1, zero_, 15);
		st_ = st / 16, en_ = en / 16;
		assert(en_ - st_ + 1 <= n_col_);
		if (!with_cigar) { // score only
			for (t = st_; t <= en_; ++t) {
				int8x16_t z, a, b, a2, b2, xt, xt1, x2t, x2t1, vt, vt1, ut, tmp;
				__dp_code_block1;
				z = vmaxq_s8(z, a);
				z = vmaxq_s8(z, b);
				z = vmaxq_s8(z, a2);
				z = vmaxq_s8(z, b2);
				z = vminq_s8(z, sc_mch_);
				__dp_code_block2; // save u[] and v[]; update a, b, a2 and b2
				x[t]  = vsubq_s8(vmaxq_s8(a,  zero_), qe_);
				y[t]  = vsubq_s8(vmaxq_s8(b,  zero_), qe_);
				x2[t] = vsubq_s8(vmaxq_s8(a2, zero_), qe2_);
				y2[t] = vsubq_s8(vmaxq_s8(b2, zero_), qe2_);
			}
		} else if (!(flag&KSW_EZ_RIGHT)) { // gap left-alignment
			uint8x16_t *pr = p + (size_t)r * n_col_ - st_;
			off[r] = st, off_end[r] = en;
			for (t = st_; t <= en_; ++t) {
				int8x16_t z, a, b, a2, b2, xt, xt1, x2t, x2t1, vt, vt1, ut, tmp;
				uint8x16_t d;
				__dp_code_block1;
				d = vandq_u8(vcgtq_s8(a, z), k1);         // d = a  > z? 1 : 0
				z = vmaxq_s8(z, a);
				d = wgse_bit(d, k2, vcgtq_s8(b,  z));                // d = b  > z? 2 : d
				z = vmaxq_s8(z, b);
				d = wgse_bit(d, k3, vcgtq_s8(a2, z));                // d = a2 > z? 3 : d
				z = vmaxq_s8(z, a2);
				d = wgse_bit(d, k4, vcgtq_s8(b2, z));                // d = b2 > z? 4 : d
				z = vmaxq_s8(z, b2);
				z = vminq_s8(z, sc_mch_);
				__dp_code_block2;
				x[t]  = vsubq_s8(vmaxq_s8(a,  zero_), qe_);
				d = wgse_bit(d, vcgtzq_s8(a),  k8);    // d = a  > 0? 1<<3 : 0
				y[t]  = vsubq_s8(vmaxq_s8(b,  zero_), qe_);
				d = wgse_bit(d, vcgtzq_s8(b),  k16);    // d = b  > 0? 1<<4 : 0
				x2[t] = vsubq_s8(vmaxq_s8(a2, zero_), qe2_);
				d = wgse_bit(d, vcgtzq_s8(a2), k32);    // d = a2 > 0? 1<<5 : 0
				y2[t] = vsubq_s8(vmaxq_s8(b2, zero_), qe2_);
				d = wgse_bit(d, vcgtzq_s8(b2), k64);    // d = b2 > 0? 1<<6 : 0
				pr[t] = d;
			}
		} else { // gap right-alignment
			uint8x16_t *pr = p + (size_t)r * n_col_ - st_;
			off[r] = st, off_end[r] = en;
			for (t = st_; t <= en_; ++t) {
				int8x16_t z, a, b, a2, b2, xt, xt1, x2t, x2t1, vt, vt1, ut, tmp;
				uint8x16_t d;
				__dp_code_block1;
				d = vandq_u8(vcgeq_s8(a, z), k1);         // d = z > a?  0 : 1
				z = vmaxq_s8(z, a);
				d = wgse_bit(d, k2, vcgeq_s8(b,  z));                // d = z > b?  d : 2
				z = vmaxq_s8(z, b);
				d = wgse_bit(d, k3, vcgeq_s8(a2, z));                // d = z > a2? d : 3
				z = vmaxq_s8(z, a2);
				d = wgse_bit(d, k4, vcgeq_s8(b2, z));                // d = z > b2? d : 4
				z = vmaxq_s8(z, b2);
				z = vminq_s8(z, sc_mch_);
				__dp_code_block2;
				x[t]  = vsubq_s8(vmaxq_s8(a,  zero_), qe_);
				d = wgse_bit(d, vcgezq_s8(a),  k8);    // d = a  >= 0? 1<<3 : 0
				y[t]  = vsubq_s8(vmaxq_s8(b,  zero_), qe_);
				d = wgse_bit(d, vcgezq_s8(b),  k16);    // d = b  >= 0? 1<<4 : 0
				x2[t] = vsubq_s8(vmaxq_s8(a2, zero_), qe2_);
				d = wgse_bit(d, vcgezq_s8(a2), k32);    // d = a2 >= 0? 1<<5 : 0
				y2[t] = vsubq_s8(vmaxq_s8(b2, zero_), qe2_);
				d = wgse_bit(d, vcgezq_s8(b2), k64);    // d = b2 >= 0? 1<<6 : 0
				pr[t] = d;
			}
		}
		if (!approx_max) { // find the exact max with a 32-bit score array
			int32_t max_H, max_t;
			// compute H[], max_H and max_t
			if (r > 0) {
				int32_t HH[4], tt[4], en1 = st0 + (en0 - st0) / 4 * 4, i;
				int32x4_t max_H_, max_t_;
				max_H = H[en0] = en0 > 0? H[en0-1] + u8[en0] : H[en0] + v8[en0]; // special casing the last element
				max_t = en0;
				max_H_ = vdupq_n_s32(max_H);
				max_t_ = vdupq_n_s32(max_t);
				for (t = st0; t < en1; t += 4) { // this implements: H[t]+=v8[t]-qe; if(H[t]>max_H) max_H=H[t],max_t=t;
					int32x4_t H1, t_;
					uint32x4_t gt;
					int32_t v4;
					memcpy(&v4, &v8[t], 4);
					t_ = vmovl_s16(vget_low_s16(vmovl_s8(vreinterpret_s8_s32(vdup_n_s32(v4)))));
					H1 = vaddq_s32(vld1q_s32(&H[t]), t_);
					vst1q_s32(&H[t], H1);
					gt = vcgtq_s32(H1, max_H_);
					max_H_ = vbslq_s32(gt, H1, max_H_);
					max_t_ = vbslq_s32(gt, vdupq_n_s32(t), max_t_);
				}
				vst1q_s32(HH, max_H_);
				vst1q_s32(tt, max_t_);
				for (i = 0; i < 4; ++i)
					if (max_H < HH[i]) max_H = HH[i], max_t = tt[i] + i;
				for (; t < en0; ++t) { // for the rest of values that haven't been computed with SSE
					H[t] += (int32_t)v8[t];
					if (H[t] > max_H)
						max_H = H[t], max_t = t;
				}
			} else H[0] = v8[0] - qe, max_H = H[0], max_t = 0; // special casing r==0
			// update ez
			if (en0 == tlen - 1 && H[en0] > ez->mte)
				ez->mte = H[en0], ez->mte_q = r - en0;
			if (r - st0 == qlen - 1 && H[st0] > ez->mqe)
				ez->mqe = H[st0], ez->mqe_t = st0;
			if (ksw_apply_zdrop(ez, 1, max_H, r, max_t, zdrop, e2)) break;
			if (r == qlen + tlen - 2 && en0 == tlen - 1)
				ez->score = H[tlen - 1];
		} else { // find approximate max; Z-drop might be inaccurate, too.
			if (r > 0) {
				if (last_H0_t >= st0 && last_H0_t <= en0 && last_H0_t + 1 >= st0 && last_H0_t + 1 <= en0) {
					int32_t d0 = v8[last_H0_t];
					int32_t d1 = u8[last_H0_t + 1];
					if (d0 > d1) H0 += d0;
					else H0 += d1, ++last_H0_t;
				} else if (last_H0_t >= st0 && last_H0_t <= en0) {
					H0 += v8[last_H0_t];
				} else {
					++last_H0_t, H0 += u8[last_H0_t];
				}
			} else H0 = v8[0] - qe, last_H0_t = 0;
			if ((flag & KSW_EZ_APPROX_DROP) && ksw_apply_zdrop(ez, 1, H0, r, last_H0_t, zdrop, e2)) break;
			if (r == qlen + tlen - 2 && en0 == tlen - 1)
				ez->score = H0;
		}
		last_st = st, last_en = en;
	}
	kfree(km, mem);
	if (!approx_max) kfree(km, H);
	if (with_cigar) { // backtrack
		int rev_cigar = !!(flag & KSW_EZ_REV_CIGAR);
		if (!ez->zdropped && !(flag&KSW_EZ_EXTZ_ONLY)) {
			ksw_backtrack(km, 1, rev_cigar, 0, (uint8_t*)p, off, off_end, n_col_*16, tlen-1, qlen-1, &ez->m_cigar, &ez->n_cigar, &ez->cigar);
		} else if (!ez->zdropped && (flag&KSW_EZ_EXTZ_ONLY) && ez->mqe + end_bonus > (int)ez->max) {
			ez->reach_end = 1;
			ksw_backtrack(km, 1, rev_cigar, 0, (uint8_t*)p, off, off_end, n_col_*16, ez->mqe_t, qlen-1, &ez->m_cigar, &ez->n_cigar, &ez->cigar);
		} else if (ez->max_t >= 0 && ez->max_q >= 0) {
			ksw_backtrack(km, 1, rev_cigar, 0, (uint8_t*)p, off, off_end, n_col_*16, ez->max_t, ez->max_q, &ez->m_cigar, &ez->n_cigar, &ez->cigar);
		}
		kfree(km, mem2); kfree(km, off);
	}
}
