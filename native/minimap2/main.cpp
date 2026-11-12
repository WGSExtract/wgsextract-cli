#include "metal_ksw.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

extern "C" int wgse_minimap2_main(int argc, char *argv[]);
// The command line minimap2 records as @PG CL, with the wrapper's options.
extern "C" {
int wgse_pg_argc = 0;
char **wgse_pg_argv = nullptr;
}

namespace {
constexpr const char *name = "wgse-minimap2";
constexpr const char *version = WGSE_VERSION;
constexpr const char *engine_version = "2.31-r1302";

void help(FILE *stream) {
#ifdef __APPLE__
    fprintf(stream, "%s %s (minimap2 %s rebuilt for Apple Silicon)\n"
        "Usage: %s [--metal] [minimap2 options] <target.fa>|<target.mmi> [query.fa[.gz]|query.fq[.gz]] [...]\n"
        "       %s capabilities --json   report Metal availability\n"
        "       %s --version             print executable and engine version\n"
        "Alignment is minimap2's, unchanged; SAM/PAF is written to stdout, diagnostics to stderr.\n"
        "--metal (EXPERIMENTAL) runs most base-level gap fills between seeds on the GPU for\n"
        "presets with dual affine gaps, such as map-hifi, map-ont and map-pb, with identical\n"
        "output; sr and splice presets stay on the CPU. It fails if Metal is unavailable,\n"
        "and exits 1 after finishing on the CPU if a GPU batch fails.\n"
        "Not qualified for clinical use.\n\n",
        name, version, engine_version, name, name, name);
#else
    fprintf(stream, "%s %s (minimap2 %s rebuilt for Linux)\n"
        "Usage: %s [minimap2 options] <target.fa>|<target.mmi> [query.fa[.gz]|query.fq[.gz]] [...]\n"
        "       %s capabilities --json   report what this build can run on\n"
        "       %s --version             print executable and engine version\n"
        "Alignment is minimap2's, unchanged; SAM/PAF is written to stdout, diagnostics to stderr.\n"
        "Not qualified for clinical use.\n\n",
        name, version, engine_version, name, name, name);
#endif
}

#ifdef WGSE_METAL_BENCHMARK_TOOLS
bool count_option(int argc, char **argv, int &index, int &value) {
    const char *text = argc > index + 1 ? argv[index + 1] : "";
    char *end = nullptr;
    const long parsed = strtol(text, &end, 10);
    if (end == text || *end || parsed < 1 || parsed > 1 << 24) {
        fprintf(stderr, "%s requires a positive count\n", argv[index]);
        return false;
    }
    value = (int)parsed;
    index += 2;
    return true;
}
#endif

// Strips the wrapper's options, which precede minimap2's; returns -1 or an exit code.
int parse_metal_options(int &argc, char **&argv, bool &metal) {
    int index = 1;
#ifdef WGSE_METAL_BENCHMARK_TOOLS
    int fibers = 0, batch = 0, max_cost = -1, fail = 0, interruptions = 0;
#endif
    while (index < argc) {
        if (strcmp(argv[index], "--metal") == 0) {
            metal = true;
            ++index;
#ifdef WGSE_METAL_BENCHMARK_TOOLS
        } else if (strcmp(argv[index], "--gpu-fibers") == 0) {
            if (!count_option(argc, argv, index, fibers)) return 2;
        } else if (strcmp(argv[index], "--gpu-batch") == 0) {
            if (!count_option(argc, argv, index, batch)) return 2;
        } else if (strcmp(argv[index], "--gpu-max-cost") == 0) {
            if (!count_option(argc, argv, index, max_cost)) return 2;
        } else if (strcmp(argv[index], "--gpu-inject-interruptions") == 0) {
            if (!count_option(argc, argv, index, interruptions)) return 2;
            wgse_ksw_inject_interruptions(interruptions);
        } else if (strcmp(argv[index], "--gpu-inject-hang") == 0) {
            // Withholds the first GPU command whose label starts with the value,
            // as a hung GPU would; the watchdog gives up on the GPU after 2 s.
            if (index + 1 >= argc) return 2;
            wgse_ksw_inject_hang(argv[index + 1], 2);
            index += 2;
        } else if (strcmp(argv[index], "--gpu-inject-failure") == 0) {
            fail = 1;
            ++index;
#endif
        } else {
            break;
        }
    }
#ifdef WGSE_METAL_BENCHMARK_TOOLS
    wgse_ksw_tune(fibers, batch, max_cost, fail);
#endif
    argv[index - 1] = argv[0];
    argv += index - 1;
    argc -= index - 1;
    return -1;
}

int run(int argc, char **argv) {
    const std::string command = argc > 1 ? argv[1] : "";
    if (command == "--version" || command == "version") {
        printf("%s %s; minimap2 %s\n", name, version, engine_version);
        return 0;
    }
    if (command == "capabilities") {
        if (argc != 3 || strcmp(argv[2], "--json")) {
            fprintf(stderr, "Usage: %s capabilities --json\n", name);
            return 2;
        }
        char reason[256] = "";
        const bool available = wgse_ksw_metal_start(reason, sizeof reason);
        const bool supported = wgse_ksw_gpu_hardware_supported();
#ifdef __APPLE__
        const bool metal = available, metal_supported = supported, cuda = false, cuda_supported = false;
#else
        const bool metal = false, metal_supported = false, cuda = available, cuda_supported = supported;
#endif
        printf("{\"schema_version\":1,\"version\":\"%s\","
            "\"engine\":\"minimap2\",\"engine_version\":\"%s\","
            "\"qualified\":false,\"metal_available\":%s,"
            "\"metal_hardware_supported\":%s,\"cuda_available\":%s,"
            "\"cuda_hardware_supported\":%s,\"cpu_available\":true}\n",
            version, engine_version, metal ? "true" : "false", metal_supported ? "true" : "false",
            cuda ? "true" : "false", cuda_supported ? "true" : "false");
        return 0;
    }
    // Upstream then prints its options: to stdout for -h (exit 0), else to stderr (exit 1).
    if (command == "--help" || command == "-h") {
        help(stdout);
        char help_option[] = "-h";
        char *arguments[] = {argv[0], help_option, nullptr};
        return wgse_minimap2_main(2, arguments);
    }
    std::vector<char *> command_line(argv, argv + argc + 1);
    wgse_pg_argc = argc;
    wgse_pg_argv = command_line.data();
    bool metal = false;
    const int status = parse_metal_options(argc, argv, metal);
    if (status >= 0) return status;
    // Upstream's stderr CMD starts with argv[0]: fold the wrapper options into it.
    std::string program = command_line[0];
    for (int i = 1; i < wgse_pg_argc - argc + 1; ++i) program.append(" ").append(command_line[i]);
    argv[0] = program.data();
    if (argc < 2) help(stderr);
    if (metal) {
        char reason[256] = "";
        if (!wgse_ksw_metal_start(reason, sizeof reason)) {
            fprintf(stderr, "[wgse-minimap2] --metal: Metal is unavailable: %s\n", reason);
            return 1;
        }
    }
    int ret = wgse_minimap2_main(argc, argv);
    if (metal) {
        wgse_ksw_print_metrics();
        char reason[256] = "";
        if (wgse_ksw_metal_failed(reason, sizeof reason)) {
            fprintf(stderr, "[wgse-minimap2] --metal: a GPU batch failed (%s); "
                "its gap fills ran on the CPU, so the output is complete\n", reason);
            if (ret == 0) ret = 1;
        }
    }
    return ret;
}
} // namespace

int main(int argc, char **argv) {
#ifdef __OBJC__
    @autoreleasepool {
        return run(argc, argv);
    }
#else
    return run(argc, argv);
#endif
}
