#!/usr/bin/env bash
set -euxo pipefail
# Use the conda compiler's portable baseline, not host-specific CPU features.
rm -f .cargo/config.toml
export CARGO_PROFILE_RELEASE_STRIP=none
cargo-bundle-licenses --format yaml --output THIRDPARTY.yml
cargo build --release --locked --bin fastvep
out="target/${CARGO_BUILD_TARGET:+$CARGO_BUILD_TARGET/}release"
mkdir -p "$PREFIX/bin"
install -m 755 "$out/fastvep" "$PREFIX/bin/fastvep"
