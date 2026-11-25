## New

- Run lineage follow-ups: `analyze comprehensive` now refuses a VCF filtered
  or annotated from wgsextract's calls of another person's reads, not only
  the calls themselves; each VCF's `artifact` names the reads it was called
  from in `called_from`. `vcf chain-annotate` lists its annotators' runs in
  `run.children`, and a step interrupted by cancelling `analyze` is listed as
  `cancelled`. A server job's steps read manifests and indexes only in the
  server's folders, a crafted index with negative counts is refused, and a
  pipe named like a manifest no longer blocks a run
  ([#1379, run lineage pilot follow-ups](https://github.com/WGSExtract/wgsextract-cli/issues/1379)).
