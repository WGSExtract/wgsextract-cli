// The CUDA device for wgse-bwa's GPU scheduling (backend.cpp). Unlike Metal on
// Apple Silicon, a discrete GPU has its own memory: the index is uploaded once,
// and each batch's inputs and results cross PCIe through pinned host buffers.
#include "extend.cuh"
#include "gpu_device.h"
#include "gpu_runtime.h"
#include "rescue.cuh"
#include "seeding.cuh"

#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <deque>
#include <mutex>
#include <thread>

namespace wgse::gpu {
namespace {
namespace sk = wgse::kernels::seeding;
namespace rk = wgse::kernels::rescue;
namespace ek = wgse::kernels::extend;

constexpr unsigned block_size = 128;

unsigned blocks(size_t threads) {
    return unsigned((threads + block_size - 1) / block_size);
}

// Compacts each read's intervals (at most seed_output_capacity, strided) into
// consecutive output at offsets[read].
__global__ void gather_seeds(const SeedInterval *__restrict__ output,
    const SeedResult *__restrict__ results, const uint64_t *__restrict__ offsets, uint32_t count,
    SeedInterval *compact) {
    const uint32_t read = blockIdx.x;
    if (read >= count || results[read].status != seed_ok) return;
    const SeedInterval *from = output + uint64_t(read) * seed_output_capacity;
    SeedInterval *to = compact + offsets[read];
    for (uint32_t i = threadIdx.x; i < results[read].count; i += blockDim.x) to[i] = from[i];
}

// Test hook: occupies the GPU for about `nanoseconds`, as a hung kernel would.
__global__ void spin(uint64_t nanoseconds) {
    const uint64_t start = clock64();
    // clock64 counts SM cycles; 2 per nanosecond overestimates no current GPU.
    while (uint64_t(clock64() - start) < nanoseconds * 2) __nanosleep(1000);
}

template <int T, int C>
void extend_launch(bool right, const uint8_t *bases, const ExtendJob *jobs, ExtendResult *results,
    const ExtendParameters &p, const uint32_t *order, wgse_gpu_stream stream) {
    ek::extend_side<T, C><<<blocks(size_t(p.count) * extend_lanes), block_size, 0, stream>>>(
        right, bases, jobs, results, p, order);
}

// extend_class_caps[k] = T * 16 * C, as extend.metal's instantiations.
void extend_class(size_t k, bool right, const uint8_t *bases, const ExtendJob *jobs,
    ExtendResult *results, const ExtendParameters &p, const uint32_t *order,
    wgse_gpu_stream stream) {
    switch (k) {
    case 0: extend_launch<1, 1>(right, bases, jobs, results, p, order, stream); break;
    case 1: extend_launch<1, 2>(right, bases, jobs, results, p, order, stream); break;
    case 2: extend_launch<1, 3>(right, bases, jobs, results, p, order, stream); break;
    case 3: extend_launch<1, 4>(right, bases, jobs, results, p, order, stream); break;
    case 4: extend_launch<1, 5>(right, bases, jobs, results, p, order, stream); break;
    case 5: extend_launch<1, 6>(right, bases, jobs, results, p, order, stream); break;
    case 6: extend_launch<1, 7>(right, bases, jobs, results, p, order, stream); break;
    case 7: extend_launch<1, 8>(right, bases, jobs, results, p, order, stream); break;
    case 8: extend_launch<4, 4>(right, bases, jobs, results, p, order, stream); break;
    default: throw std::logic_error("extension class");
    }
}
static_assert(extend_classes == 9 && extend_class_caps[7] == 128 && extend_class_caps[8] == 256);

template <int S>
void rescue_launch(const uint8_t *bases, const RescueJob *jobs, uint8_t *rows,
    RescueResult *results, const RescueParameters &p, const uint32_t *order,
    wgse_gpu_stream stream) {
    rk::rescue_align<S><<<blocks(size_t(p.count) * rescue_lanes), block_size, 0, stream>>>(
        bases, jobs, rows, results, p, order);
}

template <int S = 1>
void rescue_class(int segments, const uint8_t *bases, const RescueJob *jobs, uint8_t *rows,
    RescueResult *results, const RescueParameters &p, const uint32_t *order,
    wgse_gpu_stream stream) {
    if constexpr (S <= int(rescue_max_segments)) {
        if (segments == S) rescue_launch<S>(bases, jobs, rows, results, p, order, stream);
        else rescue_class<S + 1>(segments, bases, jobs, rows, results, p, order, stream);
    } else {
        throw std::logic_error("rescue class");
    }
}

// A stream and an event marking the end of the work queued on it.
struct Queue {
    wgse_gpu_stream stream = nullptr;
    wgse_gpu_event done = nullptr;

