/* Lineage and ancestry. */
(function () {
  "use strict";
  var WGSM = window.WGSM;
  var T = WGSM.v2tools;
  var f = T.f;
  var BUILDS = { unset: "From the file", labels: { hg19: "GRCh37 (hg19)", hg38: "GRCh38 (hg38)", t2t: "T2T" } };

  function done(c, result, detail) {
    return { tone: "ok", icon: "check", id: "done", title: "Found earlier: " + result, text: detail + " Running again is useful with other settings.",
      action: { label: "See the result", href: c.fileKey === "alex" || c.fileKey === "alexkit" ? "ancestry-alex.html" : "ancestry.html" } };
  }

  function build(c) {
    return "Empty: read from the file (" + c.file.build + ")";
  }

  T.define({
    slug: "y-haplogroup", label: "Y haplogroup", needsY: true, ops: ["lineage/y-haplogroup"], group: "Lineage", keywords: "yleaf yfull ftdna isogg paternal",
    lesson: { href: "learn-ancestry.html#lineage", label: "Haplogroups & direct lineages" },
    desc: "Places the paternal line on the Y-DNA tree from the SNPs on the Y chromosome.",
    learn: "Traces your direct father-to-son line, which often follows a surname, and shows how far down the human Y tree your SNPs place you. Because whole-genome sequencing covers far more Y SNPs than a DNA-test kit, the branch is usually much more specific.\n\n" +
      "Only genomes with a Y chromosome can be typed here. Compare your branch with surname and haplogroup projects, run Y-STR markers to separate close relatives within a branch, or use the Y chromosome extract so YFull or FamilyTreeDNA can place you on their latest trees.",
    accepts: ["reads", "vcf"], file: "reginald", button: "Find the haplogroup",
    fields: [
      f.select("tree", "Tree", "YFull’s is the most detailed", [["", "YFull (pinned release)"], ["yfull_v10", "YFull 10.01"], ["ftdna", "FamilyTreeDNA"], ["isogg", "ISOGG"]], "", { width: 240 }),
      f.param("ancient", "Ancient DNA", "Marker tables for damaged, low-coverage samples"),
    ],
    moreTitle: "Thresholds",
    more: [
      f.param("build", "Build", build, Object.assign({ width: 200, as: "select" }, BUILDS)),
      f.param("min_reads", "Minimum reads", "At a marker", { width: 80 }),
      f.param("base_quality", "Minimum base quality", "", { width: 80 }),
      f.param("base_majority", "Agreement (%)", "Reads that must agree at a marker", { width: 80 }),
    ],
    run: function (c) {
      var noY = WGSM.v2extract.noY(c);
      return {
        hours: null, timeText: "minutes", makes: "Replaces the result on " + c.name + "’s page.",
        notes: noY ? [{ tone: "info", icon: "user", id: "no-y", title: "No Y chromosome", text: noY }]
          : c.fileKey === "reginald" ? [done(c, "J-ZS241", "41 of 43 defining markers matched, today.")] : [],
        blocked: noY ? "No Y chromosome in this genome." : null,
      };
    },
  });

  T.define({
    slug: "mt-haplogroup", label: "mtDNA haplogroup", ops: ["lineage/mt-haplogroup"], group: "Lineage", keywords: "haplogrep phylotree mitotree maternal",
    lesson: { href: "learn-ancestry.html#lineage", label: "Haplogroups & direct lineages" },
    desc: "HaploGrep 3 assigns your maternal line on the mtDNA tree from the mitochondrial sequence.",
    learn: "Mitochondrial DNA follows your direct maternal line through your mother, her mother and so on back; everyone carries it, men included, though only women pass it on.\n\n" +
      "A whole genome reads the entire mitochondrial sequence, so the branch is usually precise; a DNA-test kit reads only some positions. The quality score says how well the sample fits its branch, and haplocheck flags a mix of two mtDNA types, from contamination or heteroplasmy. To compare in detail with others, the Mitochondrial DNA extract makes a FASTA.",
    accepts: ["reads", "vcf"], file: "reginald", button: "Find the haplogroup",
    fields: [
      f.param("tree", "Tree", function (c) {
        return c.v.tree === "mitotree-rcrs" ? "FamilyTreeDNA’s Mitotree: non-commercial use only" : "PhyloTree 17; the Forensic Update by default";
      }, { as: "select", width: 280, unset: "PhyloTree 17 Forensic Update (default)", labels: { "phylotree-fu-rcrs": "PhyloTree 17 Forensic Update", "phylotree-rcrs": "PhyloTree 17", "mitotree-rcrs": "FamilyTreeDNA Mitotree" } }),
      f.param("contamination", "Check for contamination", function (c) {
        return c.file.kind === "vcf" ? "With haplocheck; a VCF needs heteroplasmy levels (FORMAT/AF)" : "With haplocheck: a mix of two mtDNA types" + (c.M.os === "windows" ? "; text report only on Windows" : "");
      }, { def: true }),
    ],
    run: function (c) {
      return {
        hours: null, timeText: "minutes", makes: "Replaces the result on " + c.name + "’s page.",
        notes: c.fileKey === "reginald" ? [done(c, "K1a1b1a", "Quality 0.94 of 1, today.")] : [],
      };
    },
  });

  T.define({
    slug: "y-str", label: "Y-STR markers", needsY: true, ops: ["lineage/y-str"], group: "Lineage", keywords: "ftdna yseq y-111 surname",
    lesson: { href: "learn-ancestry.html#lineage", label: "Y-STRs & paternal comparisons" },
    desc: "Counts repeats at Y-STR markers, the numbers FamilyTreeDNA and YSEQ compare between men who share a surname.",
    learn: "Surname projects compare repeat counts at 37, 67 or 111 Y-chromosome markers to see who shares a recent paternal ancestor. Because repeat counts change every few generations, they separate closely related men who still share the same Y haplogroup.\n\n" +
      "Short reads can’t measure some long repeats, so a few markers may be left blank, and labs count some markers differently: check before comparing with a lab’s Y-STR test. Males only.",
    accepts: ["reads"], file: "reginald", button: "Count the markers",
    fields: [f.param("panel", "Panel", "Y-111 includes the smaller FamilyTreeDNA panels", {
      as: "select", width: 240, labels: { "Y-111": "Y-111 (FamilyTreeDNA)", PPY23: "PowerPlex Y23", YfilerPlus: "Yfiler Plus", all: "Every marker" },
    })],
    moreTitle: "Thresholds",
    more: [
      f.param("min_reads", "Spanning reads", "Reads that must cross the whole repeat", { width: 80 }),
      f.param("min_fraction", "Agreement", "Share of those reads that must agree", { width: 80 }),
      f.param("min_mapq", "Mapping quality", "For single-copy markers", { width: 80 }),
    ],
    run: function (c) {
      var noY = WGSM.v2extract.noY(c);
      return {
        hours: null, timeText: "minutes", makes: "The markers appear on " + c.name + "’s ancestry page, ready to compare.",
        notes: noY ? [{ tone: "info", icon: "user", id: "no-y", title: "No Y chromosome", text: noY }] : [],
        blocked: noY ? "No Y chromosome in this genome." : null,
      };
    },
  });

  T.define({
    slug: "ancestry-proportions", label: "Ancestry proportions", ops: ["qc/ancestry"], group: "Lineage", keywords: "admixture k7 k12 hgdp populations",
    lesson: { href: "learn-ancestry.html#autosomal", label: "Ancestry estimates & their limits" },
    desc: "Compares the genome with 1000 Genomes and HGDP reference samples and gives the share of 7 continental and 12 regional components, with 95% intervals.",
    learn: "Estimates broad ancestral proportions locally on your computer without sending data to a testing company. Each component is a pattern shared by groups of reference people, named after where they were sampled.\n\n" +
      "Treat these shares as statistical summaries of shared variation, since they do not name an ethnicity or reconstruct a family tree: results shift with the reference panel and component count, and one grandparent’s ancestry can be hard to see. The 95% intervals show how steady each share is. For single lines of descent, see the haplogroups; for relatives, use DNA-test files on matching sites.",
    accepts: ["reads", "vcf"], file: "reginald", button: "Work it out",
    fields: [f.param("bootstrap", "Repeats for the intervals", "More repeats give steadier 95% intervals; 0 skips them", { width: 90 })],
    more: [f.param("build", "Build", build, Object.assign({ width: 200, as: "select" }, BUILDS))],
    run: function (c) {
      return {
        hours: null, timeText: "minutes", makes: "The result appears on " + c.name + "’s page.",
        notes: c.fileKey === "reginald" ? [done(c, "European 52%, Middle Eastern 38%", "And 3 smaller components, today.")] : [],
      };
    },
  });
})();
