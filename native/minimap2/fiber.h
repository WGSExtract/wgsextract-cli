// ksw_host.cpp's fibers, for the chaining offload (chain_host.cpp): a read's fiber
// queues GPU work, then waits for it while its thread runs other reads.
#pragma once

#include <cstddef>

namespace wgse::fiber {

struct Read; // the read a fiber is mapping

// The calling fiber's read, or null outside the GPU engine's fibers.
Read *current();
// Before queuing work: the number of completions wait() waits for.
void expect(Read *read, size_t n);
// From any thread, when queued work is done.
void complete(Read *read, size_t n);
// Runs other reads until the expected work is complete.
void wait(Read *read);
// Set by the chaining offload: mapping threads with no read to run call it to compute
// queued work on the CPU (all reads started if tail); true if it did some.
extern bool (*idle_work)(bool tail);

} // namespace wgse::fiber