    Queue() {
        check(WGSE_GPU(StreamCreateWithFlags)(&stream, WGSE_GPU(StreamNonBlocking)), "stream");
        check(WGSE_GPU(EventCreateWithFlags)(&done, WGSE_GPU(EventDisableTiming)), "event");
    }
    ~Queue() {
        if (gpu_hung()) return;
        if (done) (void)WGSE_GPU(EventDestroy)(done);
        if (stream) (void)WGSE_GPU(StreamDestroy)(stream);
    }
    Queue(const Queue &) = delete;
    Queue &operator=(const Queue &) = delete;

    void finish(Watchdog &watchdog, Watchdog::Stage stage, const char *what) {
        check(WGSE_GPU(GetLastError)(), what);
        check(WGSE_GPU(EventRecord)(done, stream), what);
        watchdog.wait(done, stage, what);
    }
};

struct CudaExtendBuffers : ExtendBuffers {
    PinnedBuffer<uint8_t> host_bases;
    PinnedBuffer<ExtendJob> host_jobs;
    PinnedBuffer<ExtendResult> host_results;
    PinnedBuffer<uint32_t> host_order;
    DeviceBuffer<uint8_t> device_bases;
    DeviceBuffer<ExtendJob> device_jobs;
    DeviceBuffer<ExtendResult> device_results;
    DeviceBuffer<uint32_t> device_order;
    Queue queue;
};

class CudaDevice final : public Device {
public:
    CudaDevice(int ordinal, const DeviceOptions &options)
        : ordinal_(ordinal), options_(options), watchdog_(options.watchdog_seconds) {
        select();
        completer_ = std::thread(&CudaDevice::complete_loop, this);
    }

    ~CudaDevice() override {
        {
            std::lock_guard<std::mutex> lock(completion_mutex_);
            stopping_ = true;
        }
        completion_changed_.notify_all();
        completer_.join();
    }

    const char *name() const override { return "CUDA"; }

    size_t seed_capacity() const override { return options_.seed_batch; }

    void attach(const bwt_t &bwt, const mem_opt_t &opt) override {
        select();
        if (attached_bwt_ != bwt.bwt) {
            attached_bwt_ = nullptr;
            const size_t capacity = options_.seed_batch;
            reserve_memory(bwt.bwt_size * sizeof(uint32_t)
                + capacity * (seed_scratch_capacity * 3 + seed_output_capacity) * sizeof(SeedInterval),
                "index");
            index_.reserve(bwt.bwt_size);
                upload(index_.data(), bwt.bwt, bwt.bwt_size * sizeof(uint32_t), "index upload");
            scratch_.reserve(capacity * seed_scratch_capacity * 3);
            output_.reserve(capacity * seed_output_capacity);
            results_.reserve(capacity);
            host_results_.reserve(capacity);
            reads_.reserve(capacity);
            host_reads_.reserve(capacity);
            offsets_.reserve(capacity);
            host_offsets_.reserve(capacity);
            attached_bwt_ = bwt.bwt;
        }
        SeedParameters &p = seed_parameters_;
        p = {};
        p.primary = bwt.primary;
        std::copy_n(bwt.L2, 5, p.l2);
        p.min_seed_len = opt.min_seed_len;
        p.split_len = static_cast<int>(opt.min_seed_len * opt.split_factor + .499);
        p.split_width = opt.split_width;
        p.max_mem_intv = static_cast<int>(opt.max_mem_intv);
        p.scratch_limit = seed_scratch_capacity;
        p.output_limit = seed_output_capacity;
    }

