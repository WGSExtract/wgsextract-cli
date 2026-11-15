# wgse-mimalloc

[mimalloc](https://github.com/microsoft/mimalloc) 3.6.1, built with malloc
override on (`-DMI_OVERRIDE=ON`) as a conda package for macOS arm64. On Apple
Silicon, `vcf snp` and `vcf indel` start their bcftools processes with
`DYLD_INSERT_LIBRARIES` pointing at its `lib/wgse-mimalloc/libmimalloc.dylib`:
the same records, about a fifth faster
([hardware.md](../../docs/hardware.md#bcftools-with-mimalloc), #646).
conda-forge's `mimalloc` is built with override off, so it can't replace malloc
when injected.

The recipe's tests check that, injected, the library takes over the C
library's `malloc()` in a Python process, and that bioconda's bcftools 1.24
loads it.

The published [wgse-mimalloc-1 release](https://github.com/WGSExtract/wgsextract-cli/releases/tag/wgse-mimalloc-1)
is pinned by URL and SHA-256 in `pixi.toml` and `pixi.lock`. Pixi installs it
beside bcftools in the default and development environments on Apple Silicon;
no separate allocator installation is needed.

## Building and publishing

The [wgse-mimalloc workflow](../../.github/workflows/mimalloc.yml) builds and
tests the package on every pull request that changes it. To publish it, run the
workflow (Actions > wgse-mimalloc > Run workflow) with a new tag such as
`wgse-mimalloc-2`: it creates a prerelease with the `.conda` file and its
`SHA256SUMS`. Then pin it in `pixi.toml` by URL and SHA-256, as the Windows
tools and vep-rs are, and run `pixi lock`.

To build it locally:

```sh
pixi exec --spec rattler-build==0.77.0 rattler-build build \
  --recipe-dir packaging/mimalloc -m packaging/mimalloc/variants.yaml \
  -c conda-forge -c bioconda --output-dir out/conda --target-platform osx-arm64
```

## License

mimalloc is MIT-licensed, Copyright (c) 2018-2025 Microsoft Corporation, Daan
Leijen. The package installs its license as
`share/licenses/wgse-mimalloc/LICENSE` and carries it in `info/licenses`.
