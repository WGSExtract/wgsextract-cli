# Offline SCREEN cCRE annotation

Install the opt-in GRCh38 track, then annotate a coordinate-sorted VCF or BCF:

```sh
wgsextract ref regulatory --source screen --build hg38 --download
wgsextract vcf regulatory --input sample.vcf.gz
wgsextract vcf table --input regulatory_annotated.vcf.gz
```

Or include `regulatory` by name in `vcf chain-annotate --annotations
regulatory,vep --to-table` (each step needs its own local data). The chain
does not enable this track by default. Annotation checks the VCF header for
a GRCh38 assembly declaration, matching primary-contig lengths or matching
versioned accessions. Contradictory builds are rejected; there is no liftover
and no reference FASTA requirement.

## Data, privacy and provenance

`--download` requests the public [GRCh38 BED](https://downloads.wenglab.org/Registry-V4/GRCh38-cCREs.bed)
from Weng Lab (~129 MB). No sample data are sent. The exact upstream file is
pinned to SHA-256 `a292aea64edea7efbef275140b78116efdef1bea5b729dc9e5513421dc868dda`.
Credit: the ENCODE Project and [SCREEN cCRE Registry v4](https://screen.wenglab.org/downloads).
The [ENCODE data release policy](https://www.genome.ucsc.edu/ENCODE/terms.html)
allows unrestricted use of released data.

On an offline machine, use `wgsextract ref regulatory --bed GRCh38-cCREs.bed`
instead. The file may be gzip-compressed and must have the six SCREEN v4
columns (chromosome, zero-based start, exclusive end, DHS accession, cCRE
accession, class), with each chromosome grouped and sorted by start. Only
import data you know use native GRCh38 coordinates. Imports replace the
local track; they are recorded as local, not verified upstream releases.
`--download` restores the pinned release over an import.

The reference library's `downloads/` folder holds the BGZF BED, tabix index
and JSON provenance (assembly, release, source and installed hashes). `ref
status` reports the track, `ref needs` identifies a missing install, and `ref
regulatory` alone verifies the local files. Annotation checks the files and
never fetches a missing or damaged track.

## Reading the results

`CCRE_CLASS` and `CCRE_ID` are per-ALT INFO fields (`Number=A`). Each ALT's
classes and accessions are separately sorted and deduplicated and joined by
`|`. A dot marks an ALT without a hit when another ALT at that site has a
hit. `vcf table` exposes the nullable `ccre_class` column. Labels are retained
as published, for example `PLS` (promoter-like), `pELS` (proximal enhancer-like),
`dELS` (distal enhancer-like) and `CA-TF`.

The overlap uses only the changed REF bases, after trimming shared prefixes
and suffixes separately for each ALT. Insertions use the base immediately to
the right of the insertion breakpoint. Symbolic alleles, breakends,
spanning-deletion markers and gVCF reference blocks get no annotation.

These are research annotations. Overlap does not establish a causal effect,
a target gene, tissue-specific activity, a promoter impact score or ACMG
evidence. GTEx eQTLs are not included.
