#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>

struct dnaSeqPair;

enum class Backend { automatic, cpu, metal };
enum class ExtensionKernel { row, striped, shared, tiled, adaptive };

bool wgse_metal_available(std::string &reason);
// False when the failure is missing or unsupported hardware rather than a
// broken backend (shader compilation, pipeline or queue creation).
bool wgse_metal_hardware_supported();
// True after a GPU execution failure that was recovered on the CPU.
bool wgse_metal_execution_failed(std::string &reason);
void wgse_set_backend(Backend backend);
// Whether the selected backend uses Metal.
bool wgse_metal_active();
// Whether the CPU threads take the exact replacements of upstream code (reference
// windows from the .pac instead of the .0123, pointer sorts, NEON unpacking,
// batched mate rescue), with either backend. Off only with the test option
// --cpu-upstream, which runs upstream's code for comparison.
bool wgse_exact_cpu();
void wgse_set_upstream_cpu(bool upstream);
void wgse_print_backend_metrics();
uint64_t wgse_gpu_execution_ns();
uint64_t wgse_gpu_dispatches();
uint64_t wgse_gpu_cells();
uint64_t wgse_gpu_issued_cells();
void wgse_set_kernel(ExtensionKernel kernel);
void wgse_set_routing(int32_t minimum_pairs, int32_t inflight_limit);
void wgse_set_aggregation(int32_t target, int32_t delay_us, int32_t queue_pairs);
void wgse_set_force_gpu(bool force);
void wgse_set_gpu8(bool enabled, int32_t target, int32_t minimum_pairs = 1,
    bool paired = true);
void wgse_set_pipelining(bool enabled);
// Whether the pipeline should map each chunk while the previous one pairs:
// with GPU mate rescue, pairing is mostly GPU work.
bool wgse_metal_overlap();
void wgse_set_overlap(bool enable);
// A chunk finished mapping while the previous one was pairing.
void wgse_note_overlapped_chunk();
// Page-aligned memory for the FM index checkpoints, so Metal can use them in place.
// With Metal, warns and leaves seeding to the CPU if the GPU cannot map them.
void *wgse_metal_index_alloc(size_t bytes);
// The default device's name and limits; empty and zero without a device.
struct WgseMetalLimits {
    std::string device;
    uint64_t max_buffer_bytes = 0, recommended_working_set_bytes = 0;
};
WgseMetalLimits wgse_metal_limits();
// Why GPU seeding cannot read an occurrence table of `bytes` in place, or "".
std::string wgse_gpu_seeding_limit(uint64_t bytes);
// The size of the index's occurrence table (.bwt.2bit.64); 0 if it cannot be read.
uint64_t wgse_occurrence_table_bytes(const std::string &prefix);
// Index files a run loads: all but the .0123, unless unpacked_reference.
uint64_t wgse_index_bytes(const std::string &prefix, bool unpacked_reference);
uint64_t wgse_physical_memory();
// Warns if physical memory is below the run's estimated peak resident memory.
void wgse_memory_preflight(const char *prefix, int64_t chunk_bases);
#ifdef WGSE_METAL_BENCHMARK_TOOLS
void wgse_set_max_buffer_bytes(uint64_t bytes);
void wgse_set_physical_memory(uint64_t bytes);
#endif
// GPU SMEM collection (enabled with Metal) and its test hooks.
void wgse_set_gpu_seed(bool enable);
void wgse_set_gpu_seed_batch(size_t reads);
// SMEMs per read on the GPU before the CPU collects the read instead.
void wgse_set_gpu_seed_capacity(size_t smems);
void wgse_inject_seed_failure();
// Also collect each GPU unit's SMEMs on the CPU and count differences.
void wgse_verify_gpu_seeds();
// GPU mate rescue (enabled with Metal) and its test hooks.
void wgse_set_gpu_rescue(bool enable);
// Whether CPU threads align a pair's rescue windows together (on by default).
void wgse_set_cpu_rescue_batching(bool enable);
void wgse_set_gpu_rescue_batch(size_t pairs);
void wgse_inject_rescue_failure();
// Also run each GPU rescue alignment on the CPU and count differences.
void wgse_verify_gpu_rescue();
// Report the first commit of every nth piece of GPU work as interrupted.
void wgse_inject_gpu_interruptions(size_t every);
struct WgseExtensionScratch {
    dnaSeqPair *pairs, *aux;
    int32_t *hist;
};
WgseExtensionScratch wgse_extension_scratch(
    const dnaSeqPair *pairs, int32_t count, int32_t histogram_size);
struct WgseExtensionWork {
    explicit WgseExtensionWork(std::function<void(bool)> work);
    ~WgseExtensionWork();
    void finish(bool independent = false);
    std::function<void(bool)> work;
    WgseExtensionWork *previous;
    bool done = false;
};
#ifdef WGSE_METAL_BENCHMARK_TOOLS
bool wgse_set_capture_directory(const std::string &path, std::string &reason);
#endif
// Records wall time of one upstream batch-extension call, whichever backend ran it.
struct WgseExtensionScope {
    explicit WgseExtensionScope(int32_t count);
    ~WgseExtensionScope();
    uint64_t start;
    int32_t count;
    uint64_t children = 0;
    WgseExtensionScope *parent;
};
bool wgse_metal_extend(dnaSeqPair *pairs, const uint8_t *reference,
    const uint8_t *query, int32_t count, int32_t bandwidth,
    int32_t open_deletion, int32_t extend_deletion, int32_t open_insertion,
    int32_t extend_insertion, int32_t zdrop, int32_t end_bonus,
    const int8_t *matrix, int32_t precision = 0);
