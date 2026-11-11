<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

## Fixes

- Installing hs37d5 from the NIH mirror works again. NIH serves it as RAZF, an
  older indexed gzip that was taken for BGZF, so indexing failed and the install
  fell back to the much slower EBI mirror. It is now recompressed to BGZF.

- `ref bootstrap` no longer downloads the AADR v66.p1 2M SNP panel: the Harvard
  Dataverse server is slow enough to stall setup. Install it when you need it with
  `wgsextract ref aadr --install`, or from the app's Reference Data page. The
  Microarray page's **2M** format stays disabled, with a tooltip, until it is installed.
