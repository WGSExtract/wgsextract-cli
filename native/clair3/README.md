# wgse-clair3: streaming Clair3 on Apple MPS

Code-only osx-arm64 package, version 0.1.0, based on Clair3 2.0.5
(`ca47c1f3e004524ab466857b59df280c8b8cd7ce`). CPU remains the default.
`vcf clair3 --gpu` selects MPS only on Apple Silicon and refuses before
processing reads if the patched package or PyTorch MPS is unavailable.
There is no automatic installation, download, or CPU fallback.

The patch propagates `--device=mps` through both Python pipelines,
`CallVarBam`, `CallVariantsFromCffi` and `CallVariants`. It deliberately
keeps `--use_gpu=False`: upstream's `True` selects CUDA's disk-tensor pipeline
and different high-depth handling. FP32 prediction, CPU feature generation,
streaming and high-depth scaling are unchanged. CUDA's existing Docker path
is untouched. gVCF uses the patched Python/pipe caller too.

## Packaging and release

The recipe in `packaging/wgse-clair3` installs upstream Python caller code in
`share/wgse-clair3`, without overwriting Bioconda's caller. Its pinned
`clair3=2.0.5=py311h9aa1f4a_0` dependency supplies the existing native CFFI
library and tools through relative links. The launcher fixes worker Python to
its own dependency environment. `wgse-clair3 --wgse-device-check` reports
the streaming-MPS protocol and MPS availability as JSON.

Build explicitly (network access may be needed for build inputs):

```sh
pixi exec --spec rattler-build==0.77.0 -- rattler-build build \
  --no-build-id -c conda-forge -c bioconda \
  --recipe-dir packaging/wgse-clair3 --target-platform osx-arm64 \
  --output-dir out/clair3-conda
```

For local development, add the resulting `.conda` explicitly with
`pixi add --feature clair3 --platform osx-arm64 /absolute/path/to/package.conda`,
then `pixi install -e clair3`. Do not commit the local URL or its lockfile.

**Publication gate:** the package is not yet a released dependency. An owner
must dispatch **macOS Clair3** with a new `macos-clair3-*` release tag,
verify the artifact and SHA-256, then add a URL/SHA-256 dependency for
`wgse-clair3` under `[feature.clair3.target.osx-arm64.dependencies]` in
`pixi.toml` and regenerate `pixi.lock`. Until then the released environment
remains CPU-only; `--gpu` clearly names the missing package. Never pin a
nonexistent release or make a normal invocation fetch the package.
If the new workflow is not yet on the default branch, an owner can instead
publish the verified local `.conda` to a prerelease targeting its reviewed
source commit, record its SHA-256, then perform the same URL pin and relock.
Do not upload the build output directory: it contains upstream source and
dependency/test-environment metadata, not just the code-only release asset.

Clair3 code is BSD-3-Clause; `LICENSE.md` is retained in the package and
its conda license metadata. WGSE integration is GPL-3.0-or-later, with the
repository licence retained as `WGSE-LICENSE`.
**No model weights are included or downloaded by
this recipe/workflow.** ONT Rerio weights have a research-only licence;
Clair3's code licence does not grant permission to redistribute them.
Models are selected and checked through the existing `--model`,
`clair3_model`, metadata and Bioconda bundled-model resolution, unchanged.
Do not upload dependency packages or a resolved environment with this artifact.

## Qualification

See [ONT calling](../../docs/ont.md#clair3-optional) and
[resource estimates](../../docs/resource_estimates.md#clair3-on-the-gpu).
Whole-genome qualification remains open in #1101 (Clair3 MPS acceleration);
slice agreement is not a whole-genome speed or accuracy claim.
