## New

- On Windows x86-64, `align` and `realign` can use `wgse-minimap2` from `PATH`
  to accelerate long-read alignment on a supported NVIDIA GPU without
  changing the alignment records. The native CPU backend works without an
  NVIDIA driver; `--no-prefer-native-gpu` forces it
  ([#1121, native Windows CUDA minimap2](https://github.com/WGSExtract/wgsextract-cli/issues/1121)).
