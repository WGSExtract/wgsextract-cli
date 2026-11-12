# Generate copies of pinned upstream sources with narrow seams. The GPU seams are
# inert unless the Metal backend is enabled; the CPU ones (wgse_fast_cpu()) swap
# in exact replacements that give upstream's results, unless a test option asks
# for upstream's code.
function(seam_replace variable before after)
    string(FIND "${${variable}}" "${before}" position)
    if(position EQUAL -1)
        message(FATAL_ERROR "Unexpected upstream BWA source: ${before}")
    endif()
    string(REPLACE "${before}" "${after}" patched "${${variable}}")
    set(${variable} "${patched}" PARENT_SCOPE)
endfunction()

set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/seed_oracle.c.in" "${CMAKE_CURRENT_SOURCE_DIR}/rescue_oracle.c.in"
    "${CMAKE_CURRENT_SOURCE_DIR}/extend_oracle.c.in" "${CMAKE_CURRENT_SOURCE_DIR}/mem_stages.c.in"
    "${CMAKE_CURRENT_SOURCE_DIR}/sort_oracle.c.in" "${CMAKE_CURRENT_SOURCE_DIR}/pac_oracle.c.in"
    "${CMAKE_CURRENT_SOURCE_DIR}/ksw_oracle.c.in" "${CMAKE_CURRENT_SOURCE_DIR}/extend_neon.c.in"
    "${CMAKE_CURRENT_SOURCE_DIR}/fastmap_stages.c.in")
file(READ "${bwa_SOURCE_DIR}/bwamem.c" mem_source)
seam_replace(mem_source "mem_collect_intv(opt, bwt, len, seq, aux);"
    "if (!wgse_metal_take(opt, bwt, seq, &aux->mem)) {\n\t\tmem_collect_intv(opt, bwt, len, seq, aux);\n\t\twgse_metal_locate(opt, bwt, &aux->mem);\n\t}")
seam_replace(mem_source
    "kt_for(opt->n_threads, worker1, &w, (opt->flag&MEM_F_PE)? n>>1 : n);"
    "if (!wgse_metal_map(opt, bwt, n, seqs, worker1, &w))\n\t\tkt_for(opt->n_threads, worker1, &w, (opt->flag&MEM_F_PE)? n>>1 : n);")
seam_replace(mem_source
    "kt_for(opt->n_threads, worker2, &w, (opt->flag&MEM_F_PE)? n>>1 : n);"
    "if (!wgse_metal_pair(opt, bns, pac, pes, n, seqs, w.regs, worker2, &w))\n\t\tkt_for(opt->n_threads, worker2, &w, (opt->flag&MEM_F_PE)? n>>1 : n);")
seam_replace(mem_source "s.rbeg = tmp.pos = bwt_sa(bwt, p->x[0] + k);"
    "s.rbeg = tmp.pos = wgse_metal_sa(bwt, p->x[0] + k);")
seam_replace(mem_source "rseq = bns_fetch_seq(bns, pac, &rmax[0], c->seeds[0].rbeg, &rmax[1], &rid);"
    "rseq = wgse_fetch_window(bns, pac, &rmax[0], c->seeds[0].rbeg, &rmax[1], &rid);")
# mem_chain2aln()'s left and right extensions: the GPU's result if it extended
# exactly these inputs, otherwise upstream's.
set(left_call "a->score = ksw_extend2(s->qbeg, qs, tmp, rs, 5, opt->mat, opt->o_del, opt->e_del, opt->o_ins, opt->e_ins, aw[0], opt->pen_clip5, opt->zdrop, s->len * opt->a, &qle, &tle, &gtle, &gscore, &max_off[0]);")
seam_replace(mem_source "${left_call}"
    "if (!wgse_metal_extend(s->qbeg, qs, tmp, rs, aw[0], opt->pen_clip5, s->len * opt->a, &qle, &tle, &gtle, &gscore, &max_off[0], &a->score))\n\t\t\t\t\t${left_call}")
