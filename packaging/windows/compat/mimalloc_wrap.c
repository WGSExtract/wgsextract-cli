/* SPDX-License-Identifier: MIT */
#include <stddef.h>
#include "mimalloc.h"

void __real_free(void *);
void *__real_realloc(void *, size_t);
size_t __real__msize(void *);

void *__wrap_malloc(size_t size) { return mi_malloc(size); }
void *__wrap_calloc(size_t count, size_t size) { return mi_calloc(count, size); }
void *__wrap_realloc(void *ptr, size_t size)
{
    return ptr && !mi_is_in_heap_region(ptr) ? __real_realloc(ptr, size) : mi_realloc(ptr, size);
}
void __wrap_free(void *ptr)
{
    /* CRT functions such as _strdup can return allocations from inside the
     * UCRT DLL. Its private calls are not redirected by our static link. */
    if (mi_is_in_heap_region(ptr)) mi_free(ptr);
    else __real_free(ptr);
}
/* The CRAM buffer cache queries usable capacity before retaining a block. */
size_t __wrap__msize(void *ptr)
{
    return mi_is_in_heap_region(ptr) ? mi_usable_size(ptr) : __real__msize(ptr);
}

/* MinGW emits direct calls and dllimport function-pointer references. */
void *(*__wrap___imp_malloc)(size_t) = __wrap_malloc;
void *(*__wrap___imp_calloc)(size_t, size_t) = __wrap_calloc;
void *(*__wrap___imp_realloc)(void *, size_t) = __wrap_realloc;
void (*__wrap___imp_free)(void *) = __wrap_free;
size_t (*__wrap___imp__msize)(void *) = __wrap__msize;
