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

### Add panels, regions or coverage

In **Optional panels and coverage**, enter one full file path per line, then
choose **Open library file**. Put these files in the configured **My Genomes**
folder too. You can add up to 16 tracks alongside the BAM/CRAM/VCF that selects
the reference. To remove a track, delete its path and reopen.

- Use plain `.bed` for panels/regions and `.bedgraph` for coverage or methylation
  values. Plain text tracks have a 32 MiB limit and load in full.
- For larger BED/bedGraph tracks, use BGZF `.bed.gz` or `.bedgraph.gz` with an
  adjacent `.tbi` or `.csi` index. Sort by chromosome and start, compress with
  `bgzip`, then run `tabix -p bed file.bed.gz` (add `-C` for CSI).
  Use the BED preset for bedGraph too: both use zero-based, half-open intervals.
  Ordinary gzip, `.bgz` and other compression suffixes are unsupported for these
  supplemental tracks. Prepare them before opening; the viewer does not install
  tools or create indexes.
- Use `.bw` or `.bigwig` for range-indexed coverage/methylation. Do not compress
  bigWig with gzip or add a tabix index.

Match chromosome names to the loaded FASTA (`chr1` and `1` differ). For bigWig,
match chromosome lengths too. The app checks all plain-text records and the
contig tables in tabix/bigWig indexes before granting access. It cannot verify
the assembly identity of a BED/bedGraph from chromosome names alone: select
tracks from the same assembly and rebuild indexes after editing their data.
Keep BED/bedGraph intervals within the reference lengths and use finite numeric
bedGraph values. You inspect these tracks through the same authenticated,
loopback-only file grant as the primary reads/variants. Nothing leaves this
computer.

## Installed gene spans and symbol navigation

Library files also show the installed RefSeq gene-map for hg19 or hg38. Install
the tables explicitly with `wgsextract ref gene-map`, then reopen the library
file. The viewer reads `ref/genes_hg19.tsv` or `ref/genes_hg38.tsv` in the configured
reference library; the CLI's `microarray/` location is also supported. Missing
or invalid tables produce a notice with that command, without hiding the reads.

In **Position or RefSeq gene symbol**, enter an exact symbol such as `BRCA1`
and choose **Go**. Symbol matching ignores case. Each table loads once per open,
within an 8 MiB/100,000-row limit, and search uses an in-memory lookup.
Typing and searching do not scan files or contact public services.
The track shows each gene's combined span, without exon or transcript models.

The reference's chromosome 1 length must identify hg19 or hg38 unambiguously.
Gene-map coordinates are 1-based inclusive; the viewer converts them for drawing.
Primary chromosome aliases (`chr1`/`1`, including X/Y and mitochondrial names)
are accepted only when one name matches the reference. Absent or ambiguous
contigs are reported and excluded from both drawing and search. Out-of-bounds
coordinates disable the gene track with a mismatch notice. Manual file selection
does not load installed gene maps; coordinate navigation remains available.

## Installed cytobands

Library files show a chromosome ideogram when their hg19/hg38 cytoband
resource is installed. Run `wgsextract ref cytobands --build hg19 --download`
or `wgsextract ref cytobands --build hg38 --download` using the same settings
file as the desktop, then reopen the file. The missing-data notice names the
required table and this exact command. Reads and coordinate navigation remain
available without an ideogram.

Only `ref/cytobands_hg19.tsv` or `ref/cytobands_hg38.tsv` under the configured
reference library is read. Tables load once per open within a 1 MiB/10,000-row
limit. Malformed, truncated, overlapping or incomplete tables are rejected.
The chromosome 1 length selects the build; `chr`/no-`chr` primary aliases must
match unambiguously, and bands must cover the full loaded chromosome length.
Absent/ambiguous chromosomes are reported and omitted. Manual file selection
does not load installed cytobands.