set(right_call "a->score = ksw_extend2(l_query - qe, query + qe, rmax[1] - rmax[0] - re, rseq + re, 5, opt->mat, opt->o_del, opt->e_del, opt->o_ins, opt->e_ins, aw[1], opt->pen_clip3, opt->zdrop, sc0, &qle, &tle, &gtle, &gscore, &max_off[1]);")
seam_replace(mem_source "${right_call}"
    "if (!wgse_metal_extend(l_query - qe, query + qe, rmax[1] - rmax[0] - re, rseq + re, aw[1], opt->pen_clip3, sc0, &qle, &tle, &gtle, &gscore, &max_off[1], &a->score))\n\t\t\t\t\t${right_call}")
# mem_sort_dedup_patch()'s sorts move pointers instead of regions (sort_oracle.c.in).
seam_replace(mem_source "ks_introsort(mem_ars2, n, a); // sort by the END position, not START!"
    "if (wgse_fast_cpu()) wgse_sort_alnreg_by_end(n, a);\n\telse ks_introsort(mem_ars2, n, a);")
seam_replace(mem_source "\tks_introsort(mem_ars, n, a);\n\tfor (i = 1; i < n; ++i) { // mark identical hits"
    "\tif (wgse_fast_cpu()) wgse_sort_alnreg_by_score(n, a);\n\telse ks_introsort(mem_ars, n, a);\n\tfor (i = 1; i < n; ++i) { // mark identical hits")
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/seed_oracle.c.in" oracle_source)
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/extend_oracle.c.in" extend_oracle_source)
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/mem_stages.c.in" mem_stages_source)
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/sort_oracle.c.in" sort_oracle_source)
file(WRITE "${generated}/bwamem.c"
    "#include \"metal_backend.h\"\n${mem_source}\n${oracle_source}\n${extend_oracle_source}\n${mem_stages_source}\n${sort_oracle_source}")

file(READ "${bwa_SOURCE_DIR}/bwamem_pair.c" pair_source)
set(rescue_call "aln = ksw_align2(l_ms, seq, re - rb, ref, 5, opt->mat, opt->o_del, opt->e_del, opt->o_ins, opt->e_ins, xtra, 0);")
seam_replace(pair_source "${rescue_call}"
    "if (!wgse_metal_matesw(opt, ms, seq, l_ms, ref, is_rev, rb, re, xtra, &aln))\n\t\t\t\taln = wgse_rescue_align(opt, l_ms, seq, re - rb, ref, xtra);")
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/rescue_oracle.c.in" rescue_source)
file(WRITE "${generated}/bwamem_pair.c"
    "#include \"metal_backend.h\"\n${pair_source}\n${rescue_source}")

# bns_get_seq() unpacks a byte of bases at a time (pac_oracle.c.in).
file(READ "${bwa_SOURCE_DIR}/bntseq.c" bntseq_source)
seam_replace(bntseq_source "\t\t\tfor (k = end_f; k > beg_f; --k)\n\t\t\t\tseq[l++] = 3 - _get_pac(pac, k);"
    "\t\t\tif (wgse_fast_cpu()) wgse_unpack_pac_reverse(pac, beg_f, end_f, seq);\n\t\t\telse for (k = end_f; k > beg_f; --k)\n\t\t\t\tseq[l++] = 3 - _get_pac(pac, k);")
