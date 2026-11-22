// SPDX-License-Identifier: MIT
#include <cuda_runtime.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <map>
#include <mutex>
#include <new>
#include <tuple>
#include <vector>

#include "Context.h"
#include "cuda_pairhmm.h"

namespace {

constexpr size_t reserve_bytes = size_t(256) << 20;
constexpr size_t max_input_bytes = size_t(128) << 20;
constexpr int max_read_length = 1024;
constexpr int batch_size = 4096;

struct Row {
    float mm, gap_m, mx, my, extend, match, mismatch;
    unsigned char base;
};

struct Job {
    int read_offset, hap_offset, read_length, hap_length;
};

// Anti-diagonals keep the GKL per-cell operation order. The terminal row sums
// M and X separately, in increasing haplotype-column order, just as GKL does.
__global__ void pairhmm(const Row* reads, const unsigned char* haplotypes,
                       const Job* jobs, float* results, int stride) {
    const Job job = jobs[blockIdx.x];
    const Row* rows = reads + job.read_offset;
    const unsigned char* hap = haplotypes + job.hap_offset;
    extern __shared__ float state[];
    for (int i = threadIdx.x; i < 9 * stride; i += blockDim.x) state[i] = 0;
    __syncthreads();
    float sum_m = 0, sum_x = 0;
    const float initial = __fdiv_rn(0x1p120f, float(job.hap_length));
    for (int diagonal = 0; diagonal <= job.read_length + job.hap_length; ++diagonal) {
        float* current = state + (diagonal % 3) * 3 * stride;
        const float* previous = state + ((diagonal + 2) % 3) * 3 * stride;
        const float* before = state + ((diagonal + 1) % 3) * 3 * stride;
        for (int r = threadIdx.x; r <= job.read_length; r += blockDim.x) {
            const int c = diagonal - r;
            float m = 0, x = 0, y = 0;
            if (r == 0 && c >= 0 && c <= job.hap_length) {
                y = initial;
            } else if (r > 0 && c > 0 && c <= job.hap_length) {
                const Row row = rows[r - 1];
                const float emission = row.base == hap[c - 1] || row.base == 4 ||
                                               hap[c - 1] == 4
                                           ? row.match : row.mismatch;
                m = ((before[r - 1] * row.mm +
                      before[stride + r - 1] * row.gap_m) +
                     before[2 * stride + r - 1] * row.gap_m) * emission;
                x = previous[r - 1] * row.mx +
                    previous[stride + r - 1] * row.extend;
                y = previous[r] * row.my + previous[2 * stride + r] * row.extend;
                if (r == job.read_length) {
                    sum_m += m;
                    sum_x += x;
                }
            }
            current[r] = m;
            current[stride + r] = x;
            current[2 * stride + r] = y;
        }
        __syncthreads();
    }
    // Exactly one thread owns the terminal row, including when rows are strided.
    if (threadIdx.x == job.read_length % blockDim.x)
        results[blockIdx.x] = sum_m + sum_x;
}

template<class T> struct Buffer {
    T* pointer = nullptr;
    size_t capacity = 0;
    ~Buffer() { if (pointer) cudaFree(pointer); }
    bool resize(size_t count) {
        if (count <= capacity) return true;
        if (pointer) cudaFree(pointer);
        pointer = nullptr;
        capacity = 0;
        if (cudaMalloc(reinterpret_cast<void**>(&pointer), count * sizeof(T)) != cudaSuccess)
            return false;
        capacity = count;
        return true;
    }
};

class Device {
    Buffer<Row> rows_;
    Buffer<unsigned char> haplotypes_;
    Buffer<Job> jobs_;
    Buffer<float> results_;
    int ordinal_ = -1;
    bool disabled_ = false;
    unsigned long long gpu_pairs_ = 0;

    bool fail(const char* reason) {
        std::fprintf(stderr, "WGSE CUDA PairHMM: %s; using GKL CPU fallback.\n", reason);
        disabled_ = true;
        (void)cudaGetLastError();
        return false;
    }

public:
    ~Device() {
        std::fprintf(stderr, "WGSE CUDA PairHMM: completed %llu GPU read/haplotype pairs.\n",
                     gpu_pairs_);
    }

