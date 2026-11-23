## Changed

- Windows x86-64 Pixi installs now include `wgse-bwa`, so no manual PATH or
  `wgse_bwa_path` setup is needed to use it for BWA alignment. CUDA is used
  automatically when available and the run fits Windows RAM and commit limits.
  Pass `--no-prefer-native-gpu` or set `prefer_native_gpu = false` in
  `config.toml` to force CPU.
