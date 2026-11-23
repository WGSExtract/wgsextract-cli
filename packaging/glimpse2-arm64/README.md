# Native ARM64 GLIMPSE2

Bioconda's `glimpse-bio` 2.0.1 is x86-64 only. This recipe builds the same
upstream [v2.0.1](https://github.com/odelaneau/GLIMPSE/tree/v2.0.1) source
(commit `88915f7fdc38ee841a97b1a957f0becf120cc1b4`) for `osx-arm64` and
`linux-aarch64`. The recipe verifies the source archive's SHA-256 and packages
upstream's MIT license. Clang 21 and SIMDe 0.8.2 lower the phase program's
AVX2/FMA intrinsics to ARM NEON without changing its HMM or RNG.

The [Native GLIMPSE2](../../.github/workflows/glimpse2.yml) workflow checks
the executables and runs the real HG002 chr20 fixture on both architectures
before publishing. Dispatch it with `target=arm64` and a new
`glimpse2-arm64-N` release tag. The release records the build commit and
each asset's SHA-256; update the two URLs and checksums in `pixi.toml` and
refresh `pixi.lock` with the repository's pinned Pixi. Windows keeps the
separate [existing recipe](../glimpse2/README.md).
