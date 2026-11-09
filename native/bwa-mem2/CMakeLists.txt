find_package(ZLIB REQUIRED)
find_package(Threads REQUIRED)

FetchContent_Declare(mem2
    URL https://github.com/bwa-mem2/bwa-mem2/archive/refs/tags/v2.3.tar.gz
    URL_HASH SHA256=2f240f37d25e483d2ad403cd85f85f47f1030bf43bae184fe699a79a186e609b
    SOURCE_SUBDIR unused)
FetchContent_Declare(neon
    URL https://github.com/DLTcollab/sse2neon/archive/refs/tags/v1.8.0.tar.gz
    URL_HASH SHA256=e251746e3b761f3f0de1ad462b1efe53532341b6b0498d394765fceb85ce8a46
    SOURCE_SUBDIR unused)
FetchContent_Declare(safe
    URL https://github.com/intel/safestringlib/archive/245c4b8cff1d2e7338b7f3a82828fc8e72b29549.tar.gz
    URL_HASH SHA256=6dea4f4accdc14eea706c88bb11725e8c2cd69b0d8c5f5ee19a0669d1883a901)
# bwa-mem3 0.16.0 (as in ../bwa-mem3; the first declaration wins), for its
# exact mate-rescue pruning headers.
FetchContent_Declare(mem3
    URL https://github.com/fg-labs/bwa-mem3/archive/refs/tags/v0.16.0.tar.gz
    URL_HASH SHA256=28f7e4be7cf359e97b59f2925bb67b27905058c13e6c8af08507b13f2c10662c
    SOURCE_SUBDIR unused)
FetchContent_MakeAvailable(mem2 neon safe mem3)
set_target_properties(safestring_shared PROPERTIES EXCLUDE_FROM_ALL TRUE)
target_compile_options(safestring_objlib PRIVATE
    -include "${CMAKE_CURRENT_SOURCE_DIR}/safe_compat.h")

set(generated "${CMAKE_CURRENT_BINARY_DIR}/generated")
file(MAKE_DIRECTORY "${generated}/intrinsics")
foreach(header emmintrin.h immintrin.h smmintrin.h tmmintrin.h xmmintrin.h x86intrin.h)
    file(WRITE "${generated}/intrinsics/${header}" "#include \"sse2neon.h\"\n")
endforeach()
# Build from a patched copy so quote-included upstream headers can be changed.
set(mem2_src "${generated}/mem2-src")
file(COPY "${mem2_SOURCE_DIR}/src/" DESTINATION "${mem2_src}")
set(WGSE_BATCH_SIZE 2048 CACHE STRING "Reads per BWA-MEM2 worker batch")
file(READ "${mem2_SOURCE_DIR}/src/macro.h" macro_source)
string(REGEX REPLACE "#define BATCH_SIZE 512" "#define BATCH_SIZE ${WGSE_BATCH_SIZE}"
    patched_macro "${macro_source}")
if(patched_macro STREQUAL macro_source AND NOT WGSE_BATCH_SIZE EQUAL 512)
    message(FATAL_ERROR "Unexpected upstream BWA-MEM2 BATCH_SIZE definition")
endif()
file(WRITE "${mem2_src}/macro.h" "${patched_macro}")
file(READ "${mem2_SOURCE_DIR}/src/bandedSWA.cpp" banded_source)
string(REGEX MATCHALL "void BandedPairWiseSW::getScores(8|16)\\(" batch_seams "${banded_source}")
list(LENGTH batch_seams seam_count)
if(NOT seam_count EQUAL 6)
    message(FATAL_ERROR "Unexpected upstream BWA-MEM2 batch interface")
endif()
string(REGEX REPLACE
    "(void BandedPairWiseSW::getScores(8|16)\\([^{]+\\{)"
    "\\1\n    WgseExtensionScope wgse_scope(numPairs);\n    if (wgse_metal_extend(pairArray, seqBufRef, seqBufQer, numPairs, w,\n        o_del, e_del, o_ins, e_ins, zdrop, end_bonus, mat, \\2)) return;\n"
    banded_source "${banded_source}")
