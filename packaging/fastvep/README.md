# fastVEP packages

The recipe builds the CLI from Huang-lab/fastVEP **v0.5.0** (the validated
classifier series), not the web server. No annotation databases are included.
The tagged source is Apache-2.0: redistribution in source and binary form is
permitted under section 4. The package includes `LICENSE.md` and the dependency
licence manifest `THIRDPARTY.yml` produced by `cargo-bundle-licenses`.

The `fastVEP` workflow builds and runs an offline database-build/ACMG VCF smoke
test on Linux x86-64/ARM64, macOS Intel/Apple Silicon, and Windows x86-64.
`build.sh` and `build.bat` retain the conda compiler's portable CPU baseline.

To publish, dispatch `.github/workflows/fastvep.yml` on the packaging branch,
passing a **new** `fastvep-N` release tag. The shared
`publish-native-release` action publishes a prerelease only after every build
and package test passes, and records the source commit, workflow run and each
asset's SHA-256. Pin those URLs and hashes in `pixi.toml` and regenerate
`pixi.lock`. Never replace an existing release's assets.