    bool compute(const std::vector<Row>& rows, const std::vector<unsigned char>& haps,
                 const std::vector<Job>& jobs, std::vector<float>& results, int stride) {
        if (disabled_) return false;
        size_t free = 0, total = 0;
        if (ordinal_ < 0) {
            int count = 0;
            if (cudaGetDeviceCount(&count) != cudaSuccess || !count)
                return fail("no usable NVIDIA CUDA device/driver");
            size_t best = 0;
            for (int device = 0; device < count; ++device) {
                cudaFuncAttributes attributes{};
                if (cudaSetDevice(device) == cudaSuccess &&
                    cudaFuncGetAttributes(&attributes, pairhmm) == cudaSuccess &&
                    cudaMemGetInfo(&free, &total) == cudaSuccess && free > best) {
                    ordinal_ = device;
                    best = free;
                }
                (void)cudaGetLastError();
            }
            if (ordinal_ < 0) return fail("no device with compatible CUDA kernels");
        }
        if (cudaSetDevice(ordinal_) != cudaSuccess ||
            cudaMemGetInfo(&free, &total) != cudaSuccess)
            return fail("CUDA device unavailable");
        const size_t bytes = rows.size() * sizeof(Row) + haps.size() +
                             batch_size * (sizeof(Job) + sizeof(float));
        if (free < bytes + reserve_bytes)
            return fail("insufficient free GPU memory (includes a 256 MiB reserve)");
        if (!rows_.resize(rows.size()) || !haplotypes_.resize(haps.size()) ||
            !jobs_.resize(batch_size) || !results_.resize(batch_size))
            return fail("CUDA allocation failed");
        if (cudaMemcpy(rows_.pointer, rows.data(), rows.size() * sizeof(Row),
                       cudaMemcpyHostToDevice) != cudaSuccess ||
            cudaMemcpy(haplotypes_.pointer, haps.data(), haps.size(),
                       cudaMemcpyHostToDevice) != cudaSuccess)
            return fail("CUDA input transfer failed");
        results.resize(jobs.size());
        for (size_t offset = 0; offset < jobs.size(); offset += batch_size) {
            const int count = int(std::min(size_t(batch_size), jobs.size() - offset));
            if (cudaMemcpy(jobs_.pointer, jobs.data() + offset, count * sizeof(Job),
                           cudaMemcpyHostToDevice) != cudaSuccess)
                return fail("CUDA descriptor transfer failed");
            pairhmm<<<count, 128, 9 * stride * sizeof(float)>>>(
                rows_.pointer, haplotypes_.pointer, jobs_.pointer, results_.pointer, stride);
            if (cudaGetLastError() != cudaSuccess ||
                cudaMemcpy(results.data() + offset, results_.pointer, count * sizeof(float),
                           cudaMemcpyDeviceToHost) != cudaSuccess)
                return fail("CUDA kernel or result transfer failed");
        }
        for (float result : results)
            if (!std::isfinite(result) || result < 0)
                return fail("CUDA returned an invalid probability");
        gpu_pairs_ += jobs.size();
        return true;
    }
};

} // namespace

bool wgse_cuda_pairhmm(const std::vector<testcase>& cases, std::vector<float>& results) try {
    static std::mutex mutex;
    static Device device;
    std::lock_guard<std::mutex> lock(mutex);
    if (cases.empty()) return false;
    Context<float> context;
    std::vector<Row> rows;
    std::vector<unsigned char> haps;
    std::vector<Job> jobs;
    using ReadKey = std::tuple<const char*, const char*, const char*, const char*, const char*>;
    std::map<ReadKey, int> read_offsets;
    std::map<const char*, int> hap_offsets;
    int stride = 0;
    for (const testcase& tc : cases) {
        if (tc.rslen <= 0 || tc.rslen > max_read_length ||
            tc.haplen <= 0 || tc.haplen > 65536) return false;
        const ReadKey key{tc.rs, tc.q, tc.i, tc.d, tc.c};
        auto read = read_offsets.find(key);
        if (read == read_offsets.end()) {
            const int offset = int(rows.size());
            for (int r = 0; r < tc.rslen; ++r) {
                const int i = tc.i[r] & 127, d = tc.d[r] & 127, c = tc.c[r] & 127;
                const float error = context.ph2pr[tc.q[r] & 127];
                rows.push_back({context.set_mm_prob(i, d), 1.0f - context.ph2pr[c],
                                context.ph2pr[i], context.ph2pr[d], context.ph2pr[c],
                                1.0f - error, error / 3.0f, ConvertChar::get(tc.rs[r])});
            }
            read = read_offsets.emplace(key, offset).first;
        }
        auto hap = hap_offsets.find(tc.hap);
        if (hap == hap_offsets.end()) {
            const int offset = int(haps.size());
            for (int h = 0; h < tc.haplen; ++h)
                haps.push_back(ConvertChar::get(tc.hap[h]));
            hap = hap_offsets.emplace(tc.hap, offset).first;
        }
        jobs.push_back({read->second, hap->second, tc.rslen, tc.haplen});
        stride = std::max(stride, tc.rslen + 1);
        if (rows.size() * sizeof(Row) + haps.size() + jobs.size() * sizeof(Job) >
            max_input_bytes) return false;
    }
    return device.compute(rows, haps, jobs, results, stride);
} catch (const std::bad_alloc&) {
    std::fprintf(stderr, "WGSE CUDA PairHMM: host packing allocation failed; using GKL CPU fallback.\n");
    return false;
}
