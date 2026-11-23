#include "runtime.h"
#include "watchdog_test.h"

#include <cstdio>

int main() {
    @autoreleasepool {
        wgse::metal::Runtime runtime;
        if (!runtime.device()) return 77;
        if (!runtime.queue()) return 1;
        wgse::metal::inject_watchdog_start_failure();
        // #1228: the synchronous wait returns without committing the command.
        id<MTLCommandBuffer> command = [runtime.queue() commandBuffer];
        if (!command) return 1;
        runtime.run(command);
        if (command.status != MTLCommandBufferStatusNotEnqueued) {
            fprintf(stderr, "Metal submitted a command without a watchdog (status %ld)\n",
                static_cast<long>(command.status));
            return 1;
        }
        if (runtime.describe(command).find("could not start the Metal watchdog thread")
            == std::string::npos || runtime.unsettled() != 0) return 1;

        // Later requests fall back without waiting, including beyond the two GPU slots.
        for (int i = 0; i < 3; ++i) {
            id<MTLCommandBuffer> later = [runtime.queue() commandBuffer];
            if (!later) return 1;
            dispatch_semaphore_t done = dispatch_semaphore_create(0);
            runtime.commit(later, ^(id<MTLCommandBuffer>) { dispatch_semaphore_signal(done); });
            if (dispatch_semaphore_wait(done, DISPATCH_TIME_NOW)
                || later.status != MTLCommandBufferStatusNotEnqueued
                || !dispatch_semaphore_wait(done, DISPATCH_TIME_NOW)) return 1;
        }
        return 0;
    }
}