# The 128-bit kernels' band tests compare _mm_movemask_epi8() masks only with
# zero or all lanes set (dmask16 is 0xAAAA, one bit per 16-bit lane): bsw_lanes.h.
string(REGEX MATCHALL "_mm_movemask_epi8\\([a-z0-9_]+\\) & dmask16" lane16_tests "${banded_source}")
string(REGEX MATCHALL "_mm_movemask_epi8\\(" lane_tests "${banded_source}")
list(LENGTH lane16_tests lane16_count)
list(LENGTH lane_tests lane_count)
if(NOT lane16_count EQUAL 7 OR NOT lane_count EQUAL 15)
    message(FATAL_ERROR "Unexpected upstream BWA-MEM2 band tests")
endif()
string(REGEX REPLACE "_mm_movemask_epi8\\(([a-z0-9_]+)\\) & dmask16" "wgse_lanes16(\\1)"
    banded_source "${banded_source}")
string(REGEX REPLACE "_mm_movemask_epi8\\(([a-z0-9_]+)\\)" "wgse_lanes8(\\1)"
    banded_source "${banded_source}")
file(WRITE "${mem2_src}/bandedSWA.cpp"
    "#include \"metal_backend.h\"\n#include \"bsw_lanes.h\"\n${banded_source}")
function(mem2_seam variable before after)
    string(FIND "${${variable}}" "${before}" position)
    if(position EQUAL -1)
        message(FATAL_ERROR "Unexpected upstream BWA-MEM2 source: ${before}")
    endif()
    string(REPLACE "${before}" "${after}" patched "${${variable}}")
    set(${variable} "${patched}" PARENT_SCOPE)
endfunction()
# GPU mate rescue, shared with wgse-bwa (native/bwa): BWA-MEM2's mem_sam_pe(),
# mem_matesw() and ksw_align2() are BWA 0.7.19's.
set(bwa_dir "${CMAKE_CURRENT_SOURCE_DIR}/../bwa")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${bwa_dir}/rescue_oracle.c.in" "${bwa_dir}/rescue_types.h" "${bwa_dir}/rescue.metal"
    "${bwa_dir}/pac_oracle.c.in" "${bwa_dir}/sort_oracle.c.in" "${bwa_dir}/ksw_oracle.c.in"
    "${CMAKE_CURRENT_SOURCE_DIR}/mem_stages.cpp.in"
    "${CMAKE_CURRENT_SOURCE_DIR}/fastmap_stages.cpp.in"
    "${CMAKE_CURRENT_SOURCE_DIR}/seeding_types.h" "${CMAKE_CURRENT_SOURCE_DIR}/seeding.metal"
    "${CMAKE_CURRENT_SOURCE_DIR}/smem_lockstep.cpp.in")
# GPU SMEM collection reads FMI_search's checkpoints in place, so they are
# page-aligned for Metal.
file(READ "${mem2_SOURCE_DIR}/src/FMI_search.h" fmi_header)
mem2_seam(fmi_header "    int64_t sentinel_index;\nprivate:"
    "    int64_t sentinel_index;\n    const int64_t *wgse_count() const { return count; }\n    const CP_OCC *wgse_cp_occ() const { return cp_occ; }\n    void wgse_getSMEMsOnePosLockstep(uint8_t *enc_qdb, int16_t *query_pos_array,\n        int32_t *min_intv_array, int32_t *rid_array, int32_t numReads, int32_t max_readlength,\n        int32_t minSeedLen, SMEM *matchArray, int64_t *__numTotalSmem, const bseq1_t *seq_,\n        int32_t *query_cum_len_ar);\n    int64_t wgse_bwtSeedStrategyLockstep(uint8_t *enc_qdb, int32_t *max_intv_array,\n        int32_t numReads, const bseq1_t *seq_, int32_t *query_cum_len_ar, int32_t minSeedLen,\n        SMEM *matchArray);\n    const void *wgse_prefixes();\nprivate:")