Validated bands reach igv's native ideogram through an in-memory chromosome
source, not a network URL or file grant. Whole-genome mode remains disabled.
The viewer performs no downloads, public lookup or
remote cytoband requests. For UCSC data terms, see
[offline cytobands](../../docs/reference-genomes.md#offline-cytobands).

## Open an extraction result

After a run, choose **Show in viewer** beside an existing BAM, CRAM or indexed
VCF/gVCF in the result summary. For **Extract custom**, a single coordinate
interval, chromosome name or position opens at the extracted locus. A chromosome
name covers its header length; `chr1:1000` covers base 1000 through the chromosome
end, matching samtools. Use `chr1:1000-1000` for one base. BED selections and multiple intervals
open at the reference's first chromosome; enter the interval you want to inspect.
The output must still lie inside the configured My Genomes library.

To check an unexpected variant, open its VCF or the aligned BAM/CRAM and enter
`chromosome:position-position`. Zoom out to inspect nearby reads and coverage.
Check both strands, mapping quality and clipping before interpreting a call.
The viewer does not query ClinVar when you click a read.

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

Locus jumps unsubscribe obsolete ruler-sweep listeners so previous igv viewports
and their decoded-read/canvas references can be collected. In-flight reads may
finish before collection. The pinned igv.js 3.8.10 build contains a local repair
for this upstream lifecycle bug; it does not disable live ruler navigation or
evict the active locus. Memory still depends
on the current locus's read depth and igv's bounded decoder/reference caches,
not the number of earlier jumps.

Developers can check repeated BAM/CRAM jumps in an installed offline Chromium
with `node gui/app/scripts/verify-viewer-offline.mjs --memory --out out/viewer-memory
--chromium /usr/bin/chromium` from the repository root. This uses tiny synthetic
genomes and installed samtools/bcftools; it downloads nothing.

rsID lookups, individual finding/haplogroup
links, and authenticated remote range serving remain
[follow-up work](https://github.com/WGSExtract/wgsextract-cli/issues/1148). Context
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

An authenticated `POST /api/viewer/open` accepts a library file `path`, optional
`locus` and an optional `tracks` array (up to 16 supplemental local paths).
It creates a bounded, idle-expiring grant for those files, their indexes and
the matching installed reference and indexes. The embedded viewer receives
only that read-only grant, never the main app's API token.
The response also contains bounded, validated installed gene and cytoband
features and notices; these tables receive no file grants and need no extra
lookup endpoint.
`GET /viewer-data/<session>/<file>` requires the grant's
`x-gfc-viewer-token` header; query-string credentials are not accepted.
Responses use `Cache-Control: no-store`; there is no directory listing or CORS
permission. Both socket endpoints must be loopback addresses, and the normal
Host/Origin checks still apply even if the app itself binds a LAN interface.

Both the requested path and canonical target must remain in the same configured
library root. Traversal and escaping symlinks are refused, each read rechecks
the roots, and replaced or modified files require reopening. A single byte
range returns `206`; malformed, multiple or out-of-bounds reads return `416`.
Full, suffix and open-ended requests are limited to 32 MiB for indexes and
metadata. Explicit `bytes=start-end` ranges may exceed this limit: high-depth
mitochondrial BAI bins can span more than 32 MiB even for a small locus. The
server streams these ranges with backpressure and a 64 KiB read buffer;
it does not buffer the entire response. The browser still needs memory to
decode the indexed region. Closing the pane revokes its grants;
unused grants expire after an hour. No sample paths, loci, tokens or byte-read
logs are sent to any external service.

## Repeat the offline browser check

From `gui/app/`, after preparing the locked CLI environment and running
`npm run build`, run:

```sh
node scripts/verify-viewer-offline.mjs --out ../../out/viewer-check
```

Add `--genes` to exercise installed gene spans, case-insensitive navigation and
the missing-table notice. This generates a synthetic chromosome 1 padded with
N bases to the hg38 length (about 242 MiB on disk) and real matching indexes;
it does not download a reference or measure whole-genome performance.

Use `--chromium /path/to/chromium` for an installed browser, or `--cli-root PATH`
to reuse another prepared checkout's CLI runtime. The check validates installed
samtools and bcftools; it does not install tools or download data.

The check copies `tests/fixtures/vep_rs/genome.fa`, a synthetic 3 kb reference,
into the output directory and uses real samtools/bcftools to build matching BAM,
CRAM and VCF fixtures. Chromium opens each track, moves between loci and checks
the missing-CRAM-reference state. It also runs a region extraction through the
app and follows **Show in viewer** to that locus. The request interceptor blocks
requests outside the app's loopback origin; any attempted external request,
console error or CSP violation fails the check.

`library-browser-proof.json` records HTTP status, Content-Range and bytes for
each file request. The large-file case uses a **4 GiB sparse BAM**, with valid
indexed blocks at its start, to catch whole-file reads without storing 4 GiB.
This tests indexed access, not whole-genome rendering performance. Screenshots,
the byte log and the isolated app workspace stay under `out/`.
For an existing multi-GiB biological BAM, use `verify-viewer-library.mjs` against
a running app with matching installed references; its `--fixtures`, `--vcf`,
`--large-bam`, `--locus` and `--next-locus` arguments select local inputs.
The standalone checker works in Basic mode and does not run extraction commands.
Only the self-hosted isolated setup passes `--config` to enable the
missing-reference and extraction-result checks; do not use that option against
an app containing your own settings or outputs. Add `--read-only` to the
self-hosted command to verify the standalone Basic-mode path without extraction.
