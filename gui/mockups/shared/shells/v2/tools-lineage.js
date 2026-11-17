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
    slug: "y-haplogroup", label: "Y haplogroup", ops: ["lineage/y-haplogroup"], group: "Lineage", keywords: "yleaf yfull ftdna isogg paternal",
    lesson: { href: "learn-ancestry.html#lineage", label: "Haplogroups & direct lineages" },
    desc: "Places the paternal line on the Y-DNA tree from the SNPs on the Y chromosome.",
    learn: "Use it to learn your direct paternal line, the one that usually follows the surname: which branch of the human Y tree you belong to, and how far down it you can be placed. A whole genome reads far more Y SNPs than a DNA-test kit, so the branch is usually much more specific.\n\n" +
      "It applies only to males. Compare your haplogroup with surname and haplogroup projects; Y-STR markers then tell apart close relatives within a branch, and the Y chromosome extract lets YFull or FamilyTreeDNA place you on their latest trees.",
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
    desc: "Places the maternal line on the mtDNA tree with HaploGrep 3.",
    learn: "Use it to learn your direct maternal line: your mother’s, her mother’s and so on back. Everyone has one, men included, though only women pass it on.\n\n" +
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
    slug: "y-str", label: "Y-STR markers", ops: ["lineage/y-str"], group: "Lineage", keywords: "ftdna yseq y-111 surname",
    lesson: { href: "learn-ancestry.html#lineage", label: "Y-STRs & paternal comparisons" },
    desc: "Counts repeats at Y-STR markers, the numbers FamilyTreeDNA and YSEQ compare between men who share a surname.",
    learn: "Use it if you’re in, or want to join, a surname project: members compare counts at 37, 67 or 111 markers to see who shares a recent paternal ancestor. The counts change quickly, so they separate men only a few generations apart, which the Y haplogroup can’t.\n\n" +
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
    desc: "Compares the genome with 80 reference populations and gives the share of each component, at continental and regional level, with 95% intervals.",
    learn: "Use it for a broad picture of where your ancestors’ populations lived, worked out on your own computer rather than by a testing company. Each component is a pattern shared by groups of reference people, named after where they were sampled.\n\n" +
      "It’s a model, not an ethnicity or a family tree: results shift with the reference panel and the number of components, and one grandparent’s ancestry can be hard to see. The intervals show how sure each share is. For single lines of descent, see the haplogroups; for relatives, use DNA-test files on matching sites.",
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
