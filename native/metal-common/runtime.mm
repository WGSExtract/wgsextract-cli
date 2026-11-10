#include "runtime.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <limits>
#include <memory>
#include <thread>
#include <unistd.h>
#include <unordered_map>

namespace wgse::metal {

namespace {
std::atomic<size_t> aborted{0}, first_commits{0}, injected_every{0};
constexpr long gpu_slots = 2;
dispatch_semaphore_t gpu_slot() {
    static dispatch_semaphore_t slot = dispatch_semaphore_create(gpu_slots);
    return slot;
}

using Clock = std::chrono::steady_clock;

// A committed command whose done has not run yet.
struct InFlight {
    id<MTLCommandBuffer> command;
    void (^done)(id<MTLCommandBuffer>);
    Clock::time_point committed;
    std::atomic<bool> finished{false};
    void finish() {
        if (!finished.exchange(true) && done) done(command);
    }
};

class Watchdog {
public:
    // Registers command; false (without registering) once the GPU is given up.
    bool track(const std::shared_ptr<InFlight> &entry) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!hang_.empty()) return false;
        if (!thread_.joinable()) {
            try {
                thread_ = std::thread(&Watchdog::watch, this);
                thread_.detach();
            } catch (const std::system_error &) {
                // Without the watchdog commands are unbounded, as before.
            }
        }
        entry->committed = Clock::now();
        in_flight_[(__bridge void *)entry->command] = entry;
        return true;
    }

    void completed(InFlight &entry) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            in_flight_.erase((__bridge void *)entry.command);
            slowest_ = std::max(slowest_,
                std::chrono::duration<double>(Clock::now() - entry.committed).count());
        }
        entry.finish();
    }

    std::string hang() {
        std::lock_guard<std::mutex> lock(mutex_);
        return hang_;
    }

    double limit() {
        std::lock_guard<std::mutex> lock(mutex_);
        return limit_locked();
    }

    void inject(const std::string &label, double seconds) {
        std::lock_guard<std::mutex> lock(mutex_);
        injected_label_ = label;
        injected_ = true;
        base_ = seconds;
        poll_ = std::min(poll_, seconds / 4);
    }

    // Whether to withhold command (the test hook); at most once.
    bool withhold(id<MTLCommandBuffer> command) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!injected_) return false;
        NSString *label = command.label ?: @"";
        if (![label hasPrefix:[NSString stringWithUTF8String:injected_label_.c_str()]])
            return false;
        injected_ = false;
        return true;
    }

private:
    std::mutex mutex_;
    std::thread thread_;
    std::unordered_map<void *, std::shared_ptr<InFlight>> in_flight_;
    std::string hang_, injected_label_;
    bool injected_ = false;
    double base_ = 60, slowest_ = 0, poll_ = 1;

    double limit_locked() const { return std::max(base_, 20 * slowest_); }

    void watch() {
        for (;;) {
            std::vector<std::shared_ptr<InFlight>> abandoned;
            double poll;
            {
                std::lock_guard<std::mutex> lock(mutex_);
                poll = poll_;
                const double limit = limit_locked();
                const auto now = Clock::now();
                for (const auto &[key, entry] : in_flight_) {
                    const double age =
                        std::chrono::duration<double>(now - entry->committed).count();
                    if (age <= limit) continue;
                    char reason[200];
                    snprintf(reason, sizeof reason,
                        "GPU watchdog: a Metal command (%s) did not finish in %.0f s; "
                        "the GPU is not used for the rest of the run",
                        entry->command.label.length ? entry->command.label.UTF8String
                                                    : "unlabelled", limit);
                    hang_ = reason;
                    break;
                }
                if (!hang_.empty()) {
                    // Commands queued behind a hung one cannot finish either.
                    for (auto &[key, entry] : in_flight_) abandoned.push_back(entry);
                    in_flight_.clear();
                }
            }
            if (!abandoned.empty()) {
                for (auto &entry : abandoned) entry->finish();
                return;
            }
            std::this_thread::sleep_for(std::chrono::duration<double>(poll));
        }
    }
};

