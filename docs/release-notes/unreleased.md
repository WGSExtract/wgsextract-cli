<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

## Fixes

- `ref bootstrap` no longer downloads the AADR v66.p1 2M SNP panel: the Harvard
  Dataverse server is slow enough to stall setup. Install it when you need it with
  `wgsextract ref aadr --install`, or from the app's Reference Data page. The
  Microarray page's **2M** format stays disabled, with a tooltip, until it is installed.
