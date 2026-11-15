// The low-memory warning before bwa-mem3 loads its index, on every platform.
#include "metal_extension.h"

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <string>
#include <sys/stat.h>
#ifdef __APPLE__
#include <sys/sysctl.h>
#else
#include <unistd.h>
#endif

namespace {
std::atomic<uint64_t> physical_memory_override{0};
#ifdef __APPLE__
constexpr const char *machine = "this Mac";
#else
constexpr const char *machine = "this computer";
#endif
} // namespace

void wgse_mem3_set_physical_memory(uint64_t bytes) { physical_memory_override = bytes; }

uint64_t wgse_mem3_physical_memory() {
    if (physical_memory_override) return physical_memory_override;
#ifdef __APPLE__
    uint64_t bytes = 0;
    size_t size = sizeof(bytes);
    return sysctlbyname("hw.memsize", &bytes, &size, nullptr, 0) == 0 ? bytes : 0;
#else
    const long pages = sysconf(_SC_PHYS_PAGES), page = sysconf(_SC_PAGESIZE);
    return pages > 0 && page > 0 ? uint64_t(pages) * uint64_t(page) : 0;
#endif
}

void wgse_mem3_memory_preflight(const char *prefix, int64_t chunk_bases) {
    // The index files bwa-mem3 loads, 1 GiB, and 24 bytes per base of a chunk, as for
    // wgse-bwa-mem2's CPU backend: 11.3 GiB for hs38 with -K 10000000, which peaked at
    // 10.7 GiB (10.9 GiB with --metal).
    uint64_t need = (uint64_t(1) << 30) + uint64_t(std::max<int64_t>(0, chunk_bases)) * 24;
    // bwa-mem3's sources are C++14, which has no std::filesystem.
    for (const char *suffix : {".bwt.2bit.64", ".pac"}) {
        struct stat file;
        if (stat((std::string(prefix) + suffix).c_str(), &file) == 0) need += uint64_t(file.st_size);
    }
    const uint64_t have = wgse_mem3_physical_memory();
    if (have && have < need)
        fprintf(stderr, "[wgse-bwa-mem3] warning: this run needs about %.1f GiB of memory but "
            "%s has %.1f GiB; expect heavy paging and a much slower run\n",
            double(need) / double(1ull << 30), machine, double(have) / double(1ull << 30));
}
