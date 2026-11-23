/* The POSIX threads BWA uses (threads, mutexes and condition variables), on
 * Win32's threads, slim reader/writer locks and condition variables, for
 * MSVC-ABI builds (clang-cl). Implementations are in ../msvc_compat.c. */
#ifndef WGSE_MSVC_PTHREAD_H
#define WGSE_MSVC_PTHREAD_H

#ifdef __cplusplus
extern "C" {
#endif

typedef struct wgse_thread *pthread_t;
typedef struct {
	int detached;
} pthread_attr_t;
/* The size and layout of Win32's SRWLOCK and CONDITION_VARIABLE. */
typedef struct {
	void *lock;
} pthread_mutex_t;
typedef struct {
	void *cond;
} pthread_cond_t;
typedef int pthread_mutexattr_t;
typedef int pthread_condattr_t;

#define PTHREAD_CREATE_JOINABLE 0
#define PTHREAD_CREATE_DETACHED 1
#define PTHREAD_MUTEX_INITIALIZER {0}
#define PTHREAD_COND_INITIALIZER {0}

int pthread_attr_init(pthread_attr_t *attr);
int pthread_attr_destroy(pthread_attr_t *attr);
int pthread_attr_setdetachstate(pthread_attr_t *attr, int state);
int pthread_create(pthread_t *thread, const pthread_attr_t *attr,
		   void *(*start)(void *), void *arg);
int pthread_join(pthread_t thread, void **result);
__declspec(noreturn) void pthread_exit(void *result);

int pthread_mutex_init(pthread_mutex_t *mutex, const pthread_mutexattr_t *attr);
int pthread_mutex_destroy(pthread_mutex_t *mutex);
int pthread_mutex_lock(pthread_mutex_t *mutex);
int pthread_mutex_unlock(pthread_mutex_t *mutex);

int pthread_cond_init(pthread_cond_t *cond, const pthread_condattr_t *attr);
int pthread_cond_destroy(pthread_cond_t *cond);
int pthread_cond_wait(pthread_cond_t *cond, pthread_mutex_t *mutex);
int pthread_cond_signal(pthread_cond_t *cond);
int pthread_cond_broadcast(pthread_cond_t *cond);

#ifdef __cplusplus
}
#endif
#endif
