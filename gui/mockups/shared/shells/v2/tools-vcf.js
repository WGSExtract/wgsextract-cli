/* VCF transformations and specialist annotations. All use local data. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var T = WGSM.v2tools;
  var f = T.f;
  var HEALTH = { href: "learn-health.html#annotation", label: "Annotation is context, not a diagnosis" };
  var FILES = { href: "learn-files.html#vcf", label: "What a VCF contains" };

  T.define({
    slug: "filter-vcf", label: "Filter variants", ops: ["vcf/filter"], group: "Annotate", lesson: FILES,
    desc: "Writes a smaller VCF containing only variants in a chosen region or gene, or matching a bcftools expression.",
    learn: "Narrows a whole-genome VCF down to a gene, region or quality rule while leaving the original file untouched so you can change your criteria later.\n\nExpressions such as QUAL>=30 test sequencing and call quality; whether a variant matters medically depends on annotation. Looking up a gene name needs the installed gene map.",
    accepts: ["vcf"], file: "helen", button: "Filter a copy",
    fields: [f.region(), f.param("gene", "Gene", "Used when no region is set, for example BRCA1"),
      f.param("expr", "Include expression", "bcftools syntax; keep variants satisfying this", { placeholder: "QUAL>=30", width: 360 }),
      f.param("exclude_near_gaps", "Exclude near reference gaps", "Remove calls close to stretches of N in the reference")],
    run: function (c) {
      return { timeText: "minutes", blocked: !c.v.region && !c.v.gene && !c.v.expr && !c.v.exclude_near_gaps ? "Choose a region, gene or filter." : null,
        makes: "Makes filtered.vcf.gz; the input VCF stays." };
    },
  });

  T.define({
    slug: "annotate-vcf", label: "Copy annotation columns", ops: ["vcf/annotate"], group: "Annotate", lesson: HEALTH,
    keywords: "bcftools annotate custom annotation",
    desc: "Copy names or INFO columns from a local annotation VCF or tabular file into your VCF.",
    learn: "Use it when you have a local specialist dataset outside the built-in annotation tools. The source must share the same reference coordinates and be indexed as bcftools expects; nothing is sent to a remote service.\n\nTabular sources need a column mapping in bcftools syntax. Copied values come straight from that external file at matching coordinates and leave your sample’s genotype calls unchanged.",
    accepts: ["vcf"], file: "helen", button: "Copy annotations",
    fields: [f.param("ann_vcf", "Annotation source", "A local indexed VCF or tab file", { width: 420 }),
      f.param("cols", "Columns", "Empty: infer from a VCF header; tab files need a column mapping", { placeholder: "CHROM,POS,REF,ALT,INFO/SCORE", width: 420 })],
    run: function (c) {
      return { timeText: "minutes", blocked: c.v.ann_vcf ? null : "Choose the annotation source.",
        makes: "Makes annotated.vcf.gz with the copied columns." };
    },
  });

  T.define({
    slug: "annotation-chain", label: "Annotation chain", ops: ["vcf/chain-annotate"], group: "Annotate", lesson: HEALTH,
    desc: "Runs a sequence of annotations on one VCF, carrying each step’s added columns into the next.",
    learn: "Builds one combined VCF without feeding each tool’s output into the next by hand. By default the chain runs ClinVar, REVEL, phyloP, gnomAD and VEP.\n\nAny step missing its tool or local data is skipped with a warning instead of stopping the run, so check the warnings and applied-step list when it finishes. Reference data stays local, with no silent downloads.",
    accepts: ["vcf"], file: "helen", button: "Run chain",
    fields: [f.param("annotations", "Steps, in order", "Comma-separated annotation names", { width: 420 }),
      f.param("keep_intermediates", "Keep each intermediate VCF", "Useful to see what each step added; needs more space")],
    run: function (c) {
      return { timeText: "minutes to hours", memGB: 8,
        blocked: c.v.annotations.trim() ? null : "Choose at least one annotation step.",
        makes: "Makes chain_annotated.vcf.gz; inspect warnings for skipped steps." };
    },
  });

  T.define({
    slug: "research-acmg", label: "Research ACMG classes", ops: ["vcf/acmg"], group: "Annotate", lesson: HEALTH,
    keywords: "acmg amp fastvep classification research",
    desc: "Classifies variants in a vep-rs-annotated VCF using fastVEP’s automated ACMG/AMP research rules.",
    learn: "Built strictly for research into automated variant interpretation. Input has to be a GRCh38 VCF from vep-rs along with the matching fastVEP annotation databases and GFF3; plain VCFs and Ensembl VEP outputs lack the fields it expects.\n\nAutomated rules miss evidence that a clinical ACMG review weighs, so do not use these labels to guide medical care or reproductive decisions. Any finding of concern must be assessed by a clinical laboratory and genetics professional.",
    accepts: ["vcf"], file: "helen", button: "Classify for research",
    fields: [f.param("research_acmg", "I understand this is research only", "Explicit opt-in: not a diagnosis or clinical interpretation")],
    more: [f.param("sa_dir", "fastVEP data folder", "Empty: configured ACMG databases"),
      f.param("gff3", "Ensembl GRCh38 GFF3", "Empty: the GFF3 recorded with the installed data"), f.threads()],
    run: function (c) {
      return { timeText: "minutes", memGB: 8,
        blocked: !c.v.research_acmg ? "Acknowledge the research-only limits first." : c.file.build !== "GRCh38" ? "Needs a GRCh38 vep-rs VCF." : null,
        notes: [{ tone: "warn", icon: "triangle-alert", title: "Requires a vep-rs-annotated VCF",
          text: "The demo VCF is a placeholder without verified vep-rs annotations. Confirm your input’s provenance and the installed fastVEP data before using real results." }],
        makes: "Makes acmg_annotated.vcf.gz and acmg_summary.json; research only." };
    },
  });

  T.define({
    slug: "pharmgkb", label: "Drug-response annotations", ops: ["vcf/pharmgkb"], group: "Annotate", lesson: { href: "learn-health.html#medicines", label: "Genes & medicines" },
    keywords: "pharmgkb medicine pharmacogenomics",
    desc: "Tags GRCh38 variants with published drug-response associations from PharmGKB.",
    learn: "Checks which variants in your VCF appear in PharmGKB’s drug-response records. Unlike PharmCAT, which combines sites across a gene into star alleles, this step annotates individual variant positions.\n\nA reported association isn’t a prescribing instruction: never start, stop or change a medicine on this basis, and discuss any questions with your pharmacist or doctor.",
    accepts: ["vcf"], file: "helen", button: "Annotate",
    fields: [f.param("pharmgkb_file", "PharmGKB data", "Empty: the installed reference data")],
    run: function (c) {
      return { timeText: "minutes", blocked: c.file.build !== "GRCh38" ? "PharmGKB annotation needs GRCh38." : null,
        makes: "Makes pharmgkb_annotated.vcf.gz. Not prescribing advice." };
    },
  });

  T.define({
    slug: "sv-frequency", label: "Structural-variant frequencies", ops: ["vcf/sv-freq"], group: "Annotate", lesson: { href: "learn-health.html#scores", label: "Population frequencies & their limits" },
    keywords: "gnomad sv long read overlap rare common",
    desc: "Compare structural variants with gnomAD-SV and gnomAD-LR to estimate how common similar changes are.",
    learn: "After calling structural variants, compare them against gnomAD-SV and gnomAD-LR. Because large changes rarely share exact coordinates across people, matching relies on overlap, size similarity and breakpoint distance, and changing those thresholds changes the matches.\n\nGive it a structural-variant VCF, as small-variant files have nothing to match here. An uncommon call can still be benign, and a missing match doesn’t prove a change is new.",
    accepts: ["vcf"], file: "helen", button: "Annotate SV frequencies",
    fields: [f.param("rare_af", "Rare below", "Population frequency, not a percentage"),
      f.param("common_af", "Common from", "Between these thresholds: intermediate")],
    more: [f.param("min_overlap", "Minimum reciprocal overlap", "Fraction shared by both intervals"),
      f.param("min_size_similarity", "Minimum size similarity", "Smaller / larger interval length"),
      f.param("max_distance", "Breakpoint distance", "Maximum distance in bases"),
      f.param("gnomad_sv_file", "gnomAD-SV data", "Empty: installed data"),
      f.param("gnomad_lr_file", "gnomAD-LR data", "Optional local long-read SV catalog"), f.threads()],
    run: function (c) {
      return { timeText: "minutes", blocked: c.v.rare_af >= c.v.common_af ? "Rare must be below common." : null,
        makes: "Makes sv_freq_annotated.vcf.gz and sv_freq.tsv. Use an SV VCF." };
    },
  });

  T.define({
    slug: "consensus", label: "Consensus sequence", ops: ["vcf/to-fasta"], group: "Extract", lesson: FILES,
    keywords: "fasta consensus haplotype iupac",
    desc: "Apply a VCF’s variants to the matching reference to write a consensus FASTA sequence.",
    learn: "Writes a FASTA sequence of A, C, G and T letters for programs that take sequence input instead of a VCF. IUPAC codes represent heterozygous bases; haplotypes 1 and 2 correspond to parental copies only where the calls are phased.\n\nSites absent from the VCF are uncertain: Auto writes N at uncalled gVCF blocks, while filling reference bases into a variant-only VCF. Choose N when assuming the reference is unsafe, since the FASTA is assembled by patching your VCF’s calls onto the reference.",
    accepts: ["vcf"], file: "helen", button: "Build consensus",
    fields: [f.param("haplotype", "Alleles to write", "", { labels: { iupac: "IUPAC ambiguity codes", alt: "Alternate allele", "1": "Haplotype 1", "2": "Haplotype 2" } }),
      f.param("uncalled", "Uncalled positions", "", { labels: { auto: "Auto · depends on VCF type", n: "N · unknown", ref: "Reference base" } }),
      f.region()],
    more: [f.param("sample", "VCF sample", "Empty: use the single sample"),
      f.param("mask", "Mask BED", "Write N in these regions"), f.param("include_filtered", "Include failed calls", "Normally FILTER failures are excluded"),
      f.param("chain", "Also a liftover chain", "Map reference coordinates to the consensus"), f.threads()],
    run: function () { return { timeText: "minutes", memGB: 2, makes: "Makes a compressed consensus FASTA, and a chain if requested." }; },
  });
})();
