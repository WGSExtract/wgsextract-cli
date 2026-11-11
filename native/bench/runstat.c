/* Run a command and report what it cost, like /usr/bin/time -l, but also how it
 * ended: by exit status or by which signal (for example SIGKILL from jetsam).
 * Usage: runstat [-t seconds] <report-file> <command> [argument...]
 * Writes key=value lines to <report-file> and exits with the command's status
 * (128 + signal if it was killed). SIGTERM and SIGINT are forwarded to it.
 * With -t, the command gets SIGTERM after that many seconds, and SIGKILL 30
 * seconds later if it is still running; the report then has timed_out=1.
 * Linux has no footprint or pageins counters, so those lines are left out. */
#include <errno.h>
#include <stdlib.h>
#ifdef __APPLE__
#include <libproc.h>
#endif
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static volatile pid_t child;
static volatile sig_atomic_t timed_out;

static void forward(int signo) {
    if (child > 0) kill(child, signo);
}

static void expire(int signo) {
    (void)signo;
    if (child <= 0 || kill(child, timed_out ? SIGKILL : SIGTERM) != 0) return;
    timed_out = 1;
    alarm(30);
}

static double seconds(struct timeval t) { return t.tv_sec + t.tv_usec / 1e6; }

int main(int argc, char **argv) {
    unsigned timeout = 0;
    if (argc > 2 && strcmp(argv[1], "-t") == 0) {
        char *end;
        long value = strtol(argv[2], &end, 10);
        if (*end || value <= 0) argc = 0;
        timeout = (unsigned)value;
        argv += 2;
        argc -= 2;
    }
    if (argc < 3) {
        fprintf(stderr, "usage: runstat [-t seconds] <report-file> <command> [argument...]\n");
        return 2;
    }
    struct timespec start, end;
    clock_gettime(CLOCK_MONOTONIC, &start);
    pid_t pid = fork();
    if (pid < 0) {
        perror("runstat: fork");
        return 2;
    }
    if (pid == 0) {
        execvp(argv[2], argv + 2);
        fprintf(stderr, "runstat: cannot run %s: %s\n", argv[2], strerror(errno));
        _exit(127);
    }
    child = pid;
    struct sigaction action = {0};
    sigemptyset(&action.sa_mask);
    action.sa_handler = forward;
    sigaction(SIGTERM, &action, NULL);
    sigaction(SIGINT, &action, NULL);
    action.sa_handler = expire;
    sigaction(SIGALRM, &action, NULL);
    if (timeout) alarm(timeout);

    siginfo_t info;
    while (waitid(P_PID, pid, &info, WEXITED | WNOWAIT) != 0)
        if (errno != EINTR) {
            perror("runstat: waitid");
            return 2;
        }
    clock_gettime(CLOCK_MONOTONIC, &end);
    alarm(0);
    /* The exited child keeps its accounting until it is reaped. */
#ifdef __APPLE__
    struct rusage_info_v4 usage;
    int have_usage = proc_pid_rusage(pid, RUSAGE_INFO_V4, (rusage_info_t *)&usage) == 0;
#endif
    int status;
    struct rusage rusage;
    while (wait4(pid, &status, 0, &rusage) < 0)
        if (errno != EINTR) {
            perror("runstat: wait4");
            return 2;
        }

    int code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
    FILE *report = fopen(argv[1], "w");
    if (!report) {
        perror("runstat: report");
        return code;
    }
    fprintf(report, "exit_status=%d\n", WIFEXITED(status) ? WEXITSTATUS(status) : -1);
    fprintf(report, "signal=%d\n", WIFSIGNALED(status) ? WTERMSIG(status) : 0);
    fprintf(report, "timed_out=%d\n", (int)timed_out);
    fprintf(report, "real_s=%.3f\n",
            (end.tv_sec - start.tv_sec) + (end.tv_nsec - start.tv_nsec) / 1e9);
    fprintf(report, "user_s=%.3f\n", seconds(rusage.ru_utime));
    fprintf(report, "sys_s=%.3f\n", seconds(rusage.ru_stime));
#ifdef __APPLE__
    fprintf(report, "max_rss_bytes=%ld\n", rusage.ru_maxrss); /* bytes on macOS */
#else
    fprintf(report, "max_rss_bytes=%ld\n", rusage.ru_maxrss * 1024L); /* KiB on Linux */
#endif
    fprintf(report, "page_reclaims=%ld\n", rusage.ru_minflt);
    fprintf(report, "page_faults=%ld\n", rusage.ru_majflt);
    fprintf(report, "involuntary_switches=%ld\n", rusage.ru_nivcsw);
#ifdef __APPLE__
    if (have_usage) {
        fprintf(report, "peak_footprint_bytes=%llu\n", usage.ri_lifetime_max_phys_footprint);
        fprintf(report, "pageins=%llu\n", usage.ri_pageins);
    }
#endif
    fclose(report);
    return code;
}
