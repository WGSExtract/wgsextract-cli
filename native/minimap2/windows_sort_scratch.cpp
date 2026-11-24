#include <cstdlib>

namespace {
struct Scratch {
    void *data = nullptr;
    void (*release)(void *) = nullptr;
    ~Scratch() { if (data) release(data); }
};
}

// Reuse one buffer per mapping thread, not one per Win32 fiber.
extern "C" void *wgse_windows_sort_scratch(size_t size, void (*release)(void *)) {
    thread_local Scratch scratch;
    if (!scratch.data) {
        scratch.data = std::calloc(1, size);
        scratch.release = release;
    }
    return scratch.data;
}
