#include "runtime.h"
#include "seed_oracle.h"
#include "seeding_source.h"
#include "seeding_types.h"

#include <algorithm>
#include <chrono>
#include <climits>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#include <zlib.h>

#include "kseq.h"
KSEQ_INIT(gzFile, gzread)

extern "C" void kt_for(int, void (*)(void *, long, int), void *, long);

namespace {
using Clock = std::chrono::steady_clock;
using Intervals = std::vector<bwtintv_t>;
static_assert(sizeof(SeedInterval) == sizeof(bwtintv_t));
static_assert(sizeof(SeedParameters) == 80 && sizeof(SeedRead) == 8);

double seconds(Clock::time_point start) {
    return std::chrono::duration<double>(Clock::now() - start).count();
}

size_t positive(const char *text) {
    size_t used = 0;
    std::string value(text);
    unsigned long long result = std::stoull(value, &used);
    if (used != value.size() || value[0] == '-' || result == 0 || result > INT_MAX)
        throw std::runtime_error("expected a positive integer <= INT_MAX");
    return static_cast<size_t>(result);
}

struct Reads {
    std::vector<uint8_t> bases;
    std::vector<SeedRead> reads;
};

Reads load_reads(const char *path, size_t limit) {
    gzFile file = gzopen(path, "rb");
    if (!file) throw std::runtime_error(std::string("cannot open FASTQ: ") + path);
    struct CloseFile {
        gzFile file;
        ~CloseFile() { gzclose(file); }
    } close{file};
    std::unique_ptr<kseq_t, decltype(&kseq_destroy)> record(kseq_init(file), kseq_destroy);
    Reads result;
    int status = 0;
    while (result.reads.size() < limit && (status = kseq_read(record.get())) >= 0) {
        if (record->seq.l > INT_MAX || result.bases.size() + record->seq.l > UINT32_MAX)
            throw std::runtime_error("read length or buffer exceeds integer limits");
        if (record->qual.l != record->seq.l)
            throw std::runtime_error("expected FASTQ with full quality strings");
        result.reads.push_back({static_cast<uint32_t>(result.bases.size()),
            static_cast<uint32_t>(record->seq.l)});
        for (size_t i = 0; i < record->seq.l; ++i) {
            unsigned char base = static_cast<unsigned char>(record->seq.s[i]);
            result.bases.push_back(nst_nt4_table[base]);
        }
    }
    int gzip_status = Z_OK;
    gzerror(file, &gzip_status);
    if (status < -1 || gzip_status != Z_OK || result.reads.empty())
        throw std::runtime_error("empty, truncated or invalid FASTQ");
    return result;
}

struct CpuJob {
    const bwt_t *bwt;
    const mem_opt_t *options;
    const Reads &reads;
    std::vector<Intervals> &out;
    std::vector<void *> scratch;

    CpuJob(const bwt_t *index, const mem_opt_t *opt, const Reads &input,
        std::vector<Intervals> &output, int threads)
        : bwt(index), options(opt), reads(input), out(output), scratch(threads) {
        for (void *&entry : scratch) entry = wgse_seed_scratch_create();
    }
    ~CpuJob() {
        for (void *entry : scratch) wgse_seed_scratch_destroy(entry);
    }
    static void work(void *opaque, long i, int tid) {
        auto &job = *static_cast<CpuJob *>(opaque);
        const auto &read = job.reads.reads[i];
        const bwtintv_v *intervals = wgse_seed_collect(job.options, job.bwt,
            static_cast<int>(read.length), job.reads.bases.data() + read.offset,
            job.scratch[tid]);
        if (intervals->n)
            job.out[i].assign(intervals->a, intervals->a + intervals->n);
        else
            job.out[i].clear();
    }
};

bool equal(const bwtintv_t &cpu, const SeedInterval &gpu) {
    return cpu.info == gpu.info && cpu.x[0] == gpu.x[0]
        && cpu.x[1] == gpu.x[1] && cpu.x[2] == gpu.x[2];
}

class GpuSeeds {
public:
    wgse::metal::Runtime runtime;
    id<MTLComputePipelineState> pipeline = nil;
    id<MTLBuffer> index = nil, parameters = nil, sequences = nil, reads = nil;
    id<MTLBuffer> scratch = nil, output = nil, results = nil;
    size_t capacity;
    double kernel_seconds = 0;
    size_t fallback = 0, intervals = 0;
    size_t reasons[4]{};

    explicit GpuSeeds(size_t batch) : capacity(batch) {
        if (!runtime.device()) return;
        pipeline = runtime.compile(wgse_seeding_source, "collect_seeds");
        if (!pipeline) throw std::runtime_error(runtime.failure());
        parameters = runtime.allocate(sizeof(SeedParameters));
        reads = runtime.allocate(batch * sizeof(SeedRead));
        scratch = runtime.allocate(batch * seed_scratch_capacity * 3 * sizeof(SeedInterval));
        output = runtime.allocate(batch * seed_output_capacity * sizeof(SeedInterval));
        results = runtime.allocate(batch * sizeof(SeedResult));
        if (!parameters || !reads || !scratch || !output || !results)
            throw std::runtime_error("GPU batch allocation failed");
    }

