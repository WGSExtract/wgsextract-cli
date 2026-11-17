/* Reports: the personal report, polygenic scores and methylation. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var T = WGSM.v2tools;
  var f = T.f;

  T.define({
    slug: "report", label: "Personal report", op: "report", group: "Reports", keywords: "summary findings acmg secondary",
    desc: "One readable report from the results already made for this person: lineage, ancestry, quality, medicines and reported health variants. It runs no analysis itself, so it takes seconds.",
    accepts: ["reads", "vcf"], file: "reginald", button: "Make the report",
    fields: [
      f.select("genes", "Health genes", "The ClinVar section looks only at these; the ACMG list is genes where a finding is medically actionable", "gene_list",
        [["acmg-sf-3.3", "ACMG SF v3.3 (81 genes)"], ["acmg-sf-3.2", "ACMG SF v3.2"], ["none", "None: skip health findings"]], "acmg-sf-3.3", { width: 260 }),
      f.seg("format", "Findings table", "", "format", [["tsv", "TSV"], ["parquet", "Parquet"]], "tsv"),
    ],
    run: function (c) {
      var line = ["wgsextract report --workspace " + c.m.outdir(c.file)];
      if (c.v.genes !== "none") line.push("--gene-list " + c.v.genes);
      if (c.v.format !== "tsv") line.push("--format " + c.v.format);
      var out = { hours: 0.005, command: line, makes: "Opens " + c.name + "’s report when it’s done.", notes: [] };
      if (c.v.genes !== "none" && !c.flag("data-acmg")) {
        out.notes.push({ tone: "info", icon: "download", id: "acmg", title: "The gene list downloads once", text: "A small public list into the reference library; nothing about " + c.name + " is sent.",
          action: { label: "Download", set: "data-acmg", toast: "Downloading the ACMG gene list. See Jobs." } });
        out.blocked = "Download the gene list first, or skip health findings.";
      }
      return out;
    },
  });

  T.define({
    slug: "pgs", label: "Polygenic scores", op: "score/pgs", group: "Reports", keywords: "prs pgs catalog trait risk",
    desc: "Adds up many small effects across the genome into one score for a trait, and places it among a reference population. A score shifts odds a little; it predicts nothing for one person on its own.",
    accepts: ["vcf"], file: "helen", button: "Score",
    fields: [
      f.select("trait", "Trait", "Curated scores from the PGS Catalog", "trait",
        [["height", "Height"], ["ldl", "LDL cholesterol"], ["cad", "Coronary artery disease"], ["t2d", "Type 2 diabetes"], ["custom", "A PGS Catalog ID…"]], "height", { width: 260 }),
      f.text("pgsId", "PGS Catalog ID", "Uncurated scores carry a warning", "pgs_id", "PGS000001", {
        mono: true, width: 140,
        when: function (c) {
          return c.v.trait === "custom";
        },
      }),
      f.toggle("ambiguous", "Keep A/T and C/G sites", "Only when their strand was checked independently", "keep_ambiguous", false),
    ],
    more: [f.number("minMatch", "Minimum match", "Share of the score’s sites found, below which no percentile is given", "min_match", 0.75, { width: 90 })],
    run: function (c) {
      var id = c.v.trait === "custom" ? "--pgs-id " + c.v.pgsId : "--trait " + c.v.trait;
      var out = {
        hours: null, timeText: "minutes",
        command: ["wgsextract score pgs --input " + c.m.path(c.file) + " --build " + c.file.build, id].concat(c.v.ambiguous ? ["--keep-ambiguous"] : [], c.v.minMatch !== 0.75 ? ["--min-match " + c.v.minMatch] : []),
        makes: "The score and percentile appear on " + c.name + "’s page.", notes: [],
      };
      if (!c.flag("pgs-" + c.v.trait)) {
        out.notes.push({ tone: "info", icon: "download", id: "scoring", title: "The scoring file comes from the PGS Catalog",
          text: "A public file of weights. Only the score’s ID is requested; nothing about " + c.name + " is sent.",
          action: { label: "Download", set: "pgs-" + c.v.trait, toast: "Downloading the scoring file. See Jobs." } });
        out.blocked = "Download the scoring file first.";
      }
      return out;
    },
  });

  T.define({
    slug: "methylation", label: "Methylation", op: "methylation/pileup", group: "Reports", keywords: "5mc 5hmc cpg modkit epigenetics nanopore",
    desc: "Reads which cytosines carry methyl marks, which switch genes on and off, from Nanopore or PacBio reads that recorded them.",
    accepts: ["reads"], file: "reginald", button: "Read methylation", requires: ["modkit"],
    fields: [
      f.seg("context", "Sites", function (c) {
        return c.v.context === "all" ? "Every cytosine (CHG and CHH too): much larger output" : "CpG sites only: where most methylation is";
      }, "context", [["cpg", "CpG"], ["all", "All cytosines"]], "cpg"),
      f.toggle("hmc", "Also 5hmC", "From Nanopore 5mCG_5hmCG models", "5hmc", false),
      f.toggle("combine", "Combine strands", "Sum both strands of each CpG", "combine_strands", true),
      f.region(),
    ],
    run: function (c) {
      return {
        hours: null, timeText: "hours on a 30x genome",
        notes: [{ tone: "info", icon: "info", id: "long", title: "Needs reads with methylation tags", text: c.name + "’s Illumina reads have none: methylation comes from Nanopore or PacBio sequencing, or special bisulfite sequencing." }],
        blocked: "These reads carry no methylation tags.",
        command: ["wgsextract methylation pileup --input " + c.m.path(c.file)].concat(c.v.context !== "cpg" ? ["--context all"] : [], c.v.hmc ? ["--5hmc"] : [], c.v.region ? ["--region " + c.v.region] : []),
      };
    },
  });
})();
