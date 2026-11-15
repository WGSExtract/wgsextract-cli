/* Wire memory so that a benchmark sees a computer with less RAM.
 * Usage: balloon <MiB>
 * Allocates and mlock()s <MiB> in 64 MiB pieces, prints "wired <n> MiB" (or
 * how far it got and why it stopped), then holds the memory until a signal
 * kills it or its parent exits, so that a killed benchmark cannot leave memory
 * wired. If it cannot wire everything it says so and exits 1 at once,
 * releasing what it wired. */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#ifdef __APPLE__
#include <sys/event.h>
#else
#include <signal.h>
#include <sys/prctl.h>
#endif

int main(int argc, char **argv) {
    const size_t piece = (size_t)64 << 20;
    char *end;
    long mib = argc == 2 ? strtol(argv[1], &end, 10) : 0;
    if (argc != 2 || *end || mib <= 0) {
        fprintf(stderr, "usage: balloon <MiB>\n");
        return 2;
    }
    pid_t parent = getppid();
#ifdef __APPLE__
    int queue = kqueue();
    struct kevent watch;
    EV_SET(&watch, parent, EVFILT_PROC, EV_ADD, NOTE_EXIT, 0, NULL);
    if (queue < 0 || kevent(queue, &watch, 1, NULL, 0, NULL) < 0) {
#else
    // The parent's exit sends SIGTERM, which ends this process and frees the memory.
    if (prctl(PR_SET_PDEATHSIG, SIGTERM) != 0 || getppid() != parent) {
#endif
        fprintf(stderr, "balloon: cannot watch the parent process: %s\n", strerror(errno));
        return 1;
    }
    size_t wanted = (size_t)mib << 20, wired = 0;
    while (wired < wanted) {
        size_t size = wanted - wired < piece ? wanted - wired : piece;
        void *block = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
        if (block == MAP_FAILED) {
            fprintf(stderr, "balloon: mmap failed after %zu MiB: %s\n", wired >> 20, strerror(errno));
            break;
        }
        memset(block, 1, size);
        if (mlock(block, size) != 0) {
            fprintf(stderr, "balloon: mlock failed after %zu MiB: %s\n", wired >> 20, strerror(errno));
            munmap(block, size);
            break;
        }
        wired += size;
    }
    printf("wired %zu MiB\n", wired >> 20);
    fflush(stdout);
    if (wired < wanted) return 1;
    // SIGTERM and SIGINT keep their default action, ending the process and freeing the memory.
#ifdef __APPLE__
    struct kevent event;
    while (kevent(queue, NULL, 0, &event, 1, NULL) < 0 && errno == EINTR) {}
#else
    for (;;) pause();
#endif
    return 0;
}
