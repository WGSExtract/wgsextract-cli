/* POSIX functions for MSVC-ABI builds (clang-cl) of wgse-bwa: getopt,
 * gettimeofday, sysconf and POSIX threads (include/). The MinGW shims in
 * packaging/windows/compat (drand48, getrusage, binary I/O) are linked too. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <errno.h>
#include <process.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pthread.h"
#include "sys/time.h"
#include "unistd.h"

/* POSIX getopt (no argument permutation: options precede operands). */
char *optarg;
int optind = 1, opterr = 1, optopt;
static int wgse_optpos = 1;

int getopt(int argc, char *const argv[], const char *optstring)
{
	const char *arg, *spec;
	const int quiet = optstring[0] == ':';
	optarg = NULL;
	if (optind == 0) optind = 1, wgse_optpos = 1;
	if (optind >= argc || !argv[optind] || argv[optind][0] != '-' || !argv[optind][1]) return -1;
	if (!strcmp(argv[optind], "--")) {
		++optind;
		return -1;
	}
	arg = argv[optind];
	optopt = (unsigned char)arg[wgse_optpos];
	spec = optopt == ':' ? NULL : strchr(optstring + quiet, optopt);
	if (spec && spec[1] == ':') {
		if (arg[wgse_optpos + 1]) {
			optarg = (char *)arg + wgse_optpos + 1;
		} else if (optind + 1 < argc) {
			optarg = argv[++optind];
		} else {
			++optind, wgse_optpos = 1;
			if (!quiet && opterr) fprintf(stderr, "%s: option requires an argument -- '%c'\n", argv[0], optopt);
			return quiet ? ':' : '?';
		}
		++optind, wgse_optpos = 1;
		return optopt;
	}
	if (!arg[++wgse_optpos]) ++optind, wgse_optpos = 1;
	if (!spec) {
		if (!quiet && opterr) fprintf(stderr, "%s: invalid option -- '%c'\n", argv[0], optopt);
		return '?';
	}
	return optopt;
}

#ifndef WGSE_UPSTREAM_GETTIMEOFDAY
int gettimeofday(struct timeval *tv, void *tz)
{
	FILETIME ft;
	uint64_t t;
	(void)tz;
	GetSystemTimePreciseAsFileTime(&ft);
	/* 100 ns intervals since 1601 -> microseconds since 1970. */
	t = ((uint64_t)ft.dwHighDateTime << 32 | ft.dwLowDateTime) / 10 - 11644473600000000ULL;
	tv->tv_sec = (long)(t / 1000000);
	tv->tv_usec = (long)(t % 1000000);
	return 0;
}
#endif

long sysconf(int name)
{
	SYSTEM_INFO info;
	MEMORYSTATUSEX memory;
	GetSystemInfo(&info);
	switch (name) {
	case _SC_PAGESIZE:
		return (long)info.dwPageSize;
	case _SC_NPROCESSORS_ONLN:
		return (long)info.dwNumberOfProcessors;
	case _SC_PHYS_PAGES:
		memory.dwLength = sizeof(memory);
		if (!GlobalMemoryStatusEx(&memory)) return -1;
		return (long)(memory.ullTotalPhys / info.dwPageSize);
	default:
		errno = EINVAL;
		return -1;
	}
}

int getpagesize(void)
{
	return (int)sysconf(_SC_PAGESIZE);
}

struct wgse_thread {
	HANDLE handle;
	void *(*start)(void *);
	void *arg;
	void *result;
	int detached;
};

static __declspec(thread) struct wgse_thread *wgse_self;

static unsigned __stdcall wgse_thread_main(void *p)
{
	struct wgse_thread *t = (struct wgse_thread *)p;
	wgse_self = t;
	t->result = t->start(t->arg);
	if (t->detached) free(t);
	return 0;
}

int pthread_attr_init(pthread_attr_t *attr)
{
	attr->detached = 0;
	return 0;
}

int pthread_attr_destroy(pthread_attr_t *attr)
{
	(void)attr;
	return 0;
}

int pthread_attr_setdetachstate(pthread_attr_t *attr, int state)
{
	attr->detached = state == PTHREAD_CREATE_DETACHED;
	return 0;
}

int pthread_create(pthread_t *thread, const pthread_attr_t *attr, void *(*start)(void *), void *arg)
{
	struct wgse_thread *t = (struct wgse_thread *)calloc(1, sizeof(*t));
	const int detached = attr && attr->detached;
	uintptr_t handle;
	if (!t) return ENOMEM;
	t->start = start, t->arg = arg, t->detached = detached;
	/* Suspended, so that t is complete before the thread can use (or free) it. */
	handle = _beginthreadex(NULL, 0, wgse_thread_main, t, CREATE_SUSPENDED, NULL);
	if (!handle) {
		free(t);
		return EAGAIN;
	}
	t->handle = (HANDLE)handle;
	*thread = t;
	ResumeThread((HANDLE)handle);
	if (detached) CloseHandle((HANDLE)handle);
	return 0;
}

int pthread_join(pthread_t thread, void **result)
{
	if (!thread || thread->detached) return EINVAL;
	if (WaitForSingleObject(thread->handle, INFINITE) != WAIT_OBJECT_0) return EINVAL;
	CloseHandle(thread->handle);
	if (result) *result = thread->result;
	free(thread);
	return 0;
}

void pthread_exit(void *result)
{
	if (wgse_self) {
		wgse_self->result = result;
		if (wgse_self->detached) free(wgse_self);
	}
	_endthreadex(0);
	__builtin_unreachable();
}

int pthread_mutex_init(pthread_mutex_t *mutex, const pthread_mutexattr_t *attr)
{
	(void)attr;
	InitializeSRWLock((PSRWLOCK)mutex);
	return 0;
}

int pthread_mutex_destroy(pthread_mutex_t *mutex)
{
	(void)mutex;
	return 0;
}

int pthread_mutex_lock(pthread_mutex_t *mutex)
{
	AcquireSRWLockExclusive((PSRWLOCK)mutex);
	return 0;
}

int pthread_mutex_unlock(pthread_mutex_t *mutex)
{
	ReleaseSRWLockExclusive((PSRWLOCK)mutex);
	return 0;
}

int pthread_cond_init(pthread_cond_t *cond, const pthread_condattr_t *attr)
{
	(void)attr;
	InitializeConditionVariable((PCONDITION_VARIABLE)cond);
	return 0;
}

int pthread_cond_destroy(pthread_cond_t *cond)
{
	(void)cond;
	return 0;
}

int pthread_cond_wait(pthread_cond_t *cond, pthread_mutex_t *mutex)
{
	return SleepConditionVariableSRW((PCONDITION_VARIABLE)cond, (PSRWLOCK)mutex, INFINITE, 0)
		? 0 : EINVAL;
}

int pthread_cond_signal(pthread_cond_t *cond)
{
	WakeConditionVariable((PCONDITION_VARIABLE)cond);
	return 0;
}

int pthread_cond_broadcast(pthread_cond_t *cond)
{
	WakeAllConditionVariable((PCONDITION_VARIABLE)cond);
	return 0;
}