    void seed(const Reads &input, std::vector<Intervals> &out, std::vector<long> &failed) override {
        select();
        failed.clear();
        out.resize(input.reads.size());
        for (size_t offset = 0; offset < input.reads.size(); offset += options_.seed_batch) {
            const size_t count = std::min(options_.seed_batch, input.reads.size() - offset);
            const size_t start = input.reads[offset].offset;
            const SeedRead &last = input.reads[offset + count - 1];
            const size_t bytes = std::max<size_t>(1, size_t(last.offset) + last.length - start);
            host_bases_.reserve(bytes);
            bases_.reserve(bytes);
            memcpy(host_bases_.data(), input.bases.data() + start, bytes);
            for (size_t i = 0; i < count; ++i) {
                host_reads_.data()[i] = input.reads[offset + i];
                host_reads_.data()[i].offset -= uint32_t(start);
            }
            SeedParameters p = seed_parameters_;
            p.count = uint32_t(count);
            const auto stream = seed_queue_.stream;
            check(WGSE_GPU(MemcpyAsync)(bases_.data(), host_bases_.data(), bytes,
                WGSE_GPU(MemcpyHostToDevice), stream), "seeding upload");
            check(WGSE_GPU(MemcpyAsync)(reads_.data(), host_reads_.data(), count * sizeof(SeedRead),
                WGSE_GPU(MemcpyHostToDevice), stream), "seeding upload");
            sk::collect_seeds<<<blocks(count), block_size, 0, stream>>>(index_.data(), p,
                bases_.data(), reads_.data(), scratch_.data(), output_.data(), results_.data());
            check(WGSE_GPU(MemcpyAsync)(host_results_.data(), results_.data(),
                count * sizeof(SeedResult), WGSE_GPU(MemcpyDeviceToHost), stream), "seeding");
            seed_queue_.finish(watchdog_, Watchdog::seeding, "seeding");
            // Each read's intervals, consecutive, in one copy.
            uint64_t total = 0;
            for (size_t i = 0; i < count; ++i) {
                const SeedResult &r = host_results_.data()[i];
                if (r.status > seed_output_overflow || (r.status == seed_ok
                    && r.count > seed_output_capacity))
                    throw std::runtime_error("invalid GPU seeding result");
                host_offsets_.data()[i] = total;
                if (r.status == seed_ok) total += r.count;
            }
            compact_.reserve(total);
            host_compact_.reserve(total);
            if (total) {
                check(WGSE_GPU(MemcpyAsync)(offsets_.data(), host_offsets_.data(),
                    count * sizeof(uint64_t), WGSE_GPU(MemcpyHostToDevice), stream), "seeding");
                gather_seeds<<<unsigned(count), 32, 0, stream>>>(output_.data(), results_.data(),
                    offsets_.data(), uint32_t(count), compact_.data());
                check(WGSE_GPU(MemcpyAsync)(host_compact_.data(), compact_.data(),
                    total * sizeof(SeedInterval), WGSE_GPU(MemcpyDeviceToHost), stream), "seeding");
                seed_queue_.finish(watchdog_, Watchdog::seeding, "seeding");
            }
            for (size_t i = 0; i < count; ++i) {
                const SeedResult &r = host_results_.data()[i];
                Intervals &destination = out[offset + i];
                if (r.status != seed_ok) {
                    failed.push_back(long(offset + i));
                    destination.clear();
                    continue;
                }
                const auto *from = reinterpret_cast<const bwtintv_t *>(host_compact_.data())
                    + host_offsets_.data()[i];
                destination.assign(from, from + r.count);
            }
        }
    }

    bool attach_sa(const bwt_t &bwt) override {
        select();
        sa_ready_ = false;
        dense_low_.release();
        dense_high_.release();
        if (!bwt.sa || bwt.sa_intv <= 0 || (bwt.sa_intv & (bwt.sa_intv - 1))
            || bwt.n_sa != (bwt.seq_len + bwt.sa_intv) / bwt.sa_intv) return false;
        const size_t rows = options_.locate_rows;
        try {
            reserve_memory(bwt.n_sa * sizeof(uint64_t) + 2 * rows * sizeof(uint64_t), "suffix array");
        } catch (const std::exception &error) {
            fprintf(stderr, "[wgse-bwa] the CPU locates seeds: %s\n", error.what());
            return false;
        }
        sa_.reserve(bwt.n_sa);
        upload(sa_.data(), bwt.sa, bwt.n_sa * sizeof(uint64_t), "suffix array upload");
        rows_.reserve(rows);
        positions_.reserve(rows);
        host_rows_.reserve(rows);
        host_positions_.reserve(rows);
        LocateParameters &p = locate_parameters_;
        p = {};
        p.primary = bwt.primary;
        p.seq_len = bwt.seq_len;
        p.sa_intv = static_cast<uint64_t>(bwt.sa_intv);
        std::copy_n(bwt.L2, 5, p.l2);
        p.sa_shift = static_cast<uint32_t>(__builtin_ctzll(p.sa_intv));
        sa_ready_ = true;
        return true;
    }

