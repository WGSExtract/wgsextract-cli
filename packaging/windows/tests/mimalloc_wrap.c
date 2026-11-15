#include <assert.h>
#include <malloc.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "mimalloc.h"

typedef void *(__cdecl *malloc_fn)(size_t);
static malloc_fn crt_malloc;

static DWORD WINAPI allocations(void *handoff)
{
    void *direct = malloc(64);
    assert(direct && mi_is_in_heap_region(direct));
    free(direct);
    for (size_t size = 1; size < 1048576; size = size * 2 + 1) {
        unsigned char *p = calloc(size, 1);
        assert(p && mi_is_in_heap_region(p));
        assert(_msize(p) >= size);
        for (size_t i = 0; i < size; ++i) assert(!p[i]);
        memset(p, 0x7d, size);
        p = realloc(p, size * 2);
        assert(p && mi_is_in_heap_region(p));
        for (size_t i = 0; i < size; ++i) assert(p[i] == 0x7d);
        free(p);

        p = crt_malloc(size);
        assert(p && !mi_is_in_heap_region(p));
        assert(_msize(p) >= size);
        memset(p, 0x19, size);
        p = realloc(p, size * 2);
        assert(p);
        for (size_t i = 0; i < size; ++i) assert(p[i] == 0x19);
        free(p);

        char *str = _strdup("UCRT-owned string");
        assert(str && !strcmp(str, "UCRT-owned string"));
        free(str);
    }
    unsigned char *last = malloc(65537);
    assert(last && mi_is_in_heap_region(last));
    assert(_msize(last) >= 65537);
    memset(last, 0x35, 65537);
    *(void **)handoff = last;
    return 0;
}

int main(void)
{
    SetErrorMode(SEM_NOGPFAULTERRORBOX | SEM_FAILCRITICALERRORS);
    crt_malloc = (malloc_fn)GetProcAddress(GetModuleHandleA("ucrtbase.dll"), "malloc");
    assert(crt_malloc);
    HANDLE workers[8];
    void *handoffs[8] = {0};
    for (int i = 0; i < 8; ++i) {
        workers[i] = CreateThread(NULL, 0, allocations, &handoffs[i], 0, NULL);
        assert(workers[i]);
    }
    assert(WaitForMultipleObjects(8, workers, TRUE, INFINITE) == WAIT_OBJECT_0);
    for (int i = 0; i < 8; ++i) {
        DWORD status = 1;
        assert(GetExitCodeThread(workers[i], &status) && !status);
        unsigned char *last = handoffs[i];
        assert(last && mi_is_in_heap_region(last));
        for (size_t j = 0; j < 65537; ++j) assert(last[j] == 0x35);
        free(last);
        CloseHandle(workers[i]);
    }
    return 0;
}
