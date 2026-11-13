# Native Windows GLIMPSE2

`recipe.yaml` builds upstream GLIMPSE **v2.0.1** (commit
`88915f7fdc38ee841a97b1a957f0becf120cc1b4`), matching Bioconda's
`glimpse-bio==2.0.1`. It uses MSYS2 UCRT64 GCC, static Boost and the same
patched HTSlib as our native Windows tools. The shared build helper checks
that each executable imports only Windows system DLLs. Phase requires AVX2/FMA,
as upstream does; the other tools use x86-64-v2.

The **GLIMPSE2 Windows** workflow builds the conda package, runs every tool's
`--help`, then installs it into a disposable CI checkout's Pixi environment.
Outside MSYS2, the HG002 fixture tests prepare several reference chunks, phase
the 1x BAM, ligate the chunks and check genotype concordance and output labels.
The Beagle fixture tests also exercise Windows OpenJDK and the pinned noarch jar.
No synthetic or duplicated fixture is needed.

To publish an update, dispatch the workflow from `main` with a fresh release tag.
It publishes the tested win-64 package and the MSYS2 package
inventory as a prerelease, with SHA-256 in the release notes.

`pixi.toml` currently pins `glimpse2-1` under
`[feature.imputation.target.win-64.dependencies]` by URL and SHA-256.
For a new release, update that pin and refresh only the imputation environment
in `pixi.lock`. CI Windows validates a fresh install of the published pin and
runs the complete HG002 imputation suite without skips. The build workflow
overrides the win-64 pin locally to test its newly built candidate instead.
Runtime commands validate dependencies and never install them.
