"""Pin the current process to one CPU for throughput measurements.

On hybrid CPUs (performance + efficiency cores) a single-threaded benchmark can land on
either kind of core, and the numbers swing by 2-3x. Timed runs pin themselves to
`TRIPWIRE_BENCH_CPU` (default 0, a performance core on common Intel hybrid layouts).
"""

from __future__ import annotations

import ctypes
import os
import sys


def pin_to_cpu(cpu: int | None = None) -> int | None:
    """Pin this process to one logical CPU. Returns the CPU, or None if unsupported."""
    if cpu is None:
        cpu = int(os.environ.get("TRIPWIRE_BENCH_CPU", "0"))
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetProcessAffinityMask.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
        kernel32.SetProcessAffinityMask.restype = ctypes.c_int
        ok = kernel32.SetProcessAffinityMask(kernel32.GetCurrentProcess(), 1 << cpu)
        return cpu if ok else None
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, {cpu})
        return cpu
    return None