Watchdog &watchdog() {
    static Watchdog *value = new Watchdog; // never destroyed: its thread is detached
    return *value;
}
} // namespace

void Runtime::commit(id<MTLCommandBuffer> command, void (^done)(id<MTLCommandBuffer>)) {
    Watchdog &dog = watchdog();
    auto entry = std::make_shared<InFlight>();
    entry->command = command;
    entry->done = done;
    dispatch_semaphore_t slot = gpu_slot();
    // A hung command keeps its slot; waiting for one is bounded by the watchdog.
    while (dispatch_semaphore_wait(slot, dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC / 10))) {
        if (!dog.hang().empty()) {
            entry->finish();
            return;
        }
    }
    if (!dog.track(entry)) {
        dispatch_semaphore_signal(slot);
        entry->finish();
        return;
    }
    if (dog.withhold(command)) return;
    [command addCompletedHandler:^(id<MTLCommandBuffer>) {
        dispatch_semaphore_signal(slot);
        watchdog().completed(*entry);
    }];
    [command commit];
}

void Runtime::run(id<MTLCommandBuffer> command) {
    dispatch_semaphore_t finished = dispatch_semaphore_create(0);
    commit(command, ^(id<MTLCommandBuffer>) { dispatch_semaphore_signal(finished); });
    dispatch_semaphore_wait(finished, DISPATCH_TIME_FOREVER);
}

std::string Runtime::hang() {
    return watchdog().hang();
}

std::string Runtime::describe(id<MTLCommandBuffer> command) {
    if (command.status == MTLCommandBufferStatusError && command.error)
        return command.error.localizedDescription.UTF8String;
    std::string reason = hang();
    return reason.empty() ? "Metal command failed" : reason;
}

double Runtime::watchdog_seconds() {
    return watchdog().limit();
}

void Runtime::inject_hang(const std::string &label, double seconds) {
    watchdog().inject(label, seconds);
}

bool Runtime::interrupted(id<MTLCommandBuffer> command, int attempt) {
    const size_t every = injected_every.load(std::memory_order_relaxed);
    if (every && attempt == 1 && first_commits.fetch_add(1) % every == every - 1) {
        aborted.fetch_add(1, std::memory_order_relaxed);
        return true;
    }
    NSError *error = command.status == MTLCommandBufferStatusError ? command.error : nil;
    NSError *cause = error.userInfo[NSUnderlyingErrorKey];
    // kIOGPUCommandBufferCallbackErrorImpactingInteractivity
    if (![error.domain isEqualToString:MTLCommandBufferErrorDomain]
        || ![cause.domain isEqualToString:@"IOGPUCommandQueueErrorDomain"] || cause.code != 14)
        return false;
    aborted.fetch_add(1, std::memory_order_relaxed);
    return true;
}

void Runtime::inject_interruptions(size_t every) {
    injected_every = every;
}

size_t Runtime::interruptions() {
    return aborted.load(std::memory_order_relaxed);
}

std::string Runtime::failure() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return failure_;
}

void Runtime::fail(const std::string &reason) {
    std::lock_guard<std::mutex> lock(mutex_);
    failure_ = reason;
}

Runtime::Runtime() {
    device_ = MTLCreateSystemDefaultDevice();
    if (!device_ || !device_.hasUnifiedMemory) {
        fail("no unified-memory Metal device");
        device_ = nil;
        return;
    }
    queue_ = [device_ newCommandQueue];
    if (!queue_) fail("Metal command queue allocation failed");
}

id<MTLComputePipelineState> Runtime::compile(const char *source, const char *entry) {
    return pipeline(library(source), entry);
}

id<MTLLibrary> Runtime::library(const char *source) {
    if (failed()) return nil;
    NSError *error = nil;
    id<MTLLibrary> library = [device_ newLibraryWithSource:
        [NSString stringWithUTF8String:source] options:nil error:&error];
    if (!library) fail(error.localizedDescription.UTF8String);
    return library;
}

