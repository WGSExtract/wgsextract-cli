#include "metal_extension.h"
#include "metal_seeding_api.h"
#include "rescue_api.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

extern "C" int wgse_bwamem3_main(int argc, char *argv[]);

namespace {
constexpr const char *name = "wgse-bwa-mem3";
constexpr const char *version = WGSE_VERSION;
constexpr const char *engine_version = MEM3_VERSION;

#ifdef __APPLE__
constexpr const char *platform = "Apple Silicon", *gpu_option = "--metal";
#else
constexpr const char *platform = "Linux", *gpu_option = "--cuda";
#endif

void help(FILE *stream) {
    fprintf(stream, "%s %s (bwa-mem3 %s rebuilt for %s)\n"
        "Usage: %s [%s | --backend cpu|%s|auto] [--gpu-threads PERCENT] [--gpu-seed on|off]\n"
        "         [--gpu-backlog PAIRS] [--gpu-rescue on|off] <command> [arguments]\n"
        "         bwa-mem3's commands: index, mem, shm, re-sa\n"
        "       %s capabilities --json   report %s availability\n"
        "       %s --version | version   print executable and engine version\n"
        "       %s version --verbose     also print bwa-mem3's build details\n"
        "Alignment is bwa-mem3's on the CPU by default. %s (EXPERIMENTAL) collects SMEMs (unless\n"
        "--gpu-seed off) and scores bwa-mem3's 8-bit seed extensions on the GPU, with byte-identical\n"
        "output; CPU threads score extensions while more than PAIRS (default 4096 with GPU seeding,\n"
        "else 0 = never) wait for the GPU. It runs PERCENT%% (default\n"
        "%d) of -t compute threads, as threads wait for the GPU. %s fails if %s is\n"
        "unavailable, and exits 1 after finishing on the CPU if a GPU batch fails; --backend auto\n"
        "falls back to the CPU in both cases. With CUDA it also aligns mate-rescue windows on the\n"
        "GPU (unless --gpu-rescue off).\n"
        "SAM is written to stdout, diagnostics to stderr. Not qualified for clinical use.\n\n",
        name, version, engine_version, platform, name, gpu_option, gpu_option + 2, name,
        wgse_mem3_gpu_name(), name, name, gpu_option, wgse_mem3_default_thread_percent,
        gpu_option, wgse_mem3_gpu_name());
}

std::string json(const std::string &text) {
    std::string quoted = "\"";
    for (unsigned char c : text) {
        if (c == '"' || c == '\\') quoted += '\\';
        if (c >= 0x20) quoted += char(c);
    }
    return quoted + "\"";
}

int run(int argc, char **argv) {
    // Backend options precede the command, as for wgse-bwa and wgse-bwa-mem2.
    // gpu: --metal on macOS, --cuda on Linux.
    enum class Backend { cpu, gpu, automatic } backend = Backend::cpu;
    bool gpu_seed = true, gpu_rescue = true;
    long long gpu_backlog = -1;
    int command_index = 1;
    while (argc > command_index) {
        const char *option = argv[command_index];
        const bool has_value = argc > command_index + 1;
        const char *value = has_value ? argv[command_index + 1] : "";
        if (strcmp(option, gpu_option) == 0) {
            backend = Backend::gpu;
            ++command_index;
        } else if (strcmp(option, "--gpu-seed") == 0) {
            if (strcmp(value, "on") == 0) gpu_seed = true;
            else if (strcmp(value, "off") == 0) gpu_seed = false;
            else {
                fprintf(stderr, "--gpu-seed requires on or off\n");
                return 2;
            }
            command_index += 2;
        } else if (strcmp(option, "--gpu-rescue") == 0) {
            if (strcmp(value, "on") == 0) gpu_rescue = true;
            else if (strcmp(value, "off") == 0) gpu_rescue = false;
            else {
                fprintf(stderr, "--gpu-rescue requires on or off\n");
                return 2;
            }
            command_index += 2;
        } else if (strcmp(option, "--gpu-inject-rescue-failure") == 0) {
            wgse_mem3_rescue_inject_failure();
            ++command_index;
        } else if (strcmp(option, "--gpu-verify-seeds") == 0) {
            wgse_mem3_seed_set_verify(true);
            ++command_index;
        } else if (strcmp(option, "--gpu-seed-wait") == 0) {
            wgse_mem3_seed_set_wait(true);
            ++command_index;
        } else if (strcmp(option, "--gpu-inject-seed-failure") == 0) {
            wgse_mem3_seed_inject_failure();
            ++command_index;
        } else if (strcmp(option, "--gpu-inject-extension-failure") == 0) {
            wgse_mem3_bsw_inject_failure();
            ++command_index;
        } else if (strcmp(option, "--backend") == 0) {
            if (strcmp(value, "cpu") == 0) backend = Backend::cpu;
            else if (strcmp(value, gpu_option + 2) == 0) backend = Backend::gpu;
            else if (strcmp(value, "auto") == 0) backend = Backend::automatic;
            else {
                fprintf(stderr, "--backend requires cpu, %s or auto\n", gpu_option + 2);
                return 2;
            }
            command_index += 2;
        } else if (strcmp(option, "--physical-memory") == 0) {
            char *end = nullptr;
            const unsigned long long bytes = has_value ? strtoull(value, &end, 10) : 0;
            if (!has_value || *end || bytes == 0) {
                fprintf(stderr, "--physical-memory requires a byte count\n");
                return 2;
            }
            wgse_mem3_set_physical_memory(bytes);
            command_index += 2;
        } else if (strcmp(option, "--gpu-backlog") == 0) {
            char *end = nullptr;
            const long long pairs = has_value ? strtoll(value, &end, 10) : -1;
            if (!has_value || *end || pairs < 0) {
                fprintf(stderr, "--gpu-backlog requires a pair count\n");
                return 2;
            }
            gpu_backlog = pairs;
            command_index += 2;
        } else if (strcmp(option, "--gpu-threads") == 0
            || strcmp(option, "--gpu-inject-interruptions") == 0) {
            char *end = nullptr;
            const long number = has_value ? strtol(value, &end, 10) : -1;
            const bool threads = option[6] == 't';
            if (!has_value || *end || number < (threads ? 100 : 0) || number > (threads ? 800 : 64)) {
                fprintf(stderr, threads ? "--gpu-threads requires a percentage from 100 to 800\n"
                    : "--gpu-inject-interruptions requires a count from 0 to 64\n");
                return 2;
            }
            if (threads) wgse_mem3_bsw_set_threads(int(number));
            else wgse_mem3_gpu_inject_interruptions(size_t(number));
            command_index += 2;
#ifdef WGSE_METAL_BENCHMARK_TOOLS
        } else if (strcmp(option, "--gpu-inject-hang") == 0) {
            // Withholds the first GPU command whose label starts with value, as
            // a hung GPU would; the watchdog gives up on the GPU after 2 s.
            if (!has_value) {
                fprintf(stderr, "--gpu-inject-hang requires a command label prefix\n");
                return 2;
            }
            wgse_mem3_gpu_inject_hang(value);
            command_index += 2;
#endif
        } else {
            break;
        }
    }
    const int arguments_left = argc - command_index;
    const std::string command = arguments_left > 0 ? argv[command_index] : "";
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
        std::string reason;
        const bool available = wgse_mem3_bsw_available(reason);
        if (!available) fprintf(stderr, "%s unavailable: %s\n", wgse_mem3_gpu_name(), reason.c_str());
        const bool supported = wgse_mem3_gpu_hardware_supported();
#ifdef __APPLE__
        const bool metal = available, metal_supported = supported, cuda = false, cuda_supported = false;
#else
        const bool metal = false, metal_supported = false, cuda = available, cuda_supported = supported;
#endif
        const WgseMem3MetalLimits limits = wgse_mem3_metal_limits();
        printf("{\"schema_version\":1,\"version\":\"%s\","
            "\"engine\":\"bwa-mem3\",\"engine_version\":\"%s\","
            "\"qualified\":false,\"metal_available\":%s,"
            "\"metal_hardware_supported\":%s,\"cuda_available\":%s,"
            "\"cuda_hardware_supported\":%s,"
            "\"cpu_available\":true,\"max_gpu_query_length\":%d,"
            "\"physical_memory_bytes\":%llu,\"metal\":{\"device\":%s,"
            "\"max_buffer_bytes\":%llu,\"recommended_max_working_set_bytes\":%llu}}\n",
            version, engine_version, metal ? "true" : "false",
            metal_supported ? "true" : "false", cuda ? "true" : "false",
            cuda_supported ? "true" : "false", wgse_mem3_max_gpu_query_length,
            (unsigned long long)wgse_mem3_physical_memory(), json(limits.device).c_str(),
            (unsigned long long)limits.max_buffer_bytes,
            (unsigned long long)limits.recommended_working_set_bytes);
        return 0;
    }
    if (command.empty() || command == "--help" || command == "-h")
        help(command.empty() ? stderr : stdout);
    const bool accelerated = command == "mem" && backend != Backend::cpu;
    if (accelerated) {
        std::string reason;
        if (!wgse_mem3_bsw_start(reason)) {
            if (backend == Backend::gpu) {
                fprintf(stderr, "%s unavailable: %s\n", wgse_mem3_gpu_name(), reason.c_str());
                return 1;
            }
            fprintf(stderr, "[%s] CPU fallback: %s unavailable (%s)\n", name, wgse_mem3_gpu_name(),
                reason.c_str());
        } else {
            wgse_mem3_seed_set_enabled(gpu_seed);
            wgse_mem3_rescue_set_enabled(gpu_rescue);
            // With seeding on the GPU, the CPU threads have time to score extensions when the
            // GPU's queue is long.
            wgse_mem3_bsw_set_backlog(gpu_backlog >= 0 ? gpu_backlog : gpu_seed ? 4096 : 0);
        }
    }
    // Upstream records argv[0] plus its arguments as the @PG CL; fold the backend options
    // into argv[0] so the CL keeps the full command line.
    std::string program = argv[0];
    for (int i = 1; i < command_index; ++i) program.append(" ").append(argv[i]);
    std::vector<char *> arguments{program.data()};
    arguments.insert(arguments.end(), argv + command_index, argv + argc);
    arguments.push_back(nullptr);
    int result = wgse_bwamem3_main(static_cast<int>(arguments.size() - 1), arguments.data());
    const bool seeding_failed = accelerated && wgse_mem3_seed_finish();
    const bool extension_failed = accelerated && wgse_mem3_bsw_finish();
    const bool rescue_failed = accelerated && wgse_mem3_rescue_finish();
    if ((seeding_failed || extension_failed || rescue_failed) && backend == Backend::gpu && result == 0)
        result = 1;
    return result;
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
