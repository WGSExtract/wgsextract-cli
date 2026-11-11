<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- `--aligner auto` on Apple Silicon can now pick bwa-mem2 when
  `wgse-bwa-mem2` 0.5.0 or later is installed, without upstream bwa-mem2 or
  the reference's `.0123` file, using its smaller memory estimate. bwa-mem2's
  records differ from bwa's, so a run that used to align with bwa (or
  `wgse-bwa`) may now give a different BAM; pass `--aligner bwa` to keep bwa.

## Fixes

- Installing hs37d5 from the NIH mirror works again. NIH serves it as RAZF, an
  older indexed gzip that was taken for BGZF, so indexing failed and the install
  fell back to the much slower EBI mirror. It is now recompressed to BGZF.

- `ref bootstrap` no longer downloads the AADR v66.p1 2M SNP panel: the Harvard
  Dataverse server is slow enough to stall setup. Install it when you need it with
  `wgsextract ref aadr --install`, or from the app's Reference Data page. The
  Microarray page's **2M** format stays disabled, with a tooltip, until it is installed.