    void load_index(bwt_t &bwt, const mem_opt_t &opt, size_t scratch_limit, size_t output_limit) {
        index = runtime.allocate_pages(bwt.bwt_size * sizeof(uint32_t));
        if (!index) throw std::runtime_error("page-aligned BWT allocation failed");
        memcpy(index.contents, bwt.bwt, bwt.bwt_size * sizeof(uint32_t));
        // Both processors subsequently traverse exactly the same physical BWT.
        free(bwt.bwt);
        bwt.bwt = static_cast<uint32_t *>(index.contents);
        SeedParameters p{};
        p.primary = bwt.primary;
        std::copy_n(bwt.L2, 5, p.l2);
        p.min_seed_len = opt.min_seed_len;
        p.split_len = static_cast<int>(opt.min_seed_len * opt.split_factor + .499);
        p.split_width = opt.split_width;
        p.max_mem_intv = static_cast<int>(opt.max_mem_intv);
        p.scratch_limit = static_cast<uint32_t>(scratch_limit);
        p.output_limit = static_cast<uint32_t>(output_limit);
        memcpy(parameters.contents, &p, sizeof(p));
    }

    void run(const Reads &input, std::vector<Intervals> &out, size_t group,
        const bwt_t *bwt, const mem_opt_t *opt) {
        kernel_seconds = 0;
        fallback = intervals = 0;
        std::fill_n(reasons, 4, 0);
        std::unique_ptr<void, decltype(&wgse_seed_scratch_destroy)> cpu_scratch(
            wgse_seed_scratch_create(), wgse_seed_scratch_destroy);
        for (size_t offset = 0; offset < input.reads.size(); offset += capacity) {
            @autoreleasepool {
                size_t count = std::min(capacity, input.reads.size() - offset);
                size_t start = input.reads[offset].offset;
                const auto &last = input.reads[offset + count - 1];
                size_t bytes = size_t(last.offset) + last.length - start;
                if (!sequences || sequences.length < std::max(size_t(1), bytes))
                    sequences = runtime.allocate(std::max(size_t(1), bytes));
                if (!sequences) throw std::runtime_error("GPU reads allocation failed");
                memcpy(sequences.contents, input.bases.data() + start, bytes);
                auto *descriptors = static_cast<SeedRead *>(reads.contents);
                for (size_t i = 0; i < count; ++i) {
                    descriptors[i] = input.reads[offset + i];
                    descriptors[i].offset -= start;
                }
                static_cast<SeedParameters *>(parameters.contents)->count =
                    static_cast<uint32_t>(count);
                double elapsed = 0;
                if (!runtime.dispatch(pipeline,
                    {index, parameters, sequences, reads, scratch, output, results},
                    count, group, elapsed)) throw std::runtime_error(runtime.failure());
                kernel_seconds += elapsed;
                const auto *counts = static_cast<const SeedResult *>(results.contents);
                const auto *seeds = static_cast<const SeedInterval *>(output.contents);
                for (size_t i = 0; i < count; ++i) {
                    auto &destination = out[offset + i];
                    if (counts[i].status > seed_output_overflow)
                        throw std::runtime_error("invalid GPU fallback status");
                    ++reasons[counts[i].status];
                    if (counts[i].status != seed_ok) {
                        ++fallback;
                        const auto &read = input.reads[offset + i];
                        const auto *cpu = wgse_seed_collect(opt, bwt, int(read.length),
                            input.bases.data() + read.offset, cpu_scratch.get());
                        if (cpu->n) destination.assign(cpu->a, cpu->a + cpu->n);
                        else destination.clear();
                    } else {
                        if (counts[i].count > seed_output_capacity)
                            throw std::runtime_error("invalid GPU interval count");
                        destination.resize(counts[i].count);
                        for (size_t j = 0; j < destination.size(); ++j) {
                            const auto &seed = seeds[j * count + i];
                            std::copy_n(seed.x, 3, destination[j].x);
                            destination[j].info = seed.info;
                        }
                    }
                    intervals += destination.size();
                }
            }
        }
    }
};

void verify(const std::vector<Intervals> &cpu, const std::vector<Intervals> &gpu) {
    for (size_t i = 0; i < cpu.size(); ++i) {
        if (cpu[i].size() != gpu[i].size())
            throw std::runtime_error("read " + std::to_string(i) + ": interval count "
                + std::to_string(cpu[i].size()) + " != " + std::to_string(gpu[i].size()));
        for (size_t j = 0; j < cpu[i].size(); ++j) {
            SeedInterval seed{};
            std::copy_n(gpu[i][j].x, 3, seed.x);
            seed.info = gpu[i][j].info;
            if (!equal(cpu[i][j], seed)) {
                fprintf(stderr, "read=%zu interval=%zu cpu=%llu,%llu,%llu,%llu "
                    "gpu=%llu,%llu,%llu,%llu\n", i, j,
                    cpu[i][j].x[0], cpu[i][j].x[1], cpu[i][j].x[2], cpu[i][j].info,
                    seed.x[0], seed.x[1], seed.x[2], seed.info);
                throw std::runtime_error("GPU/CPU interval mismatch");
            }
        }
    }
}

int benchmark(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "Usage: seeding_benchmark <bwa-index-prefix> <fastq> "
            "[--reads N] [--batch N] [--repeat N] [--group N] [--verify-only] "
            "[--scratch-limit N] [--output-limit N]\n");
        return 1;
    }
    size_t limit = 16384, batch = 8192, repeat = 3, group = 64;
    size_t scratch_limit = seed_scratch_capacity, output_limit = seed_output_capacity;
    bool verify_only = false;
    for (int i = 3; i < argc; ++i) {
        if (std::string(argv[i]) == "--verify-only") { verify_only = true; continue; }
        if (i + 1 == argc) throw std::runtime_error("missing option value");
        size_t value = positive(argv[i + 1]);
        std::string option = argv[i];
        if (option == "--reads") limit = value;
        else if (option == "--batch") batch = value;
        else if (option == "--repeat") repeat = value;
        else if (option == "--group") group = value;
        else if (option == "--scratch-limit") scratch_limit = value;
        else if (option == "--output-limit") output_limit = value;
        else throw std::runtime_error("unknown option: " + option);
        ++i;
    }
    if (batch > 65536) throw std::runtime_error("--batch must be <= 65536");
    if (scratch_limit > seed_scratch_capacity || output_limit > seed_output_capacity)
        throw std::runtime_error("scratch/output limit exceeds compiled capacity");
    GpuSeeds gpu(batch);
    if (!gpu.runtime.device()) {
        fprintf(stderr, "%s\n", gpu.runtime.failure().c_str());
        return 77;
    }
    printf("device=%s group=%zu batch=%zu max_threads=%lu\n",
        gpu.runtime.device().name.UTF8String, group, batch,
        static_cast<unsigned long>(gpu.pipeline.maxTotalThreadsPerThreadgroup));
    std::unique_ptr<mem_opt_t, decltype(&free)> opt(mem_opt_init(), free);
    std::string bwt_path = std::string(argv[1]) + ".bwt";
    std::unique_ptr<bwt_t, decltype(&bwt_destroy)> bwt(bwt_restore_bwt(bwt_path.c_str()),
        bwt_destroy);
    gpu.load_index(*bwt, *opt, scratch_limit, output_limit);
    // The MTLBuffer owns the BWT now. Clear the C pointer before bwt_destroy,
    // including on exceptions. No SA is needed for interval collection.
    struct Detach {
        bwt_t *value;
        ~Detach() { value->bwt = nullptr; }
    } detach{bwt.get()};
    Reads input = load_reads(argv[2], limit);
    printf("reads=%zu bases=%zu bwt_bytes=%llu seq_len=%llu\n",
        input.reads.size(), input.bases.size(), bwt->bwt_size * 4, bwt->seq_len);
    std::vector<Intervals> reference(input.reads.size()), actual(input.reads.size());
    CpuJob one(bwt.get(), opt.get(), input, reference, 1);
    CpuJob nine(bwt.get(), opt.get(), input, actual, 9);
    // Untimed warm-up includes allocation, page faults and shader initialization.
    kt_for(1, CpuJob::work, &one, static_cast<long>(input.reads.size()));
    gpu.run(input, actual, group, bwt.get(), opt.get());
    verify(reference, actual);
    if (verify_only) {
        printf("parity=exact intervals=%zu fallback=%zu length=%zu scratch=%zu output=%zu\n",
            gpu.intervals, gpu.fallback, gpu.reasons[seed_length_limit],
            gpu.reasons[seed_scratch_overflow], gpu.reasons[seed_output_overflow]);
        return 0;
    }
    for (size_t iteration = 0; iteration < repeat; ++iteration) {
        auto start = Clock::now();
        kt_for(1, CpuJob::work, &one, static_cast<long>(input.reads.size()));
        double cpu1 = seconds(start);
        start = Clock::now();
        kt_for(9, CpuJob::work, &nine, static_cast<long>(input.reads.size()));
        double cpu9 = seconds(start);
        verify(reference, actual);
        start = Clock::now();
        gpu.run(input, actual, group, bwt.get(), opt.get());
        double wall = seconds(start);
        verify(reference, actual);
        printf("iteration=%zu parity=exact intervals=%zu fallback=%zu "
            "cpu1_s=%.6f cpu1_rps=%.1f cpu9_s=%.6f cpu9_rps=%.1f "
            "gpu_s=%.6f gpu_rps=%.1f kernel_s=%.6f speedup9=%.3f\n",
            iteration + 1, gpu.intervals, gpu.fallback, cpu1, input.reads.size() / cpu1,
            cpu9, input.reads.size() / cpu9, wall, input.reads.size() / wall,
            gpu.kernel_seconds, cpu9 / wall);
        fflush(stdout);
    }
    return 0;
}
} // namespace

int main(int argc, char **argv) {
    @autoreleasepool {
        try { return benchmark(argc, argv); }
        catch (const std::exception &error) {
            fprintf(stderr, "seeding_benchmark: %s\n", error.what());
            return 1;
        }
    }
}
