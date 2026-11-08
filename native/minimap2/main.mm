#include "runtime.h"

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

extern "C" int wgse_minimap2_main(int argc, char *argv[]);

namespace {
constexpr const char *name = "wgse-minimap2";
constexpr const char *version = WGSE_VERSION;
constexpr const char *engine_version = "2.31-r1302";

void help(FILE *stream) {
    fprintf(stream, "%s %s (minimap2 %s rebuilt for Apple Silicon)\n"
        "Usage: %s [minimap2 options] <target.fa>|<target.mmi> [query.fa[.gz]|query.fq[.gz]] [...]\n"
        "       %s capabilities --json   report the Metal hardware (no Metal backend yet)\n"
        "       %s --version             print executable and engine version\n"
        "Alignment is minimap2's, unchanged; SAM/PAF is written to stdout, diagnostics to stderr.\n"
        "Not qualified for clinical use.\n\n",
        name, version, engine_version, name, name, name);
}

bool metal_hardware_supported() {
    wgse::metal::Runtime runtime;
    return runtime.device() != nil;
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
        // There is no Metal backend yet, so Metal is never available for alignment.
        printf("{\"schema_version\":1,\"version\":\"%s\","
            "\"engine\":\"minimap2\",\"engine_version\":\"%s\","
            "\"qualified\":false,\"metal_available\":false,"
            "\"metal_hardware_supported\":%s,\"cpu_available\":true}\n",
            version, engine_version, metal_hardware_supported() ? "true" : "false");
        return 0;
    }
    // Upstream then prints its options: to stdout for -h (exit 0), else to stderr (exit 1).
    if (command == "--help" || command == "-h") {
        help(stdout);
        char help_option[] = "-h";
        char *arguments[] = {argv[0], help_option, nullptr};
        return wgse_minimap2_main(2, arguments);
    }
    if (command.empty()) help(stderr);
    return wgse_minimap2_main(argc, argv);
}
} // namespace

int main(int argc, char **argv) {
    @autoreleasepool {
        return run(argc, argv);
    }
}
