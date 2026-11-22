## Fixes

- On Windows, `wgse-bwa` uses Segment Heap to avoid stalling CUDA mate rescue
  while CPU workers wait for heap allocations. `align` and `realign` select
  CUDA automatically when the Windows RAM and commit checks pass. Use
  `--no-prefer-native-gpu` to force CPU. Alignment records are unchanged
  ([#1403, Windows CUDA mate-rescue performance](https://github.com/WGSExtract/wgsextract-cli/issues/1403)).
