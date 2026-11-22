## Fixes

- On Windows, `wgse-bwa` uses Segment Heap to avoid stalling CUDA mate rescue
  while CPU workers wait for heap allocations. Alignment records and the
  CPU-by-default policy are unchanged
  ([#1403, Windows CUDA mate-rescue performance](https://github.com/WGSExtract/wgsextract-cli/issues/1403)).
