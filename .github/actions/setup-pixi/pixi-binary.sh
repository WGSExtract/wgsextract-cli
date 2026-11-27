#!/usr/bin/env bash
set -euo pipefail

# SHA-256 digests of the release's standalone executables, not the archives.
version=v0.82.0
case "$RUNNER_OS/$RUNNER_ARCH" in
  Linux/X64) spec='x86_64-unknown-linux-musl 1dff1db704884f078c44b4c7fb3c5bc6f946fc3d1fa679edf214ad79187a9c64' ;;
  Linux/ARM64) spec='aarch64-unknown-linux-musl c6e3c4d98bb41560b5cfdebbe6cbf766f3547603a0fb96e1450a6ccff74b5e14' ;;
  macOS/X64) spec='x86_64-apple-darwin db4bd6dc3a5f88f2d94cf2e1f8a377b53d3aaac95b45b058854fbf53919d6d36' ;;
  macOS/ARM64) spec='aarch64-apple-darwin 92aa551c67a56ec0ba105fa20c3f48094b02586de523e24b96974ba1f092455b' ;;
  Windows/X64) spec='x86_64-pc-windows-msvc.exe 89c7455923dac401c1853310894d87fa06602911b838e389a46c7bc8df00171e' ;;
  Windows/ARM64) spec='aarch64-pc-windows-msvc.exe d205d144618bdc83e25dba1a10c8cff8f39e9da85c06e142a9ec0d0c8f6d3559' ;;
  *) echo "::error::No pinned Pixi executable for $RUNNER_OS/$RUNNER_ARCH"; exit 2 ;;
esac
read -r target sha256 <<< "$spec"
executable=pixi
if [ "$RUNNER_OS" = Windows ]; then executable=pixi.exe; fi

case "$1" in
  plan)
    {
      echo "executable=$executable"
      echo "key=ci-pixi-binary-$version-$target-$sha256"
    } >> "$GITHUB_OUTPUT"
    ;;
  install)
    bin="$RUNNER_TEMP/ci-pixi"
    if [ "$RUNNER_OS" = Windows ]; then bin=$(cygpath -u "$bin"); fi
    mkdir -p "$bin"
    # shellcheck source-path=SCRIPTDIR
    source "$(dirname "$0")/fetch-pinned.sh"
    fetch_pinned "https://github.com/prefix-dev/pixi/releases/download/$version/pixi-$target" \
      "$sha256" "$bin/$executable"
    chmod +x "$bin/$executable"
    if [ "$RUNNER_OS" = Windows ]; then bin=$(cygpath -aw "$bin"); fi
    echo "$bin" >> "$GITHUB_PATH"
    ;;
  *) exit 2 ;;
esac