file(WRITE "${mem2_src}/FMI_search.h" "${fmi_header}")
file(READ "${mem2_SOURCE_DIR}/src/FMI_search.cpp" fmi_source)
mem2_seam(fmi_source "(CP_OCC *)_mm_malloc(cp_occ_size * sizeof(CP_OCC), 64)) == NULL"
    "(CP_OCC *)wgse_metal_index_alloc(cp_occ_size * sizeof(CP_OCC))) == NULL")
# Suffix-array locate: an SMEM yields min(s, max_occ) rows, but upstream sizes
# its row buffers by the sum of s (megabytes for repeats, faulted in on every
# read), and its precedence slip `pos & SA_COMPX_MASK == 0` means sampled rows
# are never prefetched. Same rows, same results.
mem2_seam(fmi_source "        mem_lim += smem.s;"
    "        mem_lim += smem.s < max_occ ? smem.s : max_occ;")
mem2_seam(fmi_source "if (pos & SA_COMPX_MASK == 0) {" "if ((pos & SA_COMPX_MASK) == 0) {")
mem2_seam(fmi_source "if (sp & SA_COMPX_MASK == 0) {" "if ((sp & SA_COMPX_MASK) == 0) {")
# SMEM collection's three passes step up to 16 reads in turn (smem_lockstep.cpp.in).
mem2_seam(fmi_source "        getSMEMsOnePosOneThread(enc_qdb,\n                                query_pos_array,"
    "        if (wgse_exact_cpu())\n            wgse_getSMEMsOnePosLockstep(enc_qdb, query_pos_array, min_intv_array, rid_array,\n                tail, max_readlength, minSeedLen, matchArray, __numTotalSmem, seq_,\n                query_cum_len_ar);\n        else getSMEMsOnePosOneThread(enc_qdb,\n                                query_pos_array,")
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/smem_lockstep.cpp.in" smem_lockstep)
file(WRITE "${mem2_src}/FMI_search.cpp" "#include \"metal_backend.h\"\n${fmi_source}\n${smem_lockstep}")
file(READ "${mem2_SOURCE_DIR}/src/bwamem.cpp" bwamem_source)
include(extension_pipeline.cmake)
mem2_seam(bwamem_source "    kt_for(worker_bwt, &w, n_); // SMEMs (+SAL)"
    "    const int wgse_unit = wgse_kt_for_bwt(w, n_); // SMEMs (+SAL)")
mem2_seam(bwamem_source "    kt_for(worker_aln, &w, n_); // BSW"
    "    wgse_kt_for(worker_aln, &w, n_, wgse_unit); // BSW")
mem2_seam(bwamem_source "    fmi->getSMEMsOnePosOneThread(enc_qdb,\n                                 query_pos_ar,\n                                 min_intv_ar,\n                                 rid,\n                                 pos,\n                                 pos,\n                                 seq_,\n                                 query_cum_len_ar,\n                                 max_readlength,\n                                 opt->min_seed_len,\n                                 matchArray + num_smem1,\n                                 &num_smem2);"
    "    if (wgse_exact_cpu())\n        fmi->wgse_getSMEMsOnePosLockstep(enc_qdb, query_pos_ar, min_intv_ar, rid, pos,\n            max_readlength, opt->min_seed_len, matchArray + num_smem1, &num_smem2, seq_,\n            query_cum_len_ar);\n    else fmi->getSMEMsOnePosOneThread(enc_qdb,\n                                 query_pos_ar,\n                                 min_intv_ar,\n                                 rid,\n                                 pos,\n                                 pos,\n                                 seq_,\n                                 query_cum_len_ar,\n                                 max_readlength,\n                                 opt->min_seed_len,\n                                 matchArray + num_smem1,\n                                 &num_smem2);")
mem2_seam(bwamem_source "        num_smem3 = fmi->bwtSeedStrategyAllPosOneThread(enc_qdb, min_intv_ar,"
    "        num_smem3 = (fmi->*(wgse_exact_cpu() ? &FMI_search::wgse_bwtSeedStrategyLockstep\n            : &FMI_search::bwtSeedStrategyAllPosOneThread))(enc_qdb, min_intv_ar,")
