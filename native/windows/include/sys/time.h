/* struct timeval and gettimeofday() for MSVC-ABI builds (clang-cl). */
#ifndef WGSE_MSVC_SYS_TIME_H
#define WGSE_MSVC_SYS_TIME_H

#include <time.h>

/* winsock.h defines the same struct timeval; whichever comes first wins. */
#if !defined(_WINSOCKAPI_) && !defined(_WINSOCK2API_)
#define _WINSOCKAPI_
struct timeval {
	long tv_sec;
	long tv_usec;
};
#endif

struct timezone {
	int tz_minuteswest;
	int tz_dsttime;
};

#ifdef __cplusplus
extern "C" {
#endif

int gettimeofday(struct timeval *tv, void *tz);

#ifdef __cplusplus
}
#endif
#endif