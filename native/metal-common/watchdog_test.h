#pragma once

namespace wgse::metal {

// Simulate failure to create the watchdog thread, not a failure on the GPU.
void inject_watchdog_start_failure();

} // namespace wgse::metal
