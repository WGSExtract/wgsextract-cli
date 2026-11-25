## Changed

- The default output folder beside a lab's BAM, CRAM, FASTQ, VCF or
  microarray file is now `WGSEv6/` (was `wgse/`). Inputs already inside an
  older `wgse/` folder keep writing there, so existing file sets don't get a
  nested folder.
- Run manifests (`*.manifest.json`) and `info` caches (`*.wgse_info.json`)
  now go in a `manifests/` subfolder of the output folder, and never in the
  lab's own folder: `bam index` still writes its `.bai` beside the BAM, but
  its manifest goes to `WGSEv6/manifests/`. `info` without `--outdir` caches
  its results there too, instead of beside the input. Manifests and caches
  written beside files by earlier releases are still read.

## Fixes

- `info` reports a single-end library's insert size as its read length
  instead of 0 bp, and refreshes caches that stored 0 bp.
