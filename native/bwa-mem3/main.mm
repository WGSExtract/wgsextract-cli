#include "runtime.h"

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

extern "C" int wgse_bwamem3_main(int argc, char *argv[]);

namespace {
constexpr const char *name = "wgse-bwa-mem3";
constexpr const char *version = WGSE_VERSION;
constexpr const char *engine_version = MEM3_VERSION;

void help(FILE *stream) {
    fprintf(stream, "%s %s (bwa-mem3 %s rebuilt for Apple Silicon)\n"
        "Usage: %s [--backend cpu|auto] <command> [arguments]\n"
        "         bwa-mem3's commands: index, mem, shm, re-sa\n"
        "       %s capabilities --json   report the Metal hardware\n"
        "       %s --version | version   print executable and engine version\n"
        "       %s version --verbose     also print bwa-mem3's build details\n"
        "Alignment is bwa-mem3's, unchanged, on the CPU: there is no Metal backend yet, so\n"
        "--metal and --backend metal fail and --backend auto runs on the CPU.\n"
        "SAM is written to stdout, diagnostics to stderr. Not qualified for clinical use.\n\n",
        name, version, engine_version, name, name, name, name);
}

bool metal_hardware_supported() {
    wgse::metal::Runtime runtime;
    return runtime.device() != nil;
}

int run(int argc, char **argv) {
    // Backend options precede the command, as for wgse-bwa and wgse-bwa-mem2.
    int command_index = 1;
    bool automatic = false;
    while (argc > command_index) {
        const char *option = argv[command_index];
        const char *value = argc > command_index + 1 ? argv[command_index + 1] : "";
        if (strcmp(option, "--metal") == 0 || (strcmp(option, "--backend") == 0
                && strcmp(value, "metal") == 0)) {
            fprintf(stderr, "Metal unavailable: %s %s has no Metal backend\n", name, version);
            return 1;
        }
        if (strcmp(option, "--backend") != 0) break;
        if (strcmp(value, "auto") == 0) automatic = true;
        else if (strcmp(value, "cpu") != 0) {
            fprintf(stderr, "--backend requires cpu, metal or auto\n");
            return 2;
        }
        command_index += 2;
    }
    const int arguments_left = argc - command_index;
    const std::string command = arguments_left > 0 ? argv[command_index] : "";
    if (automatic && command == "mem")
        fprintf(stderr, "[%s] --backend auto: no Metal backend; aligning on the CPU\n", name);
    if (command == "--version" || command == "version") {
        const bool verbose = arguments_left == 2 && strcmp(argv[command_index + 1], "--verbose") == 0;
        if (arguments_left > 1 && !verbose) {
            fprintf(stderr, "Usage: %s version [--verbose]\n", name);
            return 2;
        }
        printf("%s %s; bwa-mem3 %s\n", name, version, engine_version);
        if (!verbose) return 0;
        fflush(stdout);
        char upstream_command[] = "version";
        char *upstream_arguments[] = {argv[0], upstream_command, nullptr};
        return wgse_bwamem3_main(2, upstream_arguments);
    }
    if (command == "capabilities") {
        if (arguments_left != 2 || strcmp(argv[command_index + 1], "--json")) {
            fprintf(stderr, "Usage: %s capabilities --json\n", name);
            return 2;
        }
        // There is no Metal backend yet, so Metal is never available for alignment.
        printf("{\"schema_version\":1,\"version\":\"%s\","
            "\"engine\":\"bwa-mem3\",\"engine_version\":\"%s\","
            "\"qualified\":false,\"metal_available\":false,"
            "\"metal_hardware_supported\":%s,\"cpu_available\":true}\n",
            version, engine_version, metal_hardware_supported() ? "true" : "false");
        return 0;
    }
    if (command.empty() || command == "--help" || command == "-h")
        help(command.empty() ? stderr : stdout);
    // Upstream records argv[0] plus its arguments as the @PG CL; fold the backend options
    // into argv[0] so the CL keeps the full command line.
    std::string program = argv[0];
    for (int i = 1; i < command_index; ++i) program.append(" ").append(argv[i]);
    std::vector<char *> arguments{program.data()};
    arguments.insert(arguments.end(), argv + command_index, argv + argc);
    arguments.push_back(nullptr);
    return wgse_bwamem3_main(static_cast<int>(arguments.size() - 1), arguments.data());
}
} // namespace

int main(int argc, char **argv) {
    @autoreleasepool {
        return run(argc, argv);
    }
}
