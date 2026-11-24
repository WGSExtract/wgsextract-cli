## Changed

- Windows x86-64 Pixi installs now include `wgse-minimap2`, so long-read
  `align` and `realign` use it without manual PATH setup. CUDA is used
  automatically when available and the run fits Windows RAM and commit limits.
  Pass `--no-prefer-native-gpu` or set `prefer_native_gpu = false` in
  `config.toml` to force CPU
  ([#1121, native Windows CUDA minimap2](https://github.com/WGSExtract/wgsextract-cli/issues/1121)).