id<MTLComputePipelineState> Runtime::pipeline(id<MTLLibrary> library, const char *entry,
    MTLFunctionConstantValues *constants) {
    if (failed() || !library) return nil;
    NSError *error = nil;
    NSString *name = [NSString stringWithUTF8String:entry];
    id<MTLFunction> function = constants
        ? [library newFunctionWithName:name constantValues:constants error:&error]
        : [library newFunctionWithName:name];
    if (!function) {
        std::string reason = std::string("Metal kernel missing: ") + entry;
        if (error) reason += std::string(": ") + error.localizedDescription.UTF8String;
        fail(reason);
        return nil;
    }
    // The label names the kernel's command buffers in GPU traces.
    MTLComputePipelineDescriptor *descriptor = [MTLComputePipelineDescriptor new];
    descriptor.computeFunction = function;
    descriptor.label = function.name;
    id<MTLComputePipelineState> pipeline = [device_ newComputePipelineStateWithDescriptor:descriptor
        options:MTLPipelineOptionNone reflection:nil error:&error];
    if (!pipeline) fail(error.localizedDescription.UTF8String);
    return pipeline;
}

id<MTLBuffer> Runtime::allocate(size_t bytes) {
    if (!device_ || bytes == 0 || bytes > device_.maxBufferLength) return nil;
    return [device_ newBufferWithLength:bytes options:MTLResourceStorageModeShared];
}

id<MTLBuffer> Runtime::allocate_pages(size_t bytes) {
    const size_t page = static_cast<size_t>(getpagesize());
    if (!device_ || bytes == 0 || bytes > std::numeric_limits<size_t>::max() - page)
        return nil;
    size_t capacity = (bytes + page - 1) / page * page;
    if (capacity > device_.maxBufferLength) return nil;
    void *storage = nullptr;
    if (posix_memalign(&storage, page, capacity) != 0) return nil;
    id<MTLBuffer> buffer = [device_ newBufferWithBytesNoCopy:storage length:capacity
        options:MTLResourceStorageModeShared
        deallocator:^(void *pointer, NSUInteger) { free(pointer); }];
    if (!buffer) free(storage);
    return buffer;
}

bool Runtime::dispatch(id<MTLComputePipelineState> pipeline,
    const std::vector<id<MTLBuffer>> &buffers, size_t count, size_t group_size,
    double &gpu_seconds) {
    gpu_seconds = 0;
    if (!pipeline || !queue_ || group_size == 0 ||
        group_size > pipeline.maxTotalThreadsPerThreadgroup) {
        fail("invalid Metal dispatch");
        return false;
    }
    if (count == 0) return true;
    for (size_t i = 0; i < buffers.size(); ++i) {
        if (!buffers[i]) {
            fail("missing Metal dispatch buffer");
            return false;
        }
    }
    for (int attempt = 1;; ++attempt) {
        id<MTLCommandBuffer> command = [queue_ commandBuffer];
        id<MTLComputeCommandEncoder> encoder = [command computeCommandEncoder];
        if (!command || !encoder) {
            fail("Metal command allocation failed");
            return false;
        }
        command.label = encoder.label = pipeline.label;
        [encoder setComputePipelineState:pipeline];
        for (size_t i = 0; i < buffers.size(); ++i)
            [encoder setBuffer:buffers[i] offset:0 atIndex:i];
        [encoder dispatchThreads:MTLSizeMake(count, 1, 1)
            threadsPerThreadgroup:MTLSizeMake(group_size, 1, 1)];
        [encoder endEncoding];
        run(command);
        if (attempt < attempts && interrupted(command, attempt)) continue;
        if (command.status == MTLCommandBufferStatusCompleted) {
            gpu_seconds = std::max(0.0, command.GPUEndTime - command.GPUStartTime);
            return true;
        }
        fail(describe(command));
        return false;
    }
}

} // namespace wgse::metal
