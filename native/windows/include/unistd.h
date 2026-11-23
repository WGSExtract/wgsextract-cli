/* The POSIX names BWA uses from <unistd.h>, for MSVC-ABI builds (clang-cl),
 * which have no <unistd.h>. Implementations are in ../msvc_compat.c. */
#ifndef WGSE_MSVC_UNISTD_H
#define WGSE_MSVC_UNISTD_H

#include <BaseTsd.h>
#include <io.h>
#include <process.h>
#include <stdlib.h>

#ifndef _SSIZE_T_DEFINED
#define _SSIZE_T_DEFINED
typedef SSIZE_T ssize_t;
#endif
#ifndef _PID_T_DEFINED
#define _PID_T_DEFINED
typedef int pid_t;
#endif

#define STDIN_FILENO 0
#define STDOUT_FILENO 1
#define STDERR_FILENO 2

#define _SC_PAGESIZE 1
#define _SC_PHYS_PAGES 2
#define _SC_NPROCESSORS_ONLN 3

#ifdef __cplusplus
extern "C" {
#endif

long sysconf(int name);
int getpagesize(void);

extern char *optarg;
extern int optind, opterr, optopt;
int getopt(int argc, char *const argv[], const char *optstring);

#ifdef __cplusplus
}
#endif
#endif
