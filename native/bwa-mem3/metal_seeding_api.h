#pragma once

// The wrapper's controls for GPU seeding (metal_seeding.h has the aligner's side).
// Seeds on the GPU under --metal (--gpu-seed on, the default).
void wgse_mem3_seed_set_enabled(bool enabled);
// Test hook (--gpu-verify-seeds): also seeds every unit on the CPU, uses the CPU's SMEMs
// and counts differences.
void wgse_mem3_seed_set_verify(bool verify);
// Test hook (--gpu-seed-wait): each chunk waits for its GPU batches before it maps, so the
// GPU's SMEMs are used even for small inputs.
void wgse_mem3_seed_set_wait(bool wait);
// Test hook (--gpu-inject-seed-failure): GPU seeding batches fail.
void wgse_mem3_seed_inject_failure();
// Prints the GPU's share of seeding; true if a GPU batch failed (and the CPU seeded the rest).
bool wgse_mem3_seed_finish();
