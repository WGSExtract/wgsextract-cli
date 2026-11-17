/* Reports: the personal report, polygenic scores and methylation. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var T = WGSM.v2tools;
  var f = T.f;

  T.define({
    slug: "report", label: "Personal report", ops: ["report"], group: "Reports", keywords: "summary findings acmg secondary",
    lesson: { href: "learn-health.html", label: "Interpreting findings without overclaiming" },
    desc: "One readable report from the results already made for this person: lineage, ancestry, quality, medicines and reported health variants. It runs no analysis itself, so it takes seconds.",
    learn: "Use it when you want the highlights in one place, to read or share, instead of opening each result: lineage, ancestry, quality checks, medicine–gene results and reported variants that earlier tools found.\n\n" +
      "It only includes what has been run, so run more tools first for a fuller report. It’s written for personal interest: its health sections are research results, not a diagnosis, and anything important should be confirmed by a clinical test.",
    accepts: ["reads", "vcf"], file: "reginald", button: "Make the report",
    fields: [
      f.select("gene_list", "Health genes", "The ClinVar section looks only at these genes, where a finding is medically actionable",
        [["", "ACMG SF v3.3 (81 genes)"], ["acmg-sf-3.2", "ACMG SF v3.2"]], "", { width: 260 }),
      f.param("table_format", "Findings table", "", { labels: { tsv: "TSV", parquet: "Parquet" } }),
    ],
    requests: function (c, base) {
      var req = base("report", { workspace: c.m.realPath(c.m.outdir(c.file)) });
      delete req.params.input;
      return [req];
    },
    run: function (c) {
      return {
        hours: 0.005, makes: "Opens " + c.name + "’s report when it’s done.",
        notes: [{ tone: "info", icon: "download", id: "acmg", title: "The gene list downloads the first time",
          text: "A small public list, checked against its pinned SHA-256, into the reference library. Nothing about " + c.name + " is sent." }],
      };
    },
  });

  T.define({
    slug: "pgs", label: "Polygenic scores", ops: ["score/pgs"], group: "Reports", keywords: "prs pgs catalog trait risk",
    lesson: { href: "learn-health.html#polygenic", label: "Scores, percentiles & risk" },
    desc: "Adds up many small effects across the genome into one score for a trait, and places it among a reference population. A score shifts odds a little; it predicts nothing for one person on its own.",
    learn: "Use it out of curiosity about a trait such as height, or to try published scores from the PGS Catalog. The result is a percentile among a reference population, “higher than 70% of people”, not a chance of anything happening to you.\n\n" +
      "Scores work best for people whose ancestry matches the studies behind them, mostly of European ancestry so far, and lifestyle, environment and family history often matter more. Research use only: don’t change treatment or screening because of a score. Only the score’s ID and build are sent to the PGS Catalog, never your genotypes.",
    accepts: ["vcf"], file: "helen", button: "Score",
    fields: [
      f.select("trait", "Trait", "A curated score from the PGS Catalog, or one by its ID", [["height", "Height"], ["cad", "Coronary artery disease"], ["t2d", "Type 2 diabetes"], ["ldl-cholesterol", "LDL cholesterol"], ["triglycerides", "Triglycerides"], ["stroke", "Stroke"], ["breast-cancer", "Breast cancer"], ["prostate-cancer", "Prostate cancer"], ["lung-cancer", "Lung cancer"], ["thyroid-cancer", "Thyroid cancer"], ["bmi", "Body mass index (academic research use only)"], ["", "A PGS Catalog ID…"]], "height", { width: 260 }),
      f.param("pgs_id", "PGS Catalog ID", "Uncurated scores carry a warning", {
        def: "PGS000001", width: 140,
        when: function (c) {
          return !c.v.trait;
        },
      }),
      f.param("keep_ambiguous", "Keep A/T and C/G sites", "Only when their strand was checked independently"),
    ],
    more: [f.param("min_match", "Minimum match", "Share of the score’s sites found, below which no percentile is given. Empty: 0.75", { width: 90 })],
    requests: function (c, base) {
      var req = base("score/pgs", { build: c.file.build });
      if (c.v.trait) delete req.params.pgs_id;
      return [req];
    },
    run: function (c) {
      return {
        hours: null, timeText: "minutes", makes: "The score and percentile appear on " + c.name + "’s page.",
        notes: [{ tone: "info", icon: "download", id: "scoring", title: "The scoring file comes from the PGS Catalog",
          text: "A public file of weights, fetched by the run. Only the score’s ID is requested; nothing about " + c.name + " is sent." }],
      };
    },
  });

  T.define({
    slug: "methylation", label: "Methylation", ops: ["methylation/pileup"], group: "Reports", keywords: "5mc 5hmc cpg modkit epigenetics nanopore",
    lesson: { href: "learn-sequencing.html#platforms", label: "What different technologies can measure" },
    desc: "Reads which cytosines carry methyl marks, which switch genes on and off, from Nanopore or PacBio reads that recorded them.",
    learn: "Use it if you sequenced with Nanopore or PacBio and the reads kept their modification tags: the same reads then show where cytosines are methylated, mostly at CpG sites, without a separate test. Ordinary Illumina genomes don’t record it.\n\n" +
      "Methylation differs between tissues and changes with age and environment, so a saliva or blood sample shows that tissue at that time. It’s research: patterns are interesting to explore, but they aren’t health results.",
    accepts: ["reads"], file: "reginald", button: "Read methylation", requires: ["modkit"],
    fields: [
      f.param("context", "Sites", function (c) {
        return c.v.context === "all" ? "Every cytosine (CHG and CHH too): much larger output" : "CpG sites only: where most methylation is";
      }, { def: "cpg", labels: { cpg: "CpG", all: "All cytosines" } }),
      f.param("hydroxymethyl", "Also 5hmC", "From Nanopore 5mCG_5hmCG models"),
      f.param("combine_strands", "Combine strands", "Sum both strands of each CpG", { def: true }),
      f.region(),
    ],
    run: function (c) {
      return {
        hours: null, timeText: "hours on a 30x genome",
        notes: [{ tone: "info", icon: "info", id: "long", title: "Needs reads with methylation tags", text: c.name + "’s Illumina reads have none: methylation comes from Nanopore or PacBio sequencing, or special bisulfite sequencing." }],
        blocked: "These reads carry no methylation tags.",
      };
    },
  });
})();
