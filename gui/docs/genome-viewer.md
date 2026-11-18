# Built-in genome viewer

Open **Genome viewer** in the WGS Extract sidebar to inspect local reads and
variants with the bundled igv.js. There is no separate IGV installation, upload,
online reference lookup or CDN connection.

1. In **Local reference FASTA and index**, select the uncompressed `.fa`, `.fasta`
   or `.fna` and its matching `.fai` together from your installed reference library.
   If missing, return to **Reference Genomes** and install the matching reference
   using its explicit download flow. The viewer never downloads it for you.
   CRAM requires the **exact** reference used for alignment, not just the same
   assembly name.
2. In **Reads, variants and their indexes**, select one or more tracks and indexes
   together:
   - BAM with `sample.bam.bai`, `sample.bai` or `sample.bam.csi`;
   - CRAM with `sample.cram.crai` or `sample.crai`;
   - bgzip-compressed VCF/gVCF with its `.tbi` or `.csi`.
3. Enter a chromosome and **1-based, inclusive** interval, such as
   `chr20:1000-2000`, then choose **Open local files**.
4. Drag to pan, use the zoom buttons to inspect bases, or click a read or variant
   for details. Enter another interval in the position box to move.

Files remain on your disk. The browser reads indexed slices directly from the
selected files; it does not copy a whole-genome BAM/CRAM into memory or serve it
over HTTP. A missing reference or index is reported before opening a track.
The local Node backend only serves the app and library code, not your selected
sample files. This desktop-only slice does not introduce a remote file API.
The viewer opens in a separate in-app pane, so running jobs continue. Its frame
is kept outside the app's command-output renders; returning to the app closes
the selected files without interrupting jobs.

Select only matching references and indexes. Duplicate filenames are rejected;
open same-named files from different folders separately. Leaving or reloading
the viewer closes the selections. Files are not remembered between visits.

## Current limits

This is the first desktop slice of [the built-in viewer work](https://github.com/WGSExtract/wgsextract-cli/issues/1090).
Reference selection is manual; automatic header-based reference matching,
installed gene/rsID lookups, variant/finding/region deep links, BED and coverage
tracks, and authenticated remote range serving remain follow-up work. Context
menus and external links inside igv are disabled, including BLAT.

For an unexpected variant, inspect reads in this built-in viewer rather than
installing desktop IGV. The interpretation guidance in lessons still applies:
look for coverage loss, strand bias, clipping and low mapping quality; a visual
check is not a clinical diagnosis.

igv.js 3.8.10 is MIT-licensed. Its notice ships alongside the vendored library in
`app/web/vendor/igv/LICENSE`.
