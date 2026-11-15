// The GPU that collects SMEMs for seeding_host.cpp's scheduling: Metal
// (metal_seeding_device.mm) or CUDA (cuda_seeding.cu), running wgse-bwa-mem2's seeding
// kernel (seeding.metal, seeding.cuh).
#pragma once

#include "seeding_types.h"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>

class SeedingDevice {
public:
    virtual ~SeedingDevice() = default;
    // Staging for a batch's 2-bit bases and its reads, valid until the next call.
    virtual uint8_t *bases(size_t count) = 0;
    virtual SeedRead *reads(size_t count) = 0;
    // Collects the SMEMs of p.reads staged reads, waiting for them; returns the GPU's seconds
    // and throws on a failure. Each read's results are then counts()[i] and its SMEMs
    // smems()[i * p.capacity...].
    virtual double collect(const SeedParameters &p, size_t base_count) = 0;
    virtual const SeedCount *counts() const = 0;
    virtual const SeedSmem *smems() const = 0;
    // Why the GPU may not be used again (its watchdog gave up), or empty.
    virtual std::string hang() const = 0;

    // Suffix-array lookups (--gpu-sa on): uploads FMI_search's sampled suffix array, `entries`
    // of sa_ms_byte and sa_ls_word, one for every 2^shift rows, next to the occurrence table;
    // count is FMI_search's count[5]. False, with the reason, if the GPU has too little free
    // memory or no kernel for it.
    virtual bool enable_sa(const int8_t *, const uint32_t *, size_t, int, const int64_t *,
        std::string &reason) {
        reason = "no GPU kernel for suffix-array lookups on this backend";
        return false;
    }
    // The suffix-array values of rows[0, count), as FMI_search::get_sa_entry_compressed walks
    // them (the sentinel row's walk reports 0 if drop_sentinel_offset), into
    // coordinates[slots[i]] (coordinates[i] if slots is null). Any thread may call it once
    // enable_sa succeeded; returns the GPU's seconds and throws on a failure.
    virtual double resolve_sa(const int64_t *, const int64_t *, size_t, int64_t *, bool) {
        throw std::runtime_error("no GPU suffix-array lookups");
    }
};

// A batch's limits: reads, bases per read (the scratch entries per read too) and SMEMs per
// read. A device may allocate for them up front.
struct SeedingLimits {
    size_t reads, read_length, capacity;
};

// The GPU seeder for FMI_search's occurrence table (cp_occ, `bytes`); throws with the reason
// when there is none.
std::unique_ptr<SeedingDevice> make_seeding_device(const void *checkpoints, size_t bytes,
    const SeedingLimits &limits);
// Whether an abandoned GPU command may still read the occurrence table in place, so that it
// must not be freed.
bool seeding_device_unsettled();