seam_replace(bntseq_source "\t\t\tfor (k = beg; k < end; ++k)\n\t\t\t\tseq[l++] = _get_pac(pac, k);"
    "\t\t\tif (wgse_fast_cpu()) wgse_unpack_pac(pac, beg, end, seq);\n\t\t\telse for (k = beg; k < end; ++k)\n\t\t\t\tseq[l++] = _get_pac(pac, k);")
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/pac_oracle.c.in" pac_oracle_source)
file(WRITE "${generated}/bntseq.c" "#include \"metal_backend.h\"\n${bntseq_source}\n${pac_oracle_source}")
# Batched ksw_align2() for CPU threads' mate rescue (ksw_oracle.c.in); upstream code is unchanged.
file(READ "${bwa_SOURCE_DIR}/ksw.c" ksw_source)
# ksw_extend2() runs a NEON version with upstream's exact results when the scores
# fit 16 bits (extend_neon.c.in); upstream's is kept as wgse_ksw_extend2_upstream().
seam_replace(ksw_source "int ksw_extend2(int qlen, const uint8_t *query, int tlen, const uint8_t *target, int m, const int8_t *mat, int o_del, int e_del, int o_ins, int e_ins, int w, int end_bonus, int zdrop, int h0, int *_qle, int *_tle, int *_gtle, int *_gscore, int *_max_off)\n{"
    "int wgse_ksw_extend2_upstream(int qlen, const uint8_t *query, int tlen, const uint8_t *target, int m, const int8_t *mat, int o_del, int e_del, int o_ins, int e_ins, int w, int end_bonus, int zdrop, int h0, int *_qle, int *_tle, int *_gtle, int *_gscore, int *_max_off)\n{")
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/ksw_oracle.c.in" ksw_oracle_source)
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/extend_neon.c.in" extend_neon_source)
file(WRITE "${generated}/ksw.c" "#include \"metal_backend.h\"\n${ksw_source}\n${ksw_oracle_source}\n${extend_neon_source}")

# Metal shares the BWT and the restored (not the index-time) suffix array in
# place, which needs page-aligned storage.
file(READ "${bwa_SOURCE_DIR}/bwt.c" bwt_source)
seam_replace(bwt_source "calloc(bwt->bwt_size, 4)"
    "(wgse_metal_enabled() ? wgse_metal_calloc(bwt->bwt_size, 4) : calloc(bwt->bwt_size, 4))")
seam_replace(bwt_source
    "bwt->sa = (bwtint_t*)calloc(bwt->n_sa, sizeof(bwtint_t));\n\tbwt->sa[0] = -1;"
    "bwt->sa = (bwtint_t*)(wgse_metal_enabled() ? wgse_metal_calloc(bwt->n_sa, sizeof(bwtint_t)) : calloc(bwt->n_sa, sizeof(bwtint_t)));\n\tbwt->sa[0] = -1;")
# An abandoned GPU command (watchdog) may still read the shared index.
seam_replace(bwt_source "free(bwt->sa); free(bwt->bwt);"
    "wgse_metal_free(bwt->sa); wgse_metal_free(bwt->bwt);")
file(WRITE "${generated}/bwt.c" "#include \"metal_backend.h\"\n${bwt_source}")

# With Metal, map the next chunk while this one pairs (fastmap_stages.c.in).
file(READ "${bwa_SOURCE_DIR}/fastmap.c" fastmap_source)
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/fastmap_stages.c.in" fastmap_stages_source)
seam_replace(fastmap_source "int main_mem(int argc, char *argv[])"
    "${fastmap_stages_source}int main_mem(int argc, char *argv[])")
seam_replace(fastmap_source "kt_pipeline(no_mt_io? 1 : 2, process, &aux, 3);"
    "if (!no_mt_io && !(opt->flag & MEM_F_SMARTPE) && wgse_metal_enabled())\n\t\tkt_pipeline(3, wgse_process, &aux, 4);\n\telse kt_pipeline(no_mt_io? 1 : 2, process, &aux, 3);")
# Denser suffix-array samples from the index's side file, if it has one.
seam_replace(fastmap_source "\tif (ignore_alt)\n\t\tfor (i = 0; i < aux.idx->bns->n_seqs; ++i)"
    "\twgse_index_loaded(argv[optind], aux.idx->bwt);\n\tif (ignore_alt)\n\t\tfor (i = 0; i < aux.idx->bns->n_seqs; ++i)")
file(WRITE "${generated}/fastmap.c" "#include \"metal_backend.h\"\n${fastmap_source}")

file(READ "${bwa_SOURCE_DIR}/main.c" main_source)
seam_replace(main_source "int main(int argc, char *argv[])"
    "int wgse_bwa_main(int argc, char *argv[])")
# Keep upstream's "[main] Version/CMD/Real time" stderr lines.
seam_replace(main_source "__func__" "\"main\"")
file(WRITE "${generated}/main.c" "${main_source}")
set(patched_sources bwamem bwamem_pair bwt bntseq ksw fastmap main)
