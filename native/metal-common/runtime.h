#pragma once

#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <cstddef>
#include <mutex>
#include <string>
#include <vector>

namespace wgse::metal {

// Objective-C++ interface: independent of either aligner's index/read formats.
class Runtime {
public:
    Runtime();
    id<MTLDevice> device() const { return device_; }
    // For callers that encode their own command buffers.
    id<MTLCommandQueue> queue() const { return queue_; }
    // The first failure, if any; threads may use the runtime concurrently.
    std::string failure() const;
    id<MTLComputePipelineState> compile(const char *source, const char *entry);
    // For kernels specialised by function constants: one library, several pipelines.
    id<MTLLibrary> library(const char *source);
    id<MTLComputePipelineState> pipeline(id<MTLLibrary> library, const char *entry,
        MTLFunctionConstantValues *constants = nil);
    id<MTLBuffer> allocate(size_t bytes);
    // Page-aligned storage owned by the buffer; its contents are CPU/GPU shared.
    id<MTLBuffer> allocate_pages(size_t bytes);
    // Commits command once fewer than gpu_slots command buffers of this process
    // are running. Every command buffer goes through here: with more running at
    // once, macOS aborts some (ImpactingInteractivity) to keep the display responsive.
    //
    // GPU watchdog: once a command has run on the GPU for longer than
    // watchdog_seconds() with no word from macOS (as on a hung paravirtual GPU),
    // the runtime gives up on the GPU for the rest of the process. done then runs
    // for every command in flight, whose status is not Completed, and later
    // commits do not commit: done runs at once. So done always runs exactly once,
    // and callers waiting for it finish their work on the CPU. Every GPU command
    // must go through here (or run()) to be covered.
    // keep: buffers a command made with unretained references uses; they stay
    // alive until Metal completes the command, even after it is abandoned.
    static void commit(id<MTLCommandBuffer> command,
        void (^done)(id<MTLCommandBuffer>) = nil, NSArray *keep = nil);
    // Commits command and blocks until its done would run (completion or watchdog).
    static void run(id<MTLCommandBuffer> command);
    // Why the watchdog gave up on the GPU, or empty. describe() also describes a
    // command that did not complete for another reason.
    static std::string hang();
    static std::string describe(id<MTLCommandBuffer> command);
    // The time a command may run since macOS scheduled it: 180 s until one has
    // completed, then 60 s or 20 times the slowest so far, if longer. Uptime, so
    // system sleep does not count.
    static double watchdog_seconds();
    // Abandoned commands that may still run on the GPU. Host memory they read
    // (an index wrapped without copying) must not be freed while this is nonzero.
    static size_t unsettled();
    // Watchdog threads started so far: one per stretch of GPU work, not per command.
    static size_t watchdog_threads();
    // Test hook: withholds the first command whose label starts with label
    // (any command if empty), so it never completes, and gives up after seconds.
    static void inject_hang(const std::string &label, double seconds);
    // Whether macOS aborted command, the attempt'th commit of its work, to keep
    // the display responsive (ImpactingInteractivity), for example while another
    // process uses the GPU. Its kernels ran partly or not at all; committing them
    // again is safe when they only write outputs derived from unchanged inputs.
    // Counts the aborts.
    static bool interrupted(id<MTLCommandBuffer> command, int attempt);
    static size_t interruptions();
    // Commits of one piece of work, including those after interruptions.
    static constexpr int attempts = 4;
    // Test hook: report the first commit of every nth piece of work as
    // interrupted even if it completed, so its kernels run twice.
    static void inject_interruptions(size_t every);
    bool dispatch(id<MTLComputePipelineState> pipeline,
        const std::vector<id<MTLBuffer>> &buffers, size_t count, size_t group_size,
        double &gpu_seconds);

private:
    id<MTLDevice> device_ = nil;
    id<MTLCommandQueue> queue_ = nil;
    mutable std::mutex mutex_;
    std::string failure_;
    bool failed() const { return !failure().empty(); }
    void fail(const std::string &reason);
};

} // namespace wgse::metal
