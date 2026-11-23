## New

- On Windows x86-64, `align` and `realign` can use an optional `wgse-bwa`
  from `PATH` or `wgse_bwa_path` in `config.toml`. Stock BWA remains the
  fallback when the optional build is unavailable. The native build uses
  the CPU by default; `--prefer-native-gpu` opts in to CUDA when Windows
  has enough RAM and commit
  ([#1119, native Windows BWA](https://github.com/WGSExtract/wgsextract-cli/issues/1119)).
