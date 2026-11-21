"""Regressions for the stale OpenMP registration abort (#1257)."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.macos_openmp import sweep_unwritten_openmp_registrations

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS OpenMP startup")


def test_1257_sweep_only_removes_our_unwritten_dead_pid_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def marker(pid: int) -> Path:
        return tmp_path / f"__KMP_REGISTERED_LIB_{pid}"

    def reaped_pid() -> int:
        with subprocess.Popen([sys.executable, "-c", "pass"]) as child:
            assert child.wait(timeout=10) == 0
        return child.pid

    empty, zeroed, written, link = (marker(reaped_pid()) for _ in range(4))
    live = marker(os.getpid())
    empty.touch()
    zeroed.write_bytes(bytes(1024))
    written.write_bytes(b"0x1234-aabb-libomp.dylib\0".ljust(1024, b"\0"))
    live.touch()
    link.symlink_to(live)

    with monkeypatch.context() as foreign_uid:
        # Simulate ownership by another UID without requiring elevated privileges.
        uid = os.getuid()
        foreign_uid.setattr(os, "getuid", lambda: uid + 1)
        sweep_unwritten_openmp_registrations(tmp_path)
    assert empty.exists() and zeroed.exists()

    sweep_unwritten_openmp_registrations(tmp_path)
    assert not empty.exists() and not zeroed.exists()
    assert live.exists() and written.exists() and link.is_symlink()


@pytest.mark.parametrize("environment", ["dev", "pharmcat"])
def test_1257_pixi_bcftools_does_not_load_openmp(environment: str) -> None:
    prefix = Path(sys.prefix)
    if prefix.name != "dev" or not (prefix / "conda-meta" / "history").is_file():
        pytest.skip("requires the Pixi dev environment")
    executable = prefix.with_name(environment) / "bin" / "bcftools"
    if not executable.is_file():
        pytest.skip(f"the optional Pixi {environment} environment is not installed")

    env = {
        key: value for key, value in os.environ.items() if not key.startswith("DYLD_")
    }
    completed = subprocess.run(
        [executable, "--version"],
        env={**env, "DYLD_PRINT_LIBRARIES": "1"},
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert str(executable.resolve()) in completed.stderr, (
        "expected a bcftools dyld trace"
    )
    assert not re.search(r"/lib(?:omp|iomp5|gomp)[\d.]*\.dylib\b", completed.stderr)