mem2_seam(bwamem_source "    matchArray = mem_collect_smem(fmi, opt,"
    "    if (SMEM *gpu = wgse_metal_smems(fmi, opt, seq_, nseq, mmc, num_smem, tid)) matchArray = gpu;\n    else matchArray = mem_collect_smem(fmi, opt,")
mem2_seam(bwamem_source "    kt_for(worker_sam, &w,  n_);   // SAM"
    "    if (!wgse_metal_pair(opt, w.fmi->idx->bns, w.fmi->idx->pac, pes, n, seqs, w.regs,\n        w.nthreads, wgse_worker_sam_pair, &w))\n        wgse_kt_for(worker_sam, &w, n_, wgse_sam_unit);   // SAM")
# worker_sam()'s paired-end loop body for one pair.
string(APPEND bwamem_source "
void wgse_worker_sam_pair(void *data, long pair, int)
{
    worker_t *w = (worker_t*) data;
    long i = pair << 1;
    mem_sam_pe(w->opt, w->fmi->idx->bns, w->fmi->idx->pac, w->pes,
               (w->n_processed >> 1) + pair, &w->seqs[i], &w->regs[i]);
    free(w->regs[i].a);
    free(w->regs[i+1].a);
}

// worker_bwt() for a unit of any size, with the unit's share of the seed
// buffer (upstream's for all but a chunk's last unit, whose seeds may be
// allocated as they grow).
static void wgse_worker_bwt_unit(void *data, int seq_id, int batch_size, int tid)
{
    worker_t *w = (worker_t*) data;
    mem_kernel1_core(w->fmi, w->opt, w->seqs + seq_id, batch_size, w->chain_ar + seq_id,
                     w->seedBuf + (int64_t) seq_id * AVG_SEEDS_PER_READ,
                     (int64_t) batch_size * AVG_SEEDS_PER_READ, &w->mmc, tid);
}

// kt_for(worker_bwt)'s units (BATCH_SIZE reads each), with SMEMs from the GPU.
struct wgse_bwt_t { worker_t *w; int n; };
static void wgse_worker_bwt(void *data, long unit, int tid)
{
    wgse_bwt_t *b = (wgse_bwt_t*) data;
    int st = unit * BATCH_SIZE;
    worker_bwt(b->w, st, b->n - st < BATCH_SIZE ? b->n - st : BATCH_SIZE, tid);
}
int wgse_kt_for_bwt(worker_t &w, int n)
{
    wgse_bwt_t b = {&w, n};
    if (wgse_metal_seed(w.fmi, w.opt, w.seqs, n, w.nthreads, wgse_worker_bwt, &b))
        return BATCH_SIZE;
    const int unit = wgse_cpu_unit(n, w.nthreads);
    wgse_kt_for(wgse_worker_bwt_unit, &w, n, unit);
    return unit;
}
")
# With Metal, fastmap.cpp does not load the .0123 file (the 2-bit .pac unpacked
# to a byte per base, 6.2 GB for hs38), and extension windows are decoded from
# the .pac instead: the same bases.
set(ref_window "#else\n            seq = ref_string + beg;\n#endif")
mem2_seam(bwamem_source "${ref_window}\n        } else { // forward strand"
    "#else\n            seq = ref_string ? ref_string + beg : wgse_ref_window(l_pac, pac, beg, end);\n#endif\n        } else { // forward strand")
mem2_seam(bwamem_source "${ref_window}"
    "#else\n            seq = ref_string ? ref_string + beg : wgse_ref_window(l_pac, pac, beg, end);\n#endif")
# mem_sort_dedup_patch()'s sorts move pointers instead of regions (native/bwa/sort_oracle.c.in).
mem2_seam(bwamem_source "    ks_introsort(mem_ars2, n, a); // sort by the END position, not START!"
    "    if (wgse_exact_cpu()) wgse_sort_alnreg_by_end(n, a);\n    else ks_introsort(mem_ars2, n, a);")
mem2_seam(bwamem_source "    n = m;\n    ks_introsort(mem_ars, n, a);"
    "    n = m;\n    if (wgse_exact_cpu()) wgse_sort_alnreg_by_score(n, a);\n    else ks_introsort(mem_ars, n, a);")
file(READ "${bwa_dir}/sort_oracle.c.in" sort_oracle)
string(APPEND bwamem_source "${sort_oracle}")
# mem_process_seqs() in two halves, so that with Metal the pipeline maps chunk
# i + 1 while chunk i pairs (mostly GPU mate rescue). Each half is upstream's
# code on the chunk's own alignments, so its output does not depend on overlap.
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/mem_stages.cpp.in" mem_stages)
string(APPEND bwamem_source "${mem_stages}")
file(WRITE "${mem2_src}/bwamem.cpp"
    "#include \"metal_backend.h\"\n#include \"rescue_backend.h\"\n${bwamem_source}")
# bns_get_seq()'s unpacking loops (mate-rescue windows) and wgse_ref_window()
# decode with NEON (native/bwa/pac_oracle.c.in).
file(READ "${mem2_SOURCE_DIR}/src/bntseq.cpp" bntseq_source)
mem2_seam(bntseq_source "\t\t\tfor (k = end_f; k > beg_f; --k) {\n\t\t\t\tseq[l++] = 3 - _get_pac(pac, k);\n\t\t\t}"
    "\t\t\tif (wgse_exact_cpu()) wgse_unpack_pac_reverse(pac, beg_f, end_f, seq);\n\t\t\telse for (k = end_f; k > beg_f; --k) {\n\t\t\t\tseq[l++] = 3 - _get_pac(pac, k);\n\t\t\t}")
mem2_seam(bntseq_source "\t\t\tfor (k = beg; k < end; ++k) {\n\t\t\t\tseq[l++] = _get_pac(pac, k);\n\t\t\t}"
    "\t\t\tif (wgse_exact_cpu()) wgse_unpack_pac(pac, beg, end, seq);\n\t\t\telse for (k = beg; k < end; ++k) {\n\t\t\t\tseq[l++] = _get_pac(pac, k);\n\t\t\t}")
file(READ "${bwa_dir}/pac_oracle.c.in" pac_oracle)
file(WRITE "${mem2_src}/bntseq.cpp" "#include \"metal_backend.h\"
#include \"rescue_backend.h\"
${bntseq_source}
${pac_oracle}
#include <vector>
uint8_t *wgse_ref_window(int64_t l_pac, const uint8_t *pac, int64_t beg, int64_t end)
{
    static thread_local std::vector<uint8_t> window;
    if (window.size() < (size_t)(end - beg)) window.resize(end - beg);
    if (beg >= l_pac) wgse_unpack_pac_reverse(pac, (l_pac<<1) - 1 - end, (l_pac<<1) - 1 - beg, window.data());
    else wgse_unpack_pac(pac, beg, end, window.data());
    return window.data();
}
")
# Batched ksw_align2() for CPU threads' mate rescue (native/bwa/ksw_oracle.c.in),
# with ksw_u8()'s horizontal maximum and zero test in NEON; upstream code is unchanged.
file(READ "${mem2_SOURCE_DIR}/src/ksw.cpp" ksw_source)
file(READ "${bwa_dir}/ksw_oracle.c.in" ksw_oracle)
file(WRITE "${mem2_src}/ksw.cpp" "#include \"${bwa_dir}/ksw_batch.h\"
${ksw_source}
#undef __max_16
#define __max_16(ret, xx) (ret) = vmaxvq_u8(vreinterpretq_u8_m128i(xx))
#define allzero_16(xx) (vmaxvq_u8(vreinterpretq_u8_m128i(xx)) == 0)
${ksw_oracle}")
file(READ "${mem2_SOURCE_DIR}/src/bwamem_pair.cpp" pair_source)
set(rescue_call "aln = ksw_align2(l_ms, seq, re - rb, ref, 5,\n                             opt->mat, opt->o_del, opt->e_del,\n                             opt->o_ins, opt->e_ins, xtra, 0);")
mem2_seam(pair_source "${rescue_call}"
    "if (!wgse_metal_matesw(opt, ms, seq, l_ms, ref, is_rev, rb, re, xtra, &aln))\n                aln = wgse_rescue_align(opt, l_ms, seq, re - rb, ref, xtra);")
file(READ "${bwa_dir}/rescue_oracle.c.in" rescue_oracle)
file(WRITE "${mem2_src}/bwamem_pair.cpp"
    "#include \"rescue_backend.h\"\n${pair_source}\n${rescue_oracle}")
file(READ "${mem2_SOURCE_DIR}/src/fastmap.cpp" fastmap_source)
string(REGEX REPLACE
    "void __cpuid\\(unsigned int i, unsigned int cpuid\\[4\\]\\) \\{[^}]+\\}"
    "void __cpuid(unsigned int, unsigned int cpuid[4]) {\n    memset(cpuid, 0, 4 * sizeof(unsigned int));\n}"
    fastmap_source "${fastmap_source}")
# Upstream ignores -o write/close errors and always returns 0.
set(output_close "    if (is_o) {\n        fclose(aux.fp);\n    }\n")
set(final_return "    display_stats(nt);\n\n    return 0;\n}")
string(FIND "${fastmap_source}" "${output_close}" close_at)
string(FIND "${fastmap_source}" "${final_return}" return_at)
if(close_at EQUAL -1 OR return_at EQUAL -1)
    message(FATAL_ERROR "Unexpected upstream BWA-MEM2 output handling")
endif()
string(REPLACE "${output_close}"
    "    int wgse_output_status = 0;\n    if (is_o) {\n        int write_error = ferror(aux.fp);\n        if (fclose(aux.fp) != 0 || write_error) {\n            fprintf(stderr, \"[E::main_mem] failed to write output file\\n\");\n            wgse_output_status = 1;\n        }\n    }\n"
    fastmap_source "${fastmap_source}")
string(REPLACE "${final_return}" "    display_stats(nt);\n\n    return wgse_output_status;\n}"
    fastmap_source "${fastmap_source}")
set(process_call "            mem_process_seqs(opt,
                             aux->n_processed,
                             ret->n_seqs,
                             ret->seqs,
                             aux->pes0,
                             w);")
mem2_seam(fastmap_source "${process_call}" "            if (wgse_overlap)
                wgse_mapped = wgse_mem_map(opt, aux->n_processed, ret->n_seqs, ret->seqs,
                                           aux->pes0, w);
            else
${process_call}")
mem2_seam(fastmap_source "    FILE *fr = fopen(binary_seq_file, \"r\");\n"
    "    ref_string = 0;\n    aux.ref_string = 0;\n    if (!wgse_exact_cpu()) {\n    FILE *fr = fopen(binary_seq_file, \"r\");\n")
mem2_seam(fastmap_source "    fprintf(stderr, \"* Ref file: %s\\n\", argv[optind]);\n"
    "    wgse_memory_preflight(argv[optind], fixed_chunk_size > 0 ? int64_t(fixed_chunk_size)\n        : int64_t(opt->chunk_size) * opt->n_threads);\n    fprintf(stderr, \"* Ref file: %s\\n\", argv[optind]);\n")
mem2_seam(fastmap_source "    fprintf(stderr, \"* Reference genome size: %ld bp\\n\", rlen);\n"
    "    fprintf(stderr, \"* Reference genome size: %ld bp\\n\", rlen);\n    }\n")
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/fastmap_stages.cpp.in" fastmap_stages)
mem2_seam(fastmap_source "static void *ktp_worker(void *data)"
    "${fastmap_stages}\nstatic void *ktp_worker(void *data)")
mem2_seam(fastmap_source "        w->data = kt_pipeline(p->shared,"
    "        w->data = wgse_kt_pipeline(p->shared,")
mem2_seam(fastmap_source "    int n_steps = 3;\n"
    "    int n_steps = 3;\n    wgse_overlap = p_nt == 2 && (opt->flag & MEM_F_PE)\n        && !(opt->flag & MEM_F_SMARTPE) && wgse_metal_overlap();\n    if (wgse_overlap) n_steps = 4, p_nt = 3;\n")
# wgse_mapped: step 1's wgse_mem_map() result, taken by the same worker right after it.
file(WRITE "${mem2_src}/fastmap.cpp" "#include \"metal_backend.h\"
#include \"rescue_backend.h\"
static bool wgse_overlap = false;
static void *wgse_mapped = 0;
${fastmap_source}")
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/extension.metal" kernel_source)
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/extension_tiled.metal" tiled_source)
string(APPEND kernel_source "\n${tiled_source}")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/extension.metal"
    "${CMAKE_CURRENT_SOURCE_DIR}/extension_tiled.metal")
file(READ "${bwa_dir}/rescue_types.h" rescue_types)
file(READ "${bwa_dir}/rescue.metal" rescue_source)
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/seeding_types.h" seeding_types)
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/seeding.metal" seeding_source)
configure_file(kernel_source.h.in "${generated}/kernel_source.h" @ONLY)

set(core_names
    bwtindex utils memcpy_bwamem kthread kstring ksw bntseq bwamem
    profiling FMI_search read_index_ele bwamem_pair kswv bwa bwamem_extra kopen)
foreach(name IN LISTS core_names)
    list(APPEND core_sources "${mem2_src}/${name}.cpp")
endforeach()
add_library(mem2_core STATIC ${core_sources}
    "${mem2_src}/bandedSWA.cpp" "${mem2_src}/fastmap.cpp")
target_compile_definitions(mem2_core PUBLIC
    ENABLE_PREFETCH V17=1 MATE_SORT=0 SAIS=1 __SSE2__=1 __SSE4_1__=1)
target_include_directories(mem2_core PUBLIC
    "${generated}/intrinsics" "${CMAKE_CURRENT_SOURCE_DIR}")
target_include_directories(mem2_core SYSTEM PUBLIC
    "${neon_SOURCE_DIR}" "${mem2_src}" "${safe_SOURCE_DIR}/include")
target_compile_options(mem2_core PRIVATE -Wno-register -Wno-unused-result)
target_compile_options(mem2_core PUBLIC
    -include "${CMAKE_CURRENT_SOURCE_DIR}/arm_compat.h" "-Wno-error=#warnings")
target_link_libraries(mem2_core PUBLIC safestring_static ZLIB::ZLIB Threads::Threads)

# Compiled apart from mem2_core: bwa-mem3's source directory has headers named like BWA-MEM2's.
add_library(mem2_rescue_prune STATIC rescue_prune.cpp)
target_include_directories(mem2_rescue_prune SYSTEM PRIVATE "${mem3_SOURCE_DIR}/src")
target_compile_options(mem2_rescue_prune PRIVATE -Wall -Wextra -Werror)
add_library(metal_backend STATIC metal_backend.mm)
target_include_directories(metal_backend PRIVATE "${generated}")
target_compile_options(metal_backend PRIVATE -fobjc-arc -Wall -Wextra -Werror)
target_link_libraries(metal_backend PUBLIC mem2_core mem2_rescue_prune wgse_metal_common)
add_executable(wgse-bwa-mem2 main.cpp)
target_link_libraries(wgse-bwa-mem2 PRIVATE metal_backend)
set(WGSE_BWA_MEM2_VERSION 0.5.0)
target_compile_definitions(wgse-bwa-mem2 PRIVATE WGSE_VERSION="${WGSE_BWA_MEM2_VERSION}")
target_compile_options(wgse-bwa-mem2 PRIVATE -Wall -Wextra -Werror)

if(BUILD_TESTING)
    target_compile_definitions(metal_backend PUBLIC WGSE_METAL_BENCHMARK_TOOLS=1)
    add_executable(extension_test tests/extension_test.cpp)
    target_link_libraries(extension_test PRIVATE metal_backend)
    target_compile_options(extension_test PRIVATE -Wall -Wextra -Werror)
    add_test(NAME metal_extension_parity COMMAND extension_test)
    add_test(NAME metal_tiled_parity COMMAND extension_test tiled)
    add_test(NAME metal_striped_parity COMMAND extension_test striped)
    add_test(NAME bwa_mem2_capabilities COMMAND wgse-bwa-mem2 capabilities --json)
    add_test(NAME bwa_mem2_smoke COMMAND "${PROJECT_SOURCE_DIR}/tests/smoke.sh"
        $<TARGET_FILE:wgse-bwa-mem2> "${CMAKE_CURRENT_BINARY_DIR}/smoke" force-gpu)
    add_test(NAME bwa_mem2_rescue_parity
        COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tests/rescue_parity.sh"
        $<TARGET_FILE:wgse-bwa-mem2> "${CMAKE_CURRENT_BINARY_DIR}/rescue-parity")
    add_test(NAME bwa_mem2_seed_parity
        COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tests/seed_parity.sh"
        $<TARGET_FILE:wgse-bwa-mem2> "${CMAKE_CURRENT_BINARY_DIR}/seed-parity")
    # The batched rescue against BWA-MEM2's own ksw_align2().
    add_executable(mem2_ksw_batch_check "${bwa_dir}/tests/ksw_batch_check.c")
    target_compile_options(mem2_ksw_batch_check PRIVATE -Wall -Wextra -Werror)
    target_link_libraries(mem2_ksw_batch_check PRIVATE mem2_core)
    add_test(NAME bwa_mem2_ksw_batch COMMAND mem2_ksw_batch_check)
    add_executable(extension_benchmark tests/extension_benchmark.cpp)
    target_link_libraries(extension_benchmark PRIVATE metal_backend)
    target_compile_options(extension_benchmark PRIVATE -Wall -Wextra -Werror)
    set(WGSE_REPLAY_DIR "" CACHE PATH "Optional captured extension parity fixtures")
    if(WGSE_REPLAY_DIR)
        add_test(NAME metal_real_batch_parity COMMAND extension_benchmark
            --replay "${WGSE_REPLAY_DIR}" --limit 64 --kernel row)
        add_test(NAME metal_tiled_real_batch_parity COMMAND extension_benchmark
            --replay "${WGSE_REPLAY_DIR}" --limit 64 --kernel tiled)
        add_test(NAME metal_striped_real_batch_parity COMMAND extension_benchmark
            --replay "${WGSE_REPLAY_DIR}" --limit 64 --kernel striped --workers 8)
    endif()
    # Hosted CI runners may lack a usable Metal device; those tests exit 77.
    get_property(tests DIRECTORY PROPERTY TESTS)
    set_tests_properties(${tests} PROPERTIES SKIP_RETURN_CODE 77)
endif()

install(TARGETS wgse-bwa-mem2 RUNTIME DESTINATION bin)
install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/../../LICENSE" DESTINATION share/licenses/wgse-bwa-mem2
    RENAME WGSE-LICENSE)
install(FILES "${mem2_SOURCE_DIR}/LICENSE" DESTINATION share/licenses/wgse-bwa-mem2
    RENAME BWA-MEM2-LICENSE)
install(FILES "${neon_SOURCE_DIR}/LICENSE" DESTINATION share/licenses/wgse-bwa-mem2
    RENAME sse2neon-LICENSE)
install(FILES "${safe_SOURCE_DIR}/LICENSE" DESTINATION share/licenses/wgse-bwa-mem2
    RENAME safestringlib-LICENSE)
install(FILES "${mem3_SOURCE_DIR}/LICENSE" DESTINATION share/licenses/wgse-bwa-mem2
    RENAME BWA-MEM3-LICENSE)
