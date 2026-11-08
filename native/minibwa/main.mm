#include "runtime.h"

#include <cstdio>
#include <cstring>
#include <string>

extern "C" int wgse_minibwa_main(int argc, char *argv[]);

namespace {
constexpr const char *name = "wgse-minibwa";
constexpr const char *version = WGSE_VERSION;
constexpr const char *engine_version = "0.7-r421";

void banner(FILE *stream) {
    fprintf(stream, "%s %s (minibwa %s rebuilt for Apple Silicon)\n"
        "Usage: %s <minibwa command> [options]   for example: %s map -t 9 ref.fa R1.fq.gz R2.fq.gz\n"
        "       %s capabilities --json      report the Metal hardware (no Metal backend)\n"
        "       %s --version                print executable and engine version\n"
        "Alignment is minibwa's, unchanged; SAM/PAF is written to stdout, diagnostics to stderr.\n"
        "Not qualified for clinical use.\n\n",
        name, version, engine_version, name, name, name, name);
}

bool metal_hardware_supported() {
    wgse::metal::Runtime runtime;
    return runtime.device() != nil;
}

int run(int argc, char **argv) {
    const std::string command = argc > 1 ? argv[1] : "";
    if (command == "--version") {
        printf("%s %s; minibwa %s\n", name, version, engine_version);
        return 0;
    }
    if (command == "capabilities") {
        if (argc != 3 || strcmp(argv[2], "--json")) {
            fprintf(stderr, "Usage: %s capabilities --json\n", name);
            return 2;
        }
        // There is no Metal backend, so Metal is never available for alignment.
        printf("{\"schema_version\":1,\"version\":\"%s\","
            "\"engine\":\"minibwa\",\"engine_version\":\"%s\","
            "\"qualified\":false,\"metal_available\":false,"
            "\"metal_hardware_supported\":%s,\"cpu_available\":true}\n",
            version, engine_version, metal_hardware_supported() ? "true" : "false");
        return 0;
    }
    // Upstream lists its commands on stdout, with exit code 0, for no command or --help.
    if (command.empty() || command == "--help" || command == "-h") {
        banner(stdout);
        char help_option[] = "--help";
        char *arguments[] = {argv[0], help_option, nullptr};
        return wgse_minibwa_main(command.empty() ? 1 : 2, arguments);
    }
    return wgse_minibwa_main(argc, argv);
}
} // namespace

int main(int argc, char **argv) {
    @autoreleasepool {
        return run(argc, argv);
    }
}
