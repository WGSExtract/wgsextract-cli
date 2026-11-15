"""Check that LIB, injected with DYLD_INSERT_LIBRARIES, takes over malloc.

A block from the C library's malloc() must come from mimalloc's heap when the
library is injected, and must not when it isn't (so the check can fail).
"""

import os
import subprocess
import sys

PROBE = """
import ctypes, sys
libc = ctypes.CDLL(None)
libc.malloc.restype = ctypes.c_void_p
block = libc.malloc(64)
mi = ctypes.CDLL(sys.argv[1])
mi.mi_is_in_heap_region.argtypes = [ctypes.c_void_p]
mi.mi_is_in_heap_region.restype = ctypes.c_bool
print(mi.mi_is_in_heap_region(block))
"""


def owned(lib: str, inject: bool) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD_")}
    if inject:
        env["DYLD_INSERT_LIBRARIES"] = lib
    run = subprocess.run(
        [sys.executable, "-c", PROBE, lib],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return run.stdout.strip()


lib = sys.argv[1]
injected, plain = owned(lib, True), owned(lib, False)
print(f"malloc from mimalloc: injected={injected} plain={plain}")
if (injected, plain) != ("True", "False"):
    sys.exit("mimalloc did not take over malloc when injected")
