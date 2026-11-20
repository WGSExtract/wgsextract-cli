/* Reports: the personal report, polygenic scores and methylation. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var T = WGSM.v2tools;
  var f = T.f;

  T.define({
    slug: "report", label: "Personal report", ops: ["report"], group: "Reports", keywords: "summary findings acmg secondary",
    lesson: { href: "learn-health.html", label: "Interpreting findings without overclaiming" },
    desc: "Assembles one readable summary in seconds from the lineage, ancestry, quality, medicine and health-variant results already saved for this person, without running new analyses.",
    learn: "Puts the highlights from earlier runs into one place to read or share instead of opening each output file on its own.\n\n" +
      "Sections appear only for tools that have already run, so run more checks first if you want a fuller summary. Health sections are compiled for personal interest and research; confirm anything medically important with a clinical test.",
    accepts: ["reads", "vcf"], file: "reginald", button: "Make the report",
    fields: [
      f.select("gene_list", "Health genes", "The ClinVar section looks only at these genes, where a finding is medically actionable",
        [["", "ACMG SF v3.3 (81 genes)"], ["acmg-sf-3.2", "ACMG SF v3.2"]], "", { width: 260 }),
      f.param("table_format", "Findings table", "", { labels: { tsv: "TSV", parquet: "Parquet" } }),
      f.param("download", "Download missing public gene list", "Explicit --download confirmation; no genome information is sent. Off by default."),
    ],
    run: function (c) {
      return {
        hours: 0.005, makes: "Opens " + c.name + "’s report when it’s done.",
        blocked: !c.flag("data-gene-list") && !c.v.download ? "The public gene list is not installed. Rerun with --download to confirm the fetch." : null,
        notes: [{ tone: "info", icon: "download", id: "acmg", title: "Offline until you confirm",
          text: "Missing public data is never fetched silently. --download opts in to the gene list, checked against its pinned SHA-256. No genome information is sent." }],
      };
    },
  });

  T.define({
    slug: "pgs", label: "Polygenic scores", ops: ["score/pgs"], group: "Reports", keywords: "prs pgs catalog trait risk",
    lesson: { href: "learn-health.html#polygenic", label: "Scores, percentiles & risk" },
    desc: "Sums many small effects across the genome into a trait score and ranks it in a reference population; a score nudges statistical odds slightly without predicting an individual outcome.",
    learn: "Explore polygenic traits such as height or test published models from the PGS Catalog. Results are given as a percentile in a reference population, such as “higher than 70% of people”, which describes relative rank and does not mean a 70% chance of an outcome.\n\n" +
      "Accuracy drops when your ancestry differs from the studies behind a score, which remain mostly European so far, and lifestyle, environment and family history often outweigh it. Treat scores as research only without changing medical treatment or screening. Fetching a scoring file sends only the score’s ID and build to the PGS Catalog; your genotypes stay on this computer.",
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
      f.param("download", "Download missing public scoring file", "Explicit --download confirmation. Only the score ID and build are sent to the PGS Catalog, never genotypes."),
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
        blocked: !c.flag("data-pgs") && !c.v.download ? "The public scoring file is not installed. Rerun with --download to confirm the fetch." : null,
        notes: [{ tone: "info", icon: "download", id: "scoring", title: "The scoring file comes from the PGS Catalog",
          text: "Use installed weights offline. If missing, explicitly enable --download; only the score’s ID and build are requested, never genotypes." }],
      };
    },
  });

  T.define({
    slug: "methylation", label: "Methylation", ops: ["methylation/pileup"], group: "Reports", keywords: "5mc 5hmc cpg modkit epigenetics nanopore",
    lesson: { href: "learn-sequencing.html#platforms", label: "What different technologies can measure" },
    desc: "Tabulates cytosine methyl marks (chemical tags involved in switching genes on and off) from Nanopore or PacBio reads that recorded them.",
    learn: "Nanopore and PacBio runs that retain modification tags show where cytosines are methylated, mostly at CpG sites, without a separate assay. Standard short-read Illumina genomes don’t record these marks.\n\n" +
      "Methylation differs between tissues and changes with age and environment, so a saliva or blood sample reflects that tissue at collection time. These pileups are intended for research exploration and do not give clinical health results.",
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
