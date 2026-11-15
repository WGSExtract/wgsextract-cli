#!/usr/bin/env bash
# Portable CPU baseline: upstream's .cargo/config.toml targets x86-64-v3,
# neoverse-v1 and apple-m1, which would refuse to run on older CPUs.
set -euxo pipefail
rm -f .cargo/config.toml
cargo build --release --locked --bin vep --bin vep-cache-builder
# conda's Rust sets CARGO_BUILD_TARGET, which moves the output folder.
out="target/${CARGO_BUILD_TARGET:+$CARGO_BUILD_TARGET/}release"
mkdir -p "$PREFIX/bin"
install -m 755 "$out/vep" "$PREFIX/bin/vep-rs"
install -m 755 "$out/vep-cache-builder" "$PREFIX/bin/vep-cache-builder"
