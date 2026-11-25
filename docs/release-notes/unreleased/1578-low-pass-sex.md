## Fixes

- Sex inference works again on short-read low-pass genomes below 3x, such as
  2X consumer CRAMs and downsampled subsets, which had been reported as
  `Unknown` so that `microarray`, variant calling and other sex-aware commands
  refused them. Reads are counted in more indexed windows, so a CRAM is still
  not read whole ([#1578, low-pass sex inference](https://github.com/WGSExtract/wgsextract-cli/pull/1578)).