    void locate(const std::vector<uint64_t> &input, std::vector<uint64_t> &output) override {
        select();
        output.resize(input.size());
        const size_t capacity = options_.locate_rows;
        for (size_t offset = 0; offset < input.size(); offset += capacity) {
            const size_t count = std::min(capacity, input.size() - offset);
            memcpy(host_rows_.data(), input.data() + offset, count * sizeof(uint64_t));
            LocateParameters p = locate_parameters_;
            p.count = uint32_t(count);
            const auto stream = seed_queue_.stream;
            check(WGSE_GPU(MemcpyAsync)(rows_.data(), host_rows_.data(), count * sizeof(uint64_t),
                WGSE_GPU(MemcpyHostToDevice), stream), "locate upload");
            if (dense_low_.data())
                sk::locate_rows_dense<<<blocks(count), block_size, 0, stream>>>(index_.data(),
                    sa_.data(), p, rows_.data(), positions_.data(), dense_low_.data(),
                    dense_high_.data());
            else
                sk::locate_rows<<<blocks(count), block_size, 0, stream>>>(index_.data(),
                    sa_.data(), p, rows_.data(), positions_.data());
            check(WGSE_GPU(MemcpyAsync)(host_positions_.data(), positions_.data(),
                count * sizeof(uint64_t), WGSE_GPU(MemcpyDeviceToHost), stream), "locate");
            seed_queue_.finish(watchdog_, Watchdog::locate, "locate");
            memcpy(output.data() + offset, host_positions_.data(), count * sizeof(uint64_t));
        }
    }

    bool densify(unsigned shift, size_t slice, const uint32_t **low,
        const uint32_t **high) override {
        select();
        LocateParameters &p = locate_parameters_;
        if (!sa_ready_ || shift == 0 || (uint64_t(1) << shift) >= p.sa_intv || p.seq_len >> 33
            || slice == 0 || slice % 32) return false;
        const uint64_t entries = (p.seq_len / p.sa_intv + 1) * ((p.sa_intv >> shift) - 1);
        const uint64_t words = (entries + 31) / 32;
        if (entries > UINT32_MAX) return false;
        try {
            reserve_memory((entries + words) * sizeof(uint32_t), "dense suffix-array samples");
            dense_low_.reserve(entries);
            dense_high_.reserve(words);
            host_dense_low_.resize(entries);
            host_dense_high_.resize(words);
        } catch (const std::exception &) {
            dense_low_.release();
            dense_high_.release();
            return false;
        }
        p.dense_shift = shift;
        p.dense_entries = entries;
        for (uint64_t first = 0; first < entries; first += slice) {
            LocateParameters current = p;
            current.dense_offset = uint32_t(first);
            current.count = uint32_t(std::min<uint64_t>(slice, entries - first));
            sk::locate_dense_build<<<blocks((current.count + 31) / 32 * 32), block_size, 0,
                seed_queue_.stream>>>(index_.data(), sa_.data(), current, dense_low_.data(),
                dense_high_.data());
            seed_queue_.finish(watchdog_, Watchdog::locate, "dense samples");
        }
        download(host_dense_low_.data(), dense_low_.data(), entries * sizeof(uint32_t),
            "dense samples");
        download(host_dense_high_.data(), dense_high_.data(), words * sizeof(uint32_t),
            "dense samples");
        *low = host_dense_low_.data();
        *high = host_dense_high_.data();
        return true;
    }

