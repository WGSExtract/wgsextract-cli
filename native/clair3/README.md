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
is untouched. Both CFFI streaming and the Python/pipe caller support gVCF.
MPS uses models from its selected package environment and its verified Clair3
version for CRAM compatibility, independently of any CPU launcher on `PATH`.
Missing or unusable packages report `missing_dependency` (exit 3); unavailable
MPS hardware remains a separate failure.
The MPS package also finalizes candidate-free pileup exits through Clair3's
normal merge/gVCF code, rather than copying diploid calls or losing reference
blocks before haploid handling runs.

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

**Releases.** The pinned build is
[`macos-clair3-2`](https://github.com/WGSExtract/wgsextract-cli/releases/tag/macos-clair3-2),
pinned by URL and SHA-256 under `[feature.clair3.target.osx-arm64.dependencies]`
in `pixi.toml`, so `pixi install -e clair3` installs it alongside Bioconda's
Clair3 on Apple Silicon. It was built locally from the reviewed source commit
named in its release notes, because this workflow was not yet on the default
branch. Build 2 adds interpreter isolation, real synthetic-model streaming
regressions and candidate-free MPS ploidy/gVCF finalization. Its downloaded
asset was verified as SHA-256
`c7235628d6f74acf547df475f7996211c8bb9cec8f9d015a61df9b2f39ced674`;
the previous `macos-clair3-1` release remains available.
For later releases, dispatch **macOS Clair3** with a new
`macos-clair3-*` tag (or publish a verified local build to a prerelease
targeting its reviewed source commit), verify the artifact and SHA-256, update
the URL/SHA-256 pin and regenerate `pixi.lock`. Never pin a nonexistent release
or make a normal invocation fetch the package. Upload only the `.conda` file,
not the build output directory: that contains upstream source and
dependency/test-environment metadata.

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
Recipe tests run the installed launcher on synthetic reads and synthetic
PyTorch checkpoints through CFFI streaming and the Python/pipe gVCF pipeline.
Only MPS availability and device transfers/checkpoint placement are redirected
to CPU; feature generation, inference, worker dispatch and final VCF/gVCF
generation are real. They check model-device propagation and haploid output;
these artificial checkpoints are not an accuracy qualification.
Whole-genome qualification remains open in #1101 (Clair3 MPS acceleration);
slice agreement is not a whole-genome speed or accuracy claim.
