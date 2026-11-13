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

After merging, dispatch the workflow from `main` with a new release tag such
as `glimpse2-1`. It publishes the tested win-64 package and the MSYS2 package
inventory as a prerelease, with SHA-256 in the release notes.

In a follow-up PR, add a `[feature.imputation.target.win-64.dependencies]`
`glimpse-bio` URL plus SHA-256 pin for that asset, and regenerate `pixi.lock`.
Until then, `pixi install -e imputation` on Windows provides Beagle/Java but
not GLIMPSE2; panel preparation and reads imputation require manually supplied
GLIMPSE2 tools. Runtime commands validate dependencies and never install them.