    void rescue(const RescueParameters &p, const std::vector<uint8_t> &bases,
        const std::vector<RescueJob> &jobs, size_t row_bytes,
        std::vector<RescueResult> &results) override {
        select();
        results.resize(jobs.size());
        if (jobs.empty()) return;
        const auto start_time = Clock::now();
        rescue_host_bases_.reserve(bases.size());
        rescue_bases_.reserve(bases.size());
        rescue_host_jobs_.reserve(jobs.size());
        rescue_jobs_.reserve(jobs.size());
        rescue_rows_.reserve(row_bytes);
        rescue_results_.reserve(jobs.size());
        rescue_host_results_.reserve(jobs.size());
        rescue_host_order_.reserve(jobs.size());
        rescue_order_.reserve(jobs.size());
        memcpy(rescue_host_bases_.data(), bases.data(), bases.size());
        memcpy(rescue_host_jobs_.data(), jobs.data(), jobs.size() * sizeof(RescueJob));
        // Jobs grouped by segment count, as rescue_engine.h does.
        size_t start[rescue_max_segments + 2] = {};
        auto segments = [](const RescueJob &job) {
            return std::clamp<size_t>((job.qlen + rescue_lanes - 1) / rescue_lanes, 1,
                rescue_max_segments);
        };
        for (const RescueJob &job : jobs) ++start[segments(job) + 1];
        for (size_t s = 1; s <= rescue_max_segments + 1; ++s) start[s] += start[s - 1];
        size_t next[rescue_max_segments + 1];
        std::copy_n(start, rescue_max_segments + 1, next);
        for (size_t i = 0; i < jobs.size(); ++i)
            rescue_host_order_.data()[next[segments(jobs[i])]++] = uint32_t(i);
        const auto stream = rescue_queue_.stream;
        check(WGSE_GPU(MemcpyAsync)(rescue_bases_.data(), rescue_host_bases_.data(), bases.size(),
            WGSE_GPU(MemcpyHostToDevice), stream), "rescue upload");
        check(WGSE_GPU(MemcpyAsync)(rescue_jobs_.data(), rescue_host_jobs_.data(),
            jobs.size() * sizeof(RescueJob), WGSE_GPU(MemcpyHostToDevice), stream), "rescue upload");
        check(WGSE_GPU(MemcpyAsync)(rescue_order_.data(), rescue_host_order_.data(),
            jobs.size() * sizeof(uint32_t), WGSE_GPU(MemcpyHostToDevice), stream), "rescue upload");
        if (options_.inject_hang) {
            options_.inject_hang = false;
            spin<<<1, 1, 0, stream>>>(uint64_t(watchdog_.limit(Watchdog::rescue) * 2e9));
        }
        for (size_t s = 1; s <= rescue_max_segments; ++s) {
            const size_t count = start[s + 1] - start[s];
            if (count == 0) continue;
            RescueParameters current = p;
            current.count = uint32_t(count);
            rescue_class(int(s), rescue_bases_.data(), rescue_jobs_.data(), rescue_rows_.data(),
                rescue_results_.data(), current, rescue_order_.data() + start[s], stream);
        }
        check(WGSE_GPU(MemcpyAsync)(rescue_host_results_.data(), rescue_results_.data(),
            jobs.size() * sizeof(RescueResult), WGSE_GPU(MemcpyDeviceToHost), stream), "rescue");
        rescue_queue_.finish(watchdog_, Watchdog::rescue, "mate rescue");
        memcpy(results.data(), rescue_host_results_.data(), jobs.size() * sizeof(RescueResult));
        rescue_seconds_ += seconds(start_time);
    }

    double rescue_kernel_seconds() const override { return rescue_seconds_; }

    std::unique_ptr<ExtendBuffers> extend_buffers(size_t bases, size_t jobs) override {
        select();
        auto buffers = std::make_unique<CudaExtendBuffers>();
        buffers->host_bases.reserve(bases);
        buffers->host_jobs.reserve(jobs);
        buffers->host_results.reserve(jobs);
        buffers->host_order.reserve(2 * jobs);
        buffers->device_bases.reserve(bases);
        buffers->device_jobs.reserve(jobs);
        buffers->device_results.reserve(jobs);
        buffers->device_order.reserve(2 * jobs);
        buffers->bases = buffers->host_bases.data();
        buffers->jobs = buffers->host_jobs.data();
        buffers->results = buffers->host_results.data();
        buffers->base_capacity = bases;
        buffers->job_capacity = jobs;
        return buffers;
    }

