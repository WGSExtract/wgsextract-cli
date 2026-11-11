<!-- Notes for the next release. PRs add user-facing entries here as they
land; the release PR renames this file to vX.Y.Z.md (see docs/releasing.md). -->

## New

- `qc ancestry` estimates admixture-style ancestry proportions from a BAM/CRAM,
  VCF or microarray raw-data file: 7 continental and 12 sub-continental
  reference components learned from the HGDP and 1000 Genomes samples in
  gnomAD, each with a 95% interval. They are proportions of reference
  populations, not an ethnicity. The personal report shows them. The 16 MB
  reference is downloaded on first use (`ref ancestry --install` fetches it
  ahead). See [docs/ancestry.md](../ancestry.md).

## Fixes

- `ref bootstrap` no longer downloads the AADR v66.p1 2M SNP panel: the Harvard
  Dataverse server is slow enough to stall setup. Install it when you need it with
  `wgsextract ref aadr --install`, or from the app's Reference Data page. The
  Microarray page's **2M** format stays disabled, with a tooltip, until it is installed.
