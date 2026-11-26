# Gene page

**Gene page**, in the WGS Extract sidebar below **Genome viewer**, shows one
gene from your own files: its transcripts and exons, your variants in it with
their annotations, and how well your reads cover each exon. **Show gene** reads
files on this computer with `wgsextract info gene`. It does not download data
or send information about your genome.

## Opening a gene

Enter a gene symbol or alias (NCBI RefSeq, such as `BRCA1`) and, optionally:

- **Aligned reads (BAM or CRAM)**: for read depth over each exon. It starts
  from the BAM/CRAM chosen in the app.
- **Variants (VCF)**: for the variant table. Choose the VCF your annotation
  steps wrote (VEP, ClinVar, gnomAD, ACMG) to fill those columns.
- **Callable depth**: the reads an exon base needs to count as callable;
  empty uses the `confidence_min_depth` setting.

**Show gene** runs `wgsextract info gene` and shows:

- **Transcripts and exons**: the RefSeq transcripts `wgsextract ref gene-map`
  installed, longest coding sequence first. The diagram draws the chosen
  transcript's exons along the gene, green when every base reaches the callable
  depth, red when some don't, grey without reads, with your variants as ticks.
- **Read coverage**: the gene's mean depth and callable share, and each exon of
  the chosen transcript, numbered from its 5' end, with its mean and lowest
  depth and the share of its bases at the callable depth.
- **Your variants**: the alleles you carry in the gene, with consequence,
  impact, HGVS, zygosity, depth, ClinVar significance, gnomAD frequency and the
  research-only ACMG class. **Show** filters them: rare (gnomAD below 1%) with
  high or moderate impact, ClinVar pathogenic or likely pathogenic, or ACMG
  pathogenic or likely pathogenic. The CLI tags each row, so the page and the
  export apply the same rules. **Where each annotation comes from** lists the
  VCF header lines (tool versions, the files annotated from) behind each column.
- **Export TSV** writes the shown rows with `info gene --export` as
  `<vcf stem>_<GENE>_<filter>_variants.tsv`, in the `WGSEv6/` folder beside your
  files or your output folder, and **Show in folder** reveals it.
- **Open in genome viewer** opens the [genome viewer](genome-viewer.md) at the
  gene with your reads or variants, its gene span and the RefSeq transcripts
  track; **Back to the gene page** returns.

ACMG classes are fastVEP's automated classification, for research only and
not a diagnosis.

## Open in gene.iobio

For GRCh37 or GRCh38, choose **gene.iobio licence and privacy…** on a gene
result. Read the notice and choose **Accept and open gene.iobio** to open
`gene.iobio.io` in your default browser. WGS Extract does not bundle or install
gene.iobio or its backend. The CLI has no external hand-off.

gene.iobio is free for academic and personal use; any other use (including
commercial) requires a licence from Frameshift Genomics (info@frameshift.io).
Not for clinical use.

The browser sends only the selected gene symbol and reference build in the
link, along with ordinary browser request metadata. The gene and build may
themselves be sensitive. WGS Extract does not send reads, variants, filenames,
paths or sample IDs. The notice shows the local paths used for the current
result. On gene.iobio, choose **Load** → **Specify files** and select each file
with its matching index: BAM + BAI, CRAM + CRAI, or `.vcf.gz` + TBI or CSI.
The notice marks filenames the picker does not accept, such as `.vcf.bgz`.
Selecting local files on the website can expose them to iobio's servers through
`waygate.iobio.io`; do not assume access is limited to the selected gene.
Each launch requires fresh confirmation. The app records the last
acceptance time and notice version locally, without recording the gene or
build in the receipt.

The desktop Gene page accepts local inputs, not remote URLs. gene.iobio's
file URL parameters are for files its hosted backend can reach; they cannot
load files from your computer. In a Chromium test against the live site
(2026-11-26), the site annotated BRCA1 variants from a public VCF and index.
With a synthetic loopback VCF and index, the browser sent those URLs to
`backend.iobio.io/variantHeader`, which could not open them. There were no
requests to the loopback file server. The site sent the page URL to Google
Analytics both with the test file parameters and with just the gene and build.
WGS Extract does not start a file server or add file parameters to the link.

## Missing data

Each missing input or resource is listed under **Missing data**, with the
command that provides it:

| Missing | Command |
| --- | --- |
| Transcript and exon models (hg19 and hg38, about 17 MB from UCSC) | `wgsextract ref gene-map` |
| Consequences and impacts | `wgsextract vep run --vcf-input VCF` |
| ClinVar significance | `wgsextract vcf clinvar --vcf-input VCF` |
| gnomAD frequencies (needed to pick out rare variants) | `wgsextract vcf gnomad --vcf-input VCF` |
| Research-only ACMG classes | `wgsextract vcf acmg --vcf-input VCF` |
| A VCF | `wgsextract vcf call --input BAM` |

When required reference files are missing (such as **Gene Maps** from
`wgsextract ref gene-map` or the CRAM reference genome), **Show gene** stops
before running and offers **Cancel**, **Continue** (if you installed them
elsewhere), or **Install and Continue** — matching other desktop commands.
Missing VCF annotation columns (VEP, ClinVar, gnomAD, ACMG) are listed under
**Missing data** without blocking the page or prompting to download anything.
T2T references have gene spans but no transcript models.

## Checking it

`node gui/app/scripts/verify-gene-page.mjs --out out/gene-page` (after
`npm run build` in `gui/app`, with the CLI's Pixi environment installed) builds
synthetic hg38 fixtures with the CLI's code, serves the app, and drives the
gene page and the viewer opened from it in Chromium with every non-local
request aborted. It checks the exon coverage, the filters, the export and the
viewer's transcript track, and fails on any outside request, content security
policy violation or console error. Screenshots and `gene-page-proof.json` are
written to `--out`.