    void extend(const ExtendParameters &p, ExtendBuffers &base, size_t count, int,
        ExtendDone done) override {
        select();
        auto &b = static_cast<CudaExtendBuffers &>(base);
        uint32_t *order = b.host_order.data();
        const size_t capacity = b.job_capacity;
        const size_t sides[2] = {order_side(b.jobs, count, false, order),
            order_side(b.jobs, count, true, order + capacity)};
        // The batch's bases end at the last job's last base.
        size_t bytes = 0;
        for (size_t i = 0; i < count; ++i) {
            const ExtendJob &j = b.jobs[i];
            bytes = std::max<size_t>(bytes, std::max(size_t(j.left_target) + j.left_tlen,
                size_t(j.right_target) + j.right_tlen));
        }
        const auto stream = b.queue.stream;
        check(WGSE_GPU(MemcpyAsync)(b.device_bases.data(), b.bases, std::max<size_t>(bytes, 1),
            WGSE_GPU(MemcpyHostToDevice), stream), "extension upload");
        check(WGSE_GPU(MemcpyAsync)(b.device_jobs.data(), b.jobs, count * sizeof(ExtendJob),
            WGSE_GPU(MemcpyHostToDevice), stream), "extension upload");
        for (int side = 0; side < 2; ++side)
            if (sides[side])
                check(WGSE_GPU(MemcpyAsync)(b.device_order.data() + side * capacity,
                    order + side * capacity, sides[side] * sizeof(uint32_t),
                    WGSE_GPU(MemcpyHostToDevice), stream), "extension upload");
        // The right side reads the left side's results, as on Metal.
        for (int side = 0; side < 2; ++side) {
            const uint32_t *slots = order + side * capacity;
            for_each_class(b.jobs, slots, sides[side], side,
                [&](size_t k, size_t low, size_t high) {
                    ExtendParameters current = p;
                    current.count = uint32_t(high - low);
                    extend_class(k, side, b.device_bases.data(), b.device_jobs.data(),
                        b.device_results.data(), current,
                        b.device_order.data() + side * capacity + low, stream);
                });
        }
        check(WGSE_GPU(MemcpyAsync)(b.results, b.device_results.data(), count * sizeof(ExtendResult),
            WGSE_GPU(MemcpyDeviceToHost), stream), "extension");
        check(WGSE_GPU(GetLastError)(), "extension");
        check(WGSE_GPU(EventRecord)(b.queue.done, stream), "extension");
        std::lock_guard<std::mutex> lock(completion_mutex_);
        pending_.push_back({&b, Clock::now(), std::move(done)});
        completion_changed_.notify_one();
    }

private:
    struct Pending {
        CudaExtendBuffers *buffers;
        Clock::time_point submitted;
        ExtendDone done;
    };

    const int ordinal_;
    DeviceOptions options_;
    Watchdog watchdog_;
    // Seeding and locate run on the map stage's GPU thread, rescue on the pair
    // stage's; each has its own stream and buffers, so they overlap.
    Queue seed_queue_, rescue_queue_;
    const uint32_t *attached_bwt_ = nullptr;
    SeedParameters seed_parameters_{};
    DeviceBuffer<uint32_t> index_;
    DeviceBuffer<SeedInterval> scratch_, output_, compact_;
    DeviceBuffer<SeedResult> results_;
    DeviceBuffer<SeedRead> reads_;
    DeviceBuffer<uint8_t> bases_;
    DeviceBuffer<uint64_t> offsets_;
    PinnedBuffer<uint8_t> host_bases_;
    PinnedBuffer<SeedRead> host_reads_;
    PinnedBuffer<SeedResult> host_results_;
    PinnedBuffer<uint64_t> host_offsets_;
    PinnedBuffer<SeedInterval> host_compact_;
    bool sa_ready_ = false;
    LocateParameters locate_parameters_{};
    DeviceBuffer<uint64_t> sa_, rows_, positions_;
    PinnedBuffer<uint64_t> host_rows_, host_positions_;
    PinnedBuffer<uint8_t> bounce_;
    DeviceBuffer<uint32_t> dense_low_, dense_high_;
    std::vector<uint32_t> host_dense_low_, host_dense_high_;
    DeviceBuffer<uint8_t> rescue_bases_, rescue_rows_;
    DeviceBuffer<RescueJob> rescue_jobs_;
    DeviceBuffer<RescueResult> rescue_results_;
    DeviceBuffer<uint32_t> rescue_order_;
    PinnedBuffer<uint8_t> rescue_host_bases_;
    PinnedBuffer<RescueJob> rescue_host_jobs_;
    PinnedBuffer<RescueResult> rescue_host_results_;
    PinnedBuffer<uint32_t> rescue_host_order_;
    double rescue_seconds_ = 0;
    // Extension batches in submission order, completed by completer_.
    std::mutex completion_mutex_;
    std::condition_variable completion_changed_;
    std::deque<Pending> pending_;
    bool stopping_ = false;
    std::thread completer_;

    void select() const { check(WGSE_GPU(SetDevice)(ordinal_), "device selection"); }

