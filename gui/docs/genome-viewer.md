# Built-in genome viewer

Open **Genome viewer** in the WGS Extract sidebar to inspect local reads and
variants with the bundled igv.js. There is no separate IGV installation, upload,
online reference lookup or CDN connection.

## Open a file from My Genomes

After choosing an indexed BAM/CRAM or bgzip VCF/gVCF on a tool page, choose
**View reads** beside its path. You can also open **Genome viewer**, enter the
full path under **File in My Genomes**, and choose **Open library file**.
The file must be inside the **My Genomes** folder configured in Settings;
changing a tool's input path does not grant access to another folder.

The viewer reads the header with the installed CLI's samtools or bcftools.
It uses the CLI's local reference-library resolver and verifies the sequence
names and lengths against the installed FASTA index. For headers with sequence
checksums, including CRAM, the installed `.dict` must also match those checksums.
It does not scan the whole reference to create a dictionary. The reference
library and its separately configured genome folder are both supported.
If the exact reference or indexes are missing, install them in
**Reference Genomes** using its explicit download flow, then try again.
No CRAM reference URL is followed and nothing is downloaded automatically.

Enter an optional **1-based, inclusive** position such as
`chr20:1000-2000`; leaving it empty opens the first chromosome. The loaded
reference's filename appears beside the status. The existing browser-file
workflow below is available for files outside the configured libraries.

## Select files manually

1. In **Local reference FASTA and index**, select the installed `.fa`, `.fasta`
   or `.fna` and its matching `.fai` together. BGZF-compressed references
   (`.fa.gz`, `.fasta.gz`, `.fna.gz`) require both `.fai` and `.gzi` indexes,
   named after the complete filename (for example `reference.fa.gz.gzi`).
   If `.gzi` is missing, index the BGZF reference first, then select all three files.
   If missing, return to **Reference Genomes** and install the matching reference
   using its explicit download flow. The viewer never downloads it for you.
   CRAM requires the **exact** reference used for alignment, not just the same
   assembly name.
2. In **Reads, variants and their indexes**, select one or more tracks and indexes
   together:
   - BAM with `sample.bam.bai`, `sample.bai` or `sample.bam.csi`;
   - CRAM with `sample.cram.crai` or `sample.crai`;
   - bgzip-compressed VCF/gVCF (`.vcf.gz`, `.g.vcf.gz`, `.gvcf.gz`, or the
     equivalent `.bgz` suffixes) with its `.tbi` or `.csi`.
3. Enter a chromosome and **1-based, inclusive** interval, such as
   `chr20:1000-2000`, then choose **Open local files**.
4. Drag to pan, use the zoom buttons to inspect bases, or click a read or variant
   for details. Enter another interval in the position box to move.

Files remain on your disk. The browser reads indexed slices directly from the
manually selected files. Library files use authenticated HTTP Range reads from
the app's loopback-only Node backend. Neither workflow copies a whole-genome
BAM/CRAM or loads it whole into memory. A missing reference or index is reported
before opening a track. The viewer file endpoint does not support remote or
phone access.
The viewer opens in a separate in-app pane, so running jobs continue. Its frame
is kept outside the app's command-output renders; returning to the app closes
the selected files without interrupting jobs.

Select only matching references and indexes. Duplicate filenames are rejected;
open same-named files from different folders separately. Leaving or reloading
the viewer closes the selections. Files are not remembered between visits.

## Current limits

This is part of [the built-in viewer work](https://github.com/WGSExtract/wgsextract-cli/issues/1090).
Installed gene/rsID lookups, variant/finding/region deep links, BED and coverage
tracks, and authenticated remote range serving remain follow-up work. Context
menus and external links inside igv are disabled, including BLAT.
Read and coverage popups never query ClinVar or other online services. File
fields, including VCF INFO values, are shown as text, never interpreted as HTML.

For an unexpected variant, inspect reads in this built-in viewer rather than
installing desktop IGV. The interpretation guidance in lessons still applies:
look for coverage loss, strand bias, clipping and low mapping quality; a visual
check is not a clinical diagnosis.

igv.js 3.8.10 is MIT-licensed. Its notice ships alongside the vendored library in
`app/web/vendor/igv/LICENSE`.

## Local file endpoint

An authenticated `POST /api/viewer/open` accepts a library file path and optional
locus. It creates a bounded, idle-expiring grant for that file, its index and
the matching installed reference and indexes. The embedded viewer receives
only that read-only grant, never the main app's API token.
`GET /viewer-data/<session>/<file>` requires the grant's
`x-gfc-viewer-token` header; query-string credentials are not accepted.
Responses use `Cache-Control: no-store`; there is no directory listing or CORS
permission. Both socket endpoints must be loopback addresses, and the normal
Host/Origin checks still apply even if the app itself binds a LAN interface.

Both the requested path and canonical target must remain in the same configured
library root. Traversal and escaping symlinks are refused, each read rechecks
the roots, and replaced or modified files require reopening. A single byte
range returns `206`; malformed, multiple, out-of-bounds or over-32-MiB reads
return `416`. Full requests are limited to 32 MiB for indexes and metadata;
larger data files require bounded ranges. Closing the pane revokes its grants;
unused grants expire after an hour. No sample paths, loci, tokens or byte-read
logs are sent to any external service.
