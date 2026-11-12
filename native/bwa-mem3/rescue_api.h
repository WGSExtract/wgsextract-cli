#pragma once

// The wrapper's controls for GPU mate rescue (rescue_gpu.h has the aligner's side).
// Rescues mates on the GPU under --cuda (--gpu-rescue on, the default), where the build has it.
void wgse_mem3_rescue_set_enabled(bool enabled);
// Test hook (--gpu-inject-rescue-failure): GPU mate-rescue batches fail.
void wgse_mem3_rescue_inject_failure();
// Prints the GPU's share of mate rescue; true if a GPU batch failed (the CPU did the rest).
bool wgse_mem3_rescue_finish();
