"""Cross-platform launchers for stand-in tool executables."""

import os
import shutil
import sys
from pathlib import Path

import pytest

HOST_IS_WINDOWS = os.name == "nt"


def require_bare_name_fakes() -> None:
    """Skip on Windows, where bare tool names cannot resolve .cmd fakes."""
    if HOST_IS_WINDOWS:
        pytest.skip("a .cmd fake can't stand in for a tool started by bare name")


def only_tools(request: pytest.FixtureRequest, *tools: str) -> Path:
    """Leave PATH with real ``tools`` and a pixi without environments."""
    if HOST_IS_WINDOWS:
        pytest.skip("links the real tools into a bin directory")
    paths = {tool: shutil.which(tool) for tool in tools}
    bindir: Path = request.getfixturevalue("fake_pixi").host_bin
    for tool, path in paths.items():
        if not path:
            pytest.skip(f"{tool} missing")
        os.symlink(path, bindir / tool)
        # bwa-mem2's Linux build runs bwa-mem2.<SIMD level> from beside itself.
        for variant in Path(path).parent.glob(f"{tool}.*"):
            if variant.name not in paths and not (bindir / variant.name).exists():
                os.symlink(variant, bindir / variant.name)
    return bindir


def expose_tool_fakes(
    monkeypatch: pytest.MonkeyPatch, bindir: Path, *tools: str
) -> None:
    """Expose fakes for callers that resolve a filename before launching it."""
    if HOST_IS_WINDOWS:
        pathext = os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD"
        if ".CMD" not in pathext.upper().split(os.pathsep):
            pathext += f"{os.pathsep}.CMD"
        monkeypatch.setenv("PATHEXT", pathext)
    monkeypatch.setenv("PATH", str(bindir), prepend=os.pathsep)
    for tool in tools:
        resolved = shutil.which(tool)
        assert resolved and Path(resolved).parent == bindir, (
            f"could not find fake {tool}"
        )


def python_tool(bindir: Path, name: str, source: str) -> Path:
    """Write an executable Python tool, with a .cmd launcher on Windows."""
    bindir.mkdir(parents=True, exist_ok=True)
    if not HOST_IS_WINDOWS:
        path = bindir / name
        path.write_text(f"#!{sys.executable}\n{source}")
        path.chmod(0o755)
        return path
    (bindir / f"{name}.py").write_text(source)
    launcher = bindir / f"{name}.cmd"
    launcher.write_text(f'@"{sys.executable}" "%~dp0{name}.py" %*\r\n', newline="")
    return launcher
