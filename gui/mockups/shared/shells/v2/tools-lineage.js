/* Lineage and ancestry. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var T = WGSM.v2tools;
  var f = T.f;

  function done(c, what, result, detail) {
    return { tone: "ok", icon: "check", id: "done", title: "Found earlier: " + result, text: detail + " Running again is useful with other settings.",
      action: { label: "See the result", href: c.fileKey === "alex" || c.fileKey === "alexkit" ? "ancestry-alex.html" : "ancestry.html" } };
  }

  T.define({
    slug: "y-haplogroup", label: "Y haplogroup", op: "lineage/y-haplogroup", group: "Lineage", keywords: "yleaf yfull ftdna isogg paternal",
    desc: "Places the paternal line on the Y-DNA tree from the SNPs on the Y chromosome.",
    accepts: ["reads", "vcf"], file: "reginald", button: "Find the haplogroup",
    fields: [
      f.select("tree", "Tree", "YFull’s is the most detailed", "tree", [["yfull", "YFull (pinned release)"], ["yfull_v10", "YFull 10.01"], ["ftdna", "FamilyTreeDNA"], ["isogg", "ISOGG"]], "yfull", { width: 240 }),
      f.toggle("ancient", "Ancient DNA", "Marker tables for damaged, low-coverage samples", "ancient", false),
    ],
    moreTitle: "Thresholds",
    more: [
      f.select("build", "Build", function (c) {
        return "Read from the file: " + c.file.build;
      }, "build", [["auto", "From the file"], ["hg19", "GRCh37"], ["hg38", "GRCh38"], ["t2t", "T2T"]], "auto", { width: 200 }),
      f.number("minReads", "Minimum reads", "At a marker", "min_reads", 10, { width: 80 }),
      f.number("baseQuality", "Minimum base quality", "", "base_quality", 20, { width: 80 }),
      f.number("majority", "Agreement (%)", "Reads that must agree at a marker", "base_majority", 90, { width: 80 }),
    ],
    run: function (c) {
      var line = ["wgsextract lineage y-haplogroup --input " + c.m.path(c.file)];
      if (c.v.tree !== "yfull") line.push("--tree " + c.v.tree);
      if (c.v.ancient) line.push("--ancient");
      if (c.v.build !== "auto") line.push("--build " + c.v.build);
      [["minReads", "--min-reads", 10], ["baseQuality", "--base-quality", 20], ["majority", "--base-majority", 90]].forEach(function (k) {
        if (c.v[k[0]] !== k[2]) line.push(k[1] + " " + c.v[k[0]]);
      });
      var noY = WGSM.v2extract.noY(c);
      return {
        hours: null, timeText: "minutes", command: line, makes: "Replaces the result on " + c.name + "’s page.",
        notes: noY ? [{ tone: "info", icon: "user", id: "no-y", title: "No Y chromosome", text: noY }]
          : c.fileKey === "reginald" ? [done(c, "y", "J-ZS241", "41 of 43 defining markers matched, today.")] : [],
        blocked: noY ? "No Y chromosome in this genome." : null,
      };
    },
  });

  T.define({
    slug: "mt-haplogroup", label: "mtDNA haplogroup", op: "lineage/mt-haplogroup", group: "Lineage", keywords: "haplogrep phylotree mitotree maternal",
    desc: "Places the maternal line on the mtDNA tree with HaploGrep 3.",
    accepts: ["reads", "vcf"], file: "reginald", button: "Find the haplogroup",
    fields: [
      f.select("tree", "Tree", function (c) {
        return c.v.tree === "mitotree-rcrs" ? "FamilyTreeDNA’s Mitotree: non-commercial use only" : "PhyloTree 17; the Forensic Update by default";
      }, "tree", [["phylotree-fu-rcrs", "PhyloTree 17 Forensic Update"], ["phylotree-rcrs", "PhyloTree 17"], ["mitotree-rcrs", "FamilyTreeDNA Mitotree"]], "phylotree-fu-rcrs", { width: 280 }),
      f.toggle("contamination", "Check for contamination", function (c) {
        return c.file.kind === "vcf" ? "With haplocheck; a VCF needs heteroplasmy levels (FORMAT/AF)" : "With haplocheck: a mix of two mtDNA types" + (c.M.os === "windows" ? "; text report only on Windows" : "");
      }, "contamination", true),
    ],
    run: function (c) {
      var line = ["wgsextract lineage mt-haplogroup --input " + c.m.path(c.file)];
      if (c.v.tree !== "phylotree-fu-rcrs") line.push("--tree " + c.v.tree);
      if (c.v.contamination) line.push("--contamination");
      return {
        hours: null, timeText: "minutes", command: line, makes: "Replaces the result on " + c.name + "’s page.",
        notes: c.fileKey === "reginald" ? [done(c, "mt", "K1a1b1a", "Quality 0.94 of 1, today.")] : [],
      };
    },
  });

  T.define({
    slug: "y-str", label: "Y-STR markers", op: "lineage/y-str", group: "Lineage", keywords: "ftdna yseq y-111 surname",
    desc: "Counts repeats at Y-STR markers, the numbers FamilyTreeDNA and YSEQ compare between men who share a surname.",
    accepts: ["reads"], file: "reginald", button: "Count the markers",
    fields: [f.select("panel", "Panel", "Y-111 includes the smaller FamilyTreeDNA panels", "panel",
      [["Y-111", "Y-111 (FamilyTreeDNA)"], ["Y-67", "Y-67"], ["Y-37", "Y-37"], ["Y-25", "Y-25"], ["Y-12", "Y-12"], ["PPY23", "PowerPlex Y23"], ["YfilerPlus", "Yfiler Plus"], ["all", "Every marker"]], "Y-111", { width: 240 })],
    moreTitle: "Thresholds",
    more: [
      f.number("minReads", "Spanning reads", "Reads that must cross the whole repeat", "min_reads", 2, { width: 80 }),
      f.number("fraction", "Agreement", "Share of those reads that must agree", "min_fraction", 0.6, { width: 80 }),
      f.number("mapq", "Mapping quality", "For single-copy markers", "min_mapq", 20, { width: 80 }),
    ],
    run: function (c) {
      var line = ["wgsextract lineage y-str --input " + c.m.path(c.file)];
      if (c.v.panel !== "Y-111") line.push("--panel " + c.v.panel);
      [["minReads", "--min-reads", 2], ["fraction", "--min-fraction", 0.6], ["mapq", "--min-mapq", 20]].forEach(function (k) {
        if (c.v[k[0]] !== k[2]) line.push(k[1] + " " + c.v[k[0]]);
      });
      var noY = WGSM.v2extract.noY(c);
      return {
        hours: null, timeText: "minutes", command: line, makes: "The markers appear on " + c.name + "’s ancestry page, ready to compare.",
        notes: noY ? [{ tone: "info", icon: "user", id: "no-y", title: "No Y chromosome", text: noY }] : [],
        blocked: noY ? "No Y chromosome in this genome." : null,
      };
    },
  });

  T.define({
    slug: "ancestry-proportions", label: "Ancestry proportions", op: "qc/ancestry", group: "Lineage", keywords: "admixture k7 k12 hgdp populations",
    desc: "Compares the genome with 80 reference populations and gives the share of each component, at continental and regional level, with 95% intervals.",
    accepts: ["reads", "vcf"], file: "reginald", button: "Work it out",
    fields: [f.number("bootstrap", "Repeats for the intervals", "More repeats give steadier 95% intervals; 0 skips them", "bootstrap", 100, { width: 90 })],
    more: [f.select("build", "Build", function (c) {
      return "Read from the file: " + c.file.build;
    }, "build", [["auto", "From the file"], ["hg19", "GRCh37"], ["hg38", "GRCh38"], ["t2t", "T2T"]], "auto", { width: 200 })],
    run: function (c) {
      var line = ["wgsextract qc ancestry --input " + c.m.path(c.file)];
      if (c.v.bootstrap !== 100) line.push("--bootstrap " + c.v.bootstrap);
      if (c.v.build !== "auto") line.push("--build " + c.v.build);
      return {
        hours: null, timeText: "minutes", command: line, makes: "The result appears on " + c.name + "’s page.",
        notes: c.fileKey === "reginald" ? [done(c, "anc", "European 52%, Middle Eastern 38%", "And 3 smaller components, today.")] : [],
      };
    },
  });
})();