    // Bulk copies (the index, the dense samples) through a pinned bounce buffer
    // in pieces, each an asynchronous copy with a watchdog-bounded wait: a
    // synchronous copy from pageable memory could block on a hung GPU.
    static constexpr size_t bounce_bytes = size_t(64) << 20;

    void upload(void *device, const void *host, size_t bytes, const char *what) {
        bounce_.reserve(bounce_bytes);
        for (size_t done = 0; done < bytes; done += bounce_bytes) {
            const size_t n = std::min(bounce_bytes, bytes - done);
            memcpy(bounce_.data(), static_cast<const uint8_t *>(host) + done, n);
            check(WGSE_GPU(MemcpyAsync)(static_cast<uint8_t *>(device) + done, bounce_.data(), n,
                WGSE_GPU(MemcpyHostToDevice), seed_queue_.stream), what);
            seed_queue_.finish(watchdog_, Watchdog::locate, what);
        }
    }

    void download(void *host, const void *device, size_t bytes, const char *what) {
        bounce_.reserve(bounce_bytes);
        for (size_t done = 0; done < bytes; done += bounce_bytes) {
            const size_t n = std::min(bounce_bytes, bytes - done);
            check(WGSE_GPU(MemcpyAsync)(bounce_.data(), static_cast<const uint8_t *>(device) + done,
                n, WGSE_GPU(MemcpyDeviceToHost), seed_queue_.stream), what);
            seed_queue_.finish(watchdog_, Watchdog::locate, what);
            memcpy(static_cast<uint8_t *>(host) + done, bounce_.data(), n);
        }
    }

    // Throws unless `bytes` more device memory are free, with a margin for the
    // other buffers and other programs: the GPU may be shared.
    void reserve_memory(size_t bytes, const char *what) const {
        size_t free = 0, total = 0;
        check(WGSE_GPU(MemGetInfo)(&free, &total), "memory query");
        constexpr size_t margin = size_t(1) << 30;
        if (bytes + margin > free)
            throw std::runtime_error(std::string("GPU memory: the ") + what + " needs "
                + std::to_string((bytes + margin) >> 20) + " MiB, "
                + std::to_string(free >> 20) + " MiB free");
    }

    void complete_loop() {
        std::unique_lock<std::mutex> lock(completion_mutex_);
        for (;;) {
            completion_changed_.wait(lock, [this] { return stopping_ || !pending_.empty(); });
            if (pending_.empty()) return;
            Pending next = std::move(pending_.front());
            pending_.pop_front();
            lock.unlock();
            Outcome outcome = Outcome::done;
            std::string error;
            try {
                select();
                watchdog_.wait(next.buffers->queue.done, Watchdog::extend, "seed extension");
            } catch (const std::exception &failure) {
                outcome = Outcome::failed;
                error = failure.what();
            }
            next.done(outcome, seconds(next.submitted), error);
            lock.lock();
        }
    }
};
} // namespace

std::unique_ptr<Device> make_cuda_device(const DeviceOptions &options, std::string &reason) {
    int count = 0;
    const wgse_gpu_error status = WGSE_GPU(GetDeviceCount)(&count);
    if (status != WGSE_GPU_SUCCESS || count == 0) {
        reason = status != WGSE_GPU_SUCCESS
            ? std::string("no usable NVIDIA GPU: ") + WGSE_GPU(GetErrorString)(status)
            : "no NVIDIA GPU";
        return nullptr;
    }
    // The device with the most free memory: the GPU may be shared.
    int best = -1;
    size_t best_free = 0;
    for (int ordinal = 0; ordinal < count; ++ordinal) {
        size_t free = 0, total = 0;
        if (WGSE_GPU(SetDevice)(ordinal) != WGSE_GPU_SUCCESS
            || WGSE_GPU(MemGetInfo)(&free, &total) != WGSE_GPU_SUCCESS) continue;
        if (best < 0 || free > best_free) best = ordinal, best_free = free;
    }
    if (best < 0) {
        reason = "no NVIDIA GPU answered";
        return nullptr;
    }
    try {
        return std::make_unique<CudaDevice>(best, options);
    } catch (const std::exception &error) {
        reason = error.what();
        return nullptr;
    }
}

bool cuda_hardware_supported() {
    int count = 0;
    return WGSE_GPU(GetDeviceCount)(&count) == WGSE_GPU_SUCCESS && count > 0;
}
} // namespace wgse::gpu
