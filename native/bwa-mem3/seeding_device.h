// The GPU that collects SMEMs for seeding_host.cpp's scheduling: Metal
// (metal_seeding_device.mm) or CUDA (cuda_seeding.cu), running wgse-bwa-mem2's seeding
// kernel (seeding.metal, seeding.cuh).
#pragma once

#include "seeding_types.h"

#include <cstddef>
#include <cstdint>
#include <memory>
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
